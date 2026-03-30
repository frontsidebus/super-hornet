"""FastAPI backend for the Super Hornet AI Wingman web UI.

Bridges the browser frontend to the orchestrator components: game state
telemetry streaming, Claude chat with the Super Hornet persona, Whisper STT,
and TTS via pluggable TTSProvider.

Supports barge-in interruption: if the user sends new audio or text while
Super Hornet is responding, the current Claude stream and TTS pipeline are
cancelled immediately.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.audio_processing import (
    SC_VOCABULARY_PROMPT,
    convert_webm_to_wav_normalized,
)
from orchestrator.claude_client import ClaudeClient
from orchestrator.config import load_settings
from orchestrator.context_store import ContextStore
from orchestrator.game_activity import GameActivityDetector
from orchestrator.game_client import GameStateClient
from orchestrator.game_state import GameActivity, GameState
from orchestrator.log_parser import LogParserModule
from orchestrator.screen_capture import CaptureManager
from orchestrator.skill_library import SkillLibrary
from orchestrator.tts import TTSProvider, create_tts_provider
from orchestrator.tts_preprocessor import preprocess_for_tts
from orchestrator.uex_client import UEXClient
from orchestrator.vision import VisionModule, load_roi_definitions

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("hornet.web")

# ---------------------------------------------------------------------------
# Shared application state (initialised in lifespan)
# ---------------------------------------------------------------------------
settings = load_settings()
logging.getLogger().setLevel(
    getattr(logging, settings.log_level.upper(), logging.INFO)
)

game_client: GameStateClient | None = None
claude_client: ClaudeClient | None = None
context_store: ContextStore | None = None
activity_detector: GameActivityDetector | None = None
uex_client: UEXClient | None = None
skill_library: SkillLibrary | None = None
vision_module: VisionModule | None = None
capture_manager: CaptureManager | None = None

# Track whether we have a live connection to the game state pipeline
_game_connected: bool = False

# Confidence threshold: transcriptions below this trigger a retry or warning
_LOW_CONFIDENCE_THRESHOLD = 0.4

# Brief pause (seconds) after Super Hornet finishes speaking before accepting input
_POST_SPEECH_PAUSE_SECS = 0.3


# ---------------------------------------------------------------------------
# Persistent HTTP clients and TTS provider
# ---------------------------------------------------------------------------

# TTS provider instance (created in lifespan)
_tts_provider: TTSProvider | None = None

# Shared httpx client for Whisper (connection pooling)
_whisper_client: httpx.AsyncClient | None = None


async def _get_whisper_client() -> httpx.AsyncClient:
    global _whisper_client
    if _whisper_client is None or _whisper_client.is_closed:
        _whisper_client = httpx.AsyncClient(timeout=30.0)
    return _whisper_client


# ---------------------------------------------------------------------------
# TTS phrase cache -- pre-populated at startup for common Super Hornet phrases
# ---------------------------------------------------------------------------

_TTS_CACHE: dict[str, bytes] = {}
_CACHEABLE_PHRASES = [
    "Copy that, Commander.",
    "Understood.",
    "Scanning.",
    "Quantum route calculated.",
    "Hostile contact.",
    "Shields holding.",
    "Ready when you are.",
    "Negative.",
    "Standby.",
    "Checking.",
    "Roger.",
]


async def _prepopulate_tts_cache() -> None:
    """Pre-generate TTS audio (PCM) for common short phrases at startup."""
    if _tts_provider is None:
        return

    for phrase in _CACHEABLE_PHRASES:
        sanitized = preprocess_for_tts(phrase)
        if not sanitized or sanitized in _TTS_CACHE:
            continue
        try:
            pcm_bytes = await _tts_provider.synthesize(sanitized)
            if pcm_bytes:
                _TTS_CACHE[sanitized] = pcm_bytes
                logger.info(
                    "Cached TTS phrase: '%s' (%d bytes PCM)", sanitized, len(pcm_bytes)
                )
        except Exception as exc:
            logger.debug("Failed to cache TTS phrase '%s': %s", sanitized, exc)


# ---------------------------------------------------------------------------
# Lifespan -- start / stop background services
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global game_client, claude_client, context_store, activity_detector
    global uex_client, skill_library, vision_module, capture_manager
    global _game_connected, _tts_provider

    logger.info("Starting Super Hornet web server")

    # Context store (ChromaDB) -- degrades gracefully if unavailable
    context_store = ContextStore(chromadb_url=settings.chromadb_url)

    # UEX Corp client for trade / economy data
    uex_client = UEXClient(
        base_url=settings.uex_api_base_url,
        api_key=settings.uex_api_key,
    )

    # Skill library (ChromaDB-backed)
    skill_library = SkillLibrary(chromadb_url=settings.chromadb_url)

    # Vision module (screen capture + Claude Vision analysis)
    if settings.vision_enabled:
        vision_module = VisionModule(
            anthropic_api_key=settings.anthropic_api_key,
        )
        logger.info("Vision module created (enabled)")
    else:
        vision_module = None
        logger.info("Vision module disabled")

    # Capture manager for on-demand screen grabs
    capture_manager = CaptureManager(
        fps=1, enabled=settings.vision_enabled,
    )

    # Game state client (log parser + vision)
    log_parser = (
        LogParserModule(settings.sc_game_log_path)
        if settings.sc_game_log_path
        else None
    )
    game_client = GameStateClient(
        log_parser=log_parser,
        vision_module=vision_module,
    )
    try:
        await game_client.connect()
        _game_connected = True
        logger.info("Game state client connected")
    except Exception as exc:
        _game_connected = False
        logger.warning(
            "Game state client failed to start (%s); telemetry will be offline",
            exc,
        )

    # Activity detector
    activity_detector = GameActivityDetector()

    # Register the activity detector as a subscriber when connected
    if _game_connected and game_client is not None:

        async def _on_state(state: GameState) -> None:
            assert activity_detector is not None
            detected = activity_detector.update(state)
            state.activity = detected

        game_client.subscribe(_on_state)

    # Claude client
    claude_client = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        game_client=game_client,
        context_store=context_store,
        uex_client=uex_client,
        skill_library=skill_library,
    )

    # TTS provider (pluggable: ElevenLabs or Kokoro based on TTS_PROVIDER env)
    try:
        _tts_provider = create_tts_provider(settings)
        logger.info("TTS provider created: %s", type(_tts_provider).__name__)
    except (ValueError, FileNotFoundError) as exc:
        logger.warning("TTS provider init failed: %s", exc)
        _tts_provider = None

    # Pre-populate TTS cache in the background (non-blocking)
    _cache_task = asyncio.create_task(_prepopulate_tts_cache())
    _cache_task.add_done_callback(
        lambda t: logger.error("TTS cache prepopulation failed: %s", t.exception())
        if t.exception()
        else None
    )

    logger.info("Super Hornet web server ready on port 3838")
    yield

    # Shutdown
    logger.info("Shutting down Super Hornet web server")
    if game_client is not None:
        await game_client.disconnect()

    # Close UEX client
    if uex_client is not None:
        await uex_client.close()

    # Close TTS provider
    if _tts_provider is not None:
        await _tts_provider.aclose()

    # Close persistent HTTP clients
    if _whisper_client and not _whisper_client.is_closed:
        await _whisper_client.aclose()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Super Hornet AI Wingman",
    description="Web backend for the Super Hornet Star Citizen AI wingman",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TTSRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    """Serve the frontend."""
    index_path = _STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Super Hornet web UI -- place index.html in web/static/"}


@app.get("/api/status")
async def get_status():
    """Return health status of all subsystems."""
    whisper_ok = False
    try:
        client = await _get_whisper_client()
        resp = await client.get(f"{settings.whisper_url}/health")
        whisper_ok = resp.status_code < 500
    except Exception:
        pass

    chromadb_ok = context_store.available if context_store else False

    return {
        "game_connected": (
            _game_connected
            and game_client is not None
            and game_client.connection_state.value == "CONNECTED"
        ),
        "game_log_path": settings.sc_game_log_path,
        "vision_enabled": settings.vision_enabled,
        "input_simulation_enabled": settings.input_simulation_enabled,
        "chromadb_available": chromadb_ok,
        "chromadb_documents": (
            await context_store.document_count() if context_store else 0
        ),
        "whisper_available": whisper_ok,
        "tts_configured": _tts_provider is not None,
        "claude_model": settings.claude_model,
    }


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile):
    """Transcribe uploaded audio via the Whisper Docker service.

    Accepts webm or wav from the browser MediaRecorder. Sends webm directly
    to Whisper (which handles decoding natively via encode=true) and falls
    back to ffmpeg conversion only if direct transcription fails.
    Returns text and confidence score.
    """
    audio_bytes = await file.read()
    content_type = file.content_type or ""
    filename = file.filename or "audio.webm"

    is_webm = "webm" in content_type or filename.endswith(".webm")

    if is_webm:
        # Try sending webm directly (Whisper accepts it with encode=true)
        text, confidence = await _transcribe_with_confidence(
            audio_bytes, filename="audio.webm", mime_type="audio/webm"
        )
        # Fallback: convert to wav if direct approach fails
        if not text and confidence == 0.0:
            logger.info("Direct webm transcription failed, falling back to ffmpeg")
            audio_bytes = await convert_webm_to_wav_normalized(audio_bytes)
            text, confidence = await _transcribe_with_confidence(audio_bytes)
    else:
        text, confidence = await _transcribe_with_confidence(audio_bytes)

    result: dict[str, Any] = {"text": text, "confidence": confidence}

    # If confidence is low, warn the caller
    if text and confidence < _LOW_CONFIDENCE_THRESHOLD:
        result["low_confidence"] = True
        logger.warning(
            "Low confidence transcription (%.2f): '%s'",
            confidence,
            text[:80],
        )

    return result


@app.post("/api/tts")
async def text_to_speech(request: TTSRequest):
    """Convert text to speech via TTSProvider and return PCM audio."""
    if _tts_provider is None:
        return Response(
            content=json.dumps({"error": "TTS not configured"}),
            status_code=503,
            media_type="application/json",
        )

    # Check TTS cache first
    clean = preprocess_for_tts(request.text)
    if clean in _TTS_CACHE:
        return Response(
            content=_TTS_CACHE[clean],
            media_type="application/octet-stream",
            headers={
                "X-Sample-Rate": str(_tts_provider.sample_rate),
                "X-Audio-Format": "pcm_s16le",
            },
        )

    try:
        pcm_bytes = await _tts_provider.synthesize(clean)
        return Response(
            content=pcm_bytes,
            media_type="application/octet-stream",
            headers={
                "X-Sample-Rate": str(_tts_provider.sample_rate),
                "X-Audio-Format": "pcm_s16le",
            },
        )
    except Exception as exc:
        logger.error("TTS failed: %s", exc)
        return Response(
            content=json.dumps({"error": f"TTS failed: {exc}"}),
            status_code=502,
            media_type="application/json",
        )


# ---------------------------------------------------------------------------
# Vision endpoints
# ---------------------------------------------------------------------------


@app.post("/api/vision/analyze")
async def vision_analyze():
    """Capture the screen and analyse it via Claude Vision."""
    if vision_module is None:
        return Response(
            content=json.dumps({"error": "Vision not enabled"}),
            status_code=503,
            media_type="application/json",
        )

    frame_b64 = await vision_module.capture_full_frame()
    if not frame_b64:
        return Response(
            content=json.dumps({"error": "Screen capture failed"}),
            status_code=500,
            media_type="application/json",
        )

    prompt = (
        "Analyze this Star Citizen HUD screenshot. Describe: "
        "ship status, shields, fuel, location, threats, "
        "notable elements."
    )
    analysis = await vision_module.analyze_frame(frame_b64, prompt)
    return {
        "analysis": analysis,
        "timestamp": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
    }


@app.get("/api/vision/capture")
async def vision_capture():
    """Capture the current screen and return as JPEG (debug)."""
    if vision_module is None and capture_manager is None:
        return Response(
            content=json.dumps({"error": "Vision not enabled"}),
            status_code=503,
            media_type="application/json",
        )

    frame_b64: str | None = None
    if vision_module is not None:
        frame_b64 = await vision_module.capture_full_frame()
    elif capture_manager is not None:
        frame_b64 = await capture_manager.capture_once()

    if not frame_b64:
        return Response(
            content=json.dumps({"error": "Screen capture failed"}),
            status_code=500,
            media_type="application/json",
        )

    jpeg_bytes = base64.b64decode(frame_b64)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# WebSocket: /ws/telemetry
# ---------------------------------------------------------------------------


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """Stream game state telemetry to the browser.

    Subscribes to GameStateClient updates and forwards GameState snapshots
    as JSON. Enriches each snapshot with the current detected activity.
    """
    await ws.accept()
    logger.info("Telemetry WebSocket client connected")

    try:
        if not _game_connected or game_client is None:
            # Game client not connected -- send empty state and wait
            while True:
                await ws.send_json({
                    "type": "telemetry",
                    "connected": False,
                    "data": None,
                })
                await asyncio.sleep(3.0)
        else:
            # Stream game state updates
            state_queue: asyncio.Queue[GameState] = asyncio.Queue(maxsize=10)

            async def _on_state_update(state: GameState) -> None:
                try:
                    state_queue.put_nowait(state)
                except asyncio.QueueFull:
                    # Drop oldest if browser can't keep up
                    try:
                        state_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    state_queue.put_nowait(state)

            game_client.subscribe(_on_state_update)

            # Send initial state immediately
            current = game_client.state
            if activity_detector:
                current.activity = activity_detector.update(current)
            await ws.send_json({
                "type": "telemetry",
                "connected": True,
                "data": {
                    "activity": current.activity.value,
                    "ship": current.ship.model_dump(),
                    "player": current.player.model_dump(),
                    "combat": current.combat.model_dump(),
                },
            })

            while True:
                try:
                    state = await asyncio.wait_for(
                        state_queue.get(), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # Send heartbeat with current state
                    state = game_client.state

                if activity_detector:
                    state.activity = activity_detector.update(state)

                await ws.send_json({
                    "type": "telemetry",
                    "connected": True,
                    "data": {
                        "activity": state.activity.value,
                        "ship": state.ship.model_dump(),
                        "player": state.player.model_dump(),
                        "combat": state.combat.model_dump(),
                    },
                })

    except WebSocketDisconnect:
        logger.info("Telemetry WebSocket client disconnected")
    except Exception as exc:
        logger.warning("Telemetry WebSocket error: %s", exc)


# ---------------------------------------------------------------------------
# WebSocket: /ws/chat  (with barge-in / interruption support)
# ---------------------------------------------------------------------------


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """Chat with Super Hornet, with barge-in interruption support.

    Receives JSON messages or binary audio data.

    Text messages:
      {"type": "audio_start", "mime": "audio/webm"}  -- next binary = audio
      {"text": "user message"}                         -- direct text input
      {"type": "interrupt"}                            -- cancel current response

    Binary messages:
      Raw audio bytes (preceded by audio_start marker)

    Streams response as:
      {"type": "text", "content": "..."}      -- streamed text chunks
      {"type": "transcription", "text": "...", "confidence": 0.85}
      {"type": "tts_audio", "size": N}        -- followed by binary MP3
      {"type": "interrupted"}                 -- response was cancelled
      {"type": "done"}                        -- end of response
      {"type": "listening"}                   -- Super Hornet is ready for input
    """
    await ws.accept()
    logger.info("Chat WebSocket client connected")

    pending_audio_mime: str | None = None

    # Active response task -- cancelled on barge-in
    active_response_task: asyncio.Task[None] | None = None
    # Event signalled when the user interrupts
    interrupt_event = asyncio.Event()

    async def _cancel_active_response() -> None:
        """Cancel any in-progress Claude stream and TTS pipeline."""
        nonlocal active_response_task
        if active_response_task and not active_response_task.done():
            interrupt_event.set()
            active_response_task.cancel()
            try:
                await active_response_task
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("Active response cancelled (barge-in)")
            try:
                await ws.send_json({"type": "interrupted"})
            except Exception:
                pass
        active_response_task = None

    try:
        while True:
            message = await ws.receive()

            # Handle binary audio data from the browser's MediaRecorder
            if "bytes" in message and message["bytes"]:
                audio_bytes = message["bytes"]
                logger.info(
                    "Received %d bytes of audio (mime: %s)",
                    len(audio_bytes),
                    pending_audio_mime,
                )

                # Barge-in: cancel current response if one is active
                await _cancel_active_response()

                user_text, confidence = (
                    await _transcribe_audio_bytes_with_confidence(
                        audio_bytes,
                        pending_audio_mime or "audio/webm",
                    )
                )
                pending_audio_mime = None

                if not user_text:
                    await ws.send_json({
                        "type": "error",
                        "content": "Could not transcribe audio",
                    })
                    continue

                await ws.send_json({
                    "type": "transcription",
                    "text": user_text,
                    "confidence": round(confidence, 2),
                })

                # If confidence is very low, retry once with the raw audio
                if confidence < _LOW_CONFIDENCE_THRESHOLD and user_text:
                    logger.warning(
                        "Low confidence (%.2f), sending anyway: '%s'",
                        confidence,
                        user_text[:60],
                    )

            elif "text" in message and message["text"]:
                raw = message["text"]
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({
                        "type": "error",
                        "content": "Invalid JSON",
                    })
                    continue

                # Handle audio_start marker (next message will be binary)
                if msg.get("type") == "audio_start":
                    pending_audio_mime = msg.get("mime", "audio/webm")
                    # Barge-in: if Super Hornet is speaking and user starts recording
                    await _cancel_active_response()
                    continue

                # Handle explicit interrupt request
                if msg.get("type") == "interrupt":
                    await _cancel_active_response()
                    continue

                user_text = msg.get("text", "")
                if not user_text:
                    await ws.send_json({
                        "type": "error",
                        "content": "No text provided",
                    })
                    continue

                # Barge-in: cancel if user sends text while Super Hornet responding
                await _cancel_active_response()

            else:
                continue

            # Detect scan / vision trigger commands
            scan_image_b64: str | None = None
            lower_text = user_text.lower().strip()
            is_scan = (
                lower_text.startswith("/scan")
                or "what do you see" in lower_text
                or "look at my screen" in lower_text
            )
            if is_scan and vision_module is not None:
                scan_image_b64 = await vision_module.capture_full_frame()
                if not scan_image_b64:
                    logger.warning("Scan requested but capture failed")

            # Reset interrupt event for the new response
            interrupt_event.clear()

            # Launch response streaming as a cancellable task
            active_response_task = asyncio.create_task(
                _stream_response(
                    ws, user_text, interrupt_event,
                    image_base64=scan_image_b64,
                )
            )

    except WebSocketDisconnect:
        logger.info("Chat WebSocket client disconnected")
        await _cancel_active_response()
    except Exception as exc:
        logger.warning("Chat WebSocket error: %s", exc)
        await _cancel_active_response()


# ---------------------------------------------------------------------------
# TTSProvider streaming to browser
# ---------------------------------------------------------------------------


async def _tts_stream_to_browser(
    ws: WebSocket,
    tts_queue: asyncio.Queue[str | None],
    interrupt: asyncio.Event,
) -> None:
    """Drain sentences from tts_queue, synthesize via TTSProvider, send PCM to browser.

    For each sentence: preprocess, check cache, then stream PCM chunks to the
    browser with format metadata so the browser can play via Web Audio API.
    """
    assert _tts_provider is not None
    sample_rate = _tts_provider.sample_rate

    while True:
        if interrupt.is_set():
            break
        try:
            sentence = await asyncio.wait_for(
                tts_queue.get(), timeout=0.1
            )
        except asyncio.TimeoutError:
            continue
        if sentence is None:
            break  # Poison pill -- done
        if interrupt.is_set():
            break

        clean_text = preprocess_for_tts(sentence)
        if not clean_text:
            continue

        # Check cache first (cached data is PCM)
        if clean_text in _TTS_CACHE:
            cached = _TTS_CACHE[clean_text]
            await ws.send_json({
                "type": "tts_audio",
                "format": "pcm_s16le",
                "sample_rate": sample_rate,
                "size": len(cached),
            })
            await ws.send_bytes(cached)
            continue

        # Stream via TTSProvider
        try:
            async for chunk in _tts_provider.synthesize_stream(clean_text):
                if interrupt.is_set():
                    await _tts_provider.cancel()
                    break
                if chunk:
                    await ws.send_json({
                        "type": "tts_audio",
                        "format": "pcm_s16le",
                        "sample_rate": sample_rate,
                        "size": len(chunk),
                    })
                    await ws.send_bytes(chunk)
        except Exception as exc:
            logger.warning("TTS stream failed for '%s': %s", clean_text[:40], exc)


async def _stream_response(
    ws: WebSocket,
    user_text: str,
    interrupt: asyncio.Event,
    image_base64: str | None = None,
) -> None:
    """Stream Claude response with TTS. Cancellable via interrupt event.

    Uses the pluggable TTSProvider for audio synthesis. This runs as a
    task so it can be cancelled when the user barges in.

    If *image_base64* is provided it is forwarded to Claude as a vision
    attachment (e.g. screen-scan feature).
    """
    tts_enabled = _tts_provider is not None
    sentence_buffer = ""
    full_response = ""

    # TTS queue ensures audio chunks are sent in order
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Use TTSProvider streaming sender
    if tts_enabled:
        tts_task: asyncio.Task[None] | None = asyncio.create_task(
            _tts_stream_to_browser(ws, tts_queue, interrupt)
        )
    else:
        tts_task = None

    try:
        assert claude_client is not None
        # Pass current game state so Claude has context
        current_game_state = None
        if _game_connected and game_client is not None:
            current_game_state = game_client.state
            if activity_detector:
                detected = activity_detector.update(current_game_state)
                current_game_state.activity = detected

        async for chunk in claude_client.chat(
            user_text,
            game_state=current_game_state,
            image_base64=image_base64,
        ):
            if interrupt.is_set():
                logger.info("Response interrupted mid-stream")
                break

            full_response += chunk
            await ws.send_json({"type": "text", "content": chunk})

            if tts_enabled:
                sentence_buffer += chunk
                sent, remaining = _split_at_sentence(sentence_buffer)
                if sent:
                    sentence_buffer = remaining
                    await tts_queue.put(sent)

        # Flush remaining text to TTS (if not interrupted)
        if tts_enabled and sentence_buffer.strip() and not interrupt.is_set():
            await tts_queue.put(sentence_buffer.strip())
            sentence_buffer = ""

    except asyncio.CancelledError:
        logger.info("Response task cancelled")
        raise
    except Exception as exc:
        logger.exception("Claude chat error")
        await ws.send_json({
            "type": "error",
            "content": f"Chat error: {exc}",
        })
    finally:
        # Flush any remaining text before sending poison pill
        if tts_task and sentence_buffer.strip() and not interrupt.is_set():
            await tts_queue.put(sentence_buffer.strip())
        # Signal TTS sender to finish
        if tts_task:
            await tts_queue.put(None)
            try:
                await asyncio.wait_for(tts_task, timeout=15.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                tts_task.cancel()

    if not interrupt.is_set():
        await ws.send_json({"type": "done"})

        # Brief pause after Super Hornet finishes before signalling readiness
        await asyncio.sleep(_POST_SPEECH_PAUSE_SECS)
        await ws.send_json({"type": "listening"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_at_sentence(text: str) -> tuple[str, str]:
    """Split text at a natural speech boundary. Returns (complete, remaining).

    Looks for sentence-ending punctuation first. If the buffer is getting long
    (>50 chars) without a sentence break, falls back to splitting at commas,
    semicolons, or colons to keep TTS chunks flowing.
    """
    # First try: sentence-ending punctuation (.!?) followed by space or end
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ".!?" and (
            i + 1 >= len(text) or text[i + 1] in " \n"
        ):
            return text[: i + 1].strip(), text[i + 1 :].lstrip()

    # Fallback for long buffers: split at clause boundaries (, ; :)
    if len(text) > 50:
        for i in range(len(text) - 1, -1, -1):
            if (
                text[i] in ",;:"
                and i + 1 < len(text)
                and text[i + 1] == " "
            ):
                return text[: i + 1].strip(), text[i + 1 :].lstrip()

    # Force-split very long buffers with no punctuation at all
    if len(text) > 200:
        # Split at last space
        last_space = text.rfind(" ", 0, 180)
        if last_space > 0:
            return text[:last_space].strip(), text[last_space:].lstrip()

    return "", text  # No boundary found yet -- keep buffering


async def _transcribe_with_confidence(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    mime_type: str = "audio/wav",
) -> tuple[str, float]:
    """Send audio to Whisper with aviation prompt and return (text, confidence).

    Uses verbose_json output to extract per-segment confidence scoring.
    Uses the shared Whisper client for connection pooling.
    """
    try:
        client = await _get_whisper_client()
        resp = await client.post(
            f"{settings.whisper_url}/v1/audio/transcriptions",
            files={
                "file": (filename, audio_bytes, mime_type),
            },
            data={
                "model": settings.whisper_model,
                "language": "en",
                "response_format": "verbose_json",
                "prompt": SC_VOCABULARY_PROMPT,
            },
        )
        resp.raise_for_status()
        # Whisper may return plain text instead of JSON for some inputs
        try:
            data = resp.json()
        except Exception:
            text = resp.text.strip()
            logger.warning(
                "Whisper returned plain text instead of JSON: %s",
                text[:80],
            )
            return text, 0.5
        text = data.get("text", "").strip()

        # Calculate confidence from segment avg_logprob
        segments = data.get("segments", [])
        if segments:
            logprobs = [s.get("avg_logprob", -1.0) for s in segments]
            avg_logprob = sum(logprobs) / len(logprobs)
            confidence = min(1.0, max(0.0, math.exp(avg_logprob)))
        else:
            confidence = 0.5

        logger.info(
            "Transcribed (confidence=%.2f): %s", confidence, text[:80]
        )
        return text, confidence
    except httpx.HTTPError as exc:
        logger.error("Whisper transcription HTTP error: %s", exc)
        return "", 0.0
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return "", 0.0


async def _transcribe_audio_bytes_with_confidence(
    audio_bytes: bytes,
    mime_type: str = "audio/webm",
) -> tuple[str, float]:
    """Transcribe browser audio with confidence. Sends webm directly to
    Whisper and falls back to ffmpeg conversion if that fails.
    """
    if "webm" in mime_type or "ogg" in mime_type:
        # Try sending webm/ogg directly -- Whisper handles it with encode=true
        text, confidence = await _transcribe_with_confidence(
            audio_bytes, filename="audio.webm", mime_type="audio/webm"
        )
        if text or confidence > 0.0:
            return text, confidence

        # Fallback: convert to wav via ffmpeg
        logger.info(
            "Direct webm transcription failed, falling back to ffmpeg"
        )
        audio_bytes = await convert_webm_to_wav_normalized(audio_bytes)

    return await _transcribe_with_confidence(audio_bytes)
