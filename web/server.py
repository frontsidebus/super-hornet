"""FastAPI backend for the MERLIN AI co-pilot web UI.

Bridges the browser frontend to the orchestrator components: SimConnect
telemetry streaming, Claude chat with the MERLIN persona, Whisper STT,
and ElevenLabs TTS.

Supports barge-in interruption: if the user sends new audio or text while
MERLIN is responding, the current Claude stream and TTS pipeline are
cancelled immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
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
    AVIATION_PROMPT,
    convert_webm_to_wav_normalized,
)
from orchestrator.claude_client import ClaudeClient  # noqa: E402
from orchestrator.config import load_settings  # noqa: E402
from orchestrator.context_store import ContextStore  # noqa: E402
from orchestrator.flight_phase import FlightPhaseDetector  # noqa: E402
from orchestrator.sim_client import SimConnectClient, SimState  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("merlin.web")

# ---------------------------------------------------------------------------
# Shared application state (initialised in lifespan)
# ---------------------------------------------------------------------------
settings = load_settings()
logging.getLogger().setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

sim_client: SimConnectClient | None = None
claude_client: ClaudeClient | None = None
context_store: ContextStore | None = None
phase_detector: FlightPhaseDetector | None = None

# Track whether we have a live connection to the SimConnect bridge
_sim_connected: bool = False

# Confidence threshold: transcriptions below this trigger a retry or warning
_LOW_CONFIDENCE_THRESHOLD = 0.4

# Brief pause (seconds) after MERLIN finishes speaking before accepting input
_POST_SPEECH_PAUSE_SECS = 0.3


# ---------------------------------------------------------------------------
# Lifespan -- start / stop background services
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sim_client, claude_client, context_store, phase_detector, _sim_connected

    logger.info("Starting MERLIN web server")

    # Context store (ChromaDB) -- degrades gracefully if unavailable
    context_store = ContextStore(chromadb_url=settings.chromadb_url)

    # SimConnect client
    sim_client = SimConnectClient(url=settings.simconnect_bridge_url)
    try:
        await sim_client.connect()
        _sim_connected = True
        logger.info("SimConnect bridge connected at %s", settings.simconnect_bridge_url)
    except Exception as exc:
        _sim_connected = False
        logger.warning(
            "SimConnect bridge unavailable at %s (%s); telemetry will be offline",
            settings.simconnect_bridge_url,
            exc,
        )

    # Flight phase detector
    phase_detector = FlightPhaseDetector()

    # Register the phase detector as a subscriber when connected
    if _sim_connected and sim_client is not None:

        async def _on_state(state: SimState) -> None:
            assert phase_detector is not None
            detected = phase_detector.update(state)
            state.flight_phase = detected

        sim_client.subscribe(_on_state)

    # Claude client
    claude_client = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        sim_client=sim_client,
        context_store=context_store,
    )

    logger.info("MERLIN web server ready on port 3838")
    yield

    # Shutdown
    logger.info("Shutting down MERLIN web server")
    if _sim_connected and sim_client is not None:
        await sim_client.disconnect()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MERLIN AI Co-Pilot",
    description="Web backend for the MERLIN flight simulator co-pilot",
    version="1.0.0",
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
    return {"message": "MERLIN web UI -- place index.html in web/static/"}


@app.get("/api/status")
async def get_status():
    """Return health status of all subsystems."""
    whisper_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.whisper_url}/")
            whisper_ok = resp.status_code < 500
    except Exception:
        pass

    chromadb_ok = context_store.available if context_store else False

    return {
        "sim_connected": _sim_connected,
        "chromadb_available": chromadb_ok,
        "chromadb_documents": context_store.document_count if context_store else 0,
        "whisper_available": whisper_ok,
        "elevenlabs_configured": bool(settings.elevenlabs_api_key and settings.voice_id),
        "claude_model": settings.claude_model,
        "simconnect_bridge_url": settings.simconnect_bridge_url,
    }


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile):
    """Transcribe uploaded audio via the Whisper Docker service.

    Accepts webm or wav from the browser MediaRecorder. Converts webm to wav
    via ffmpeg with audio preprocessing before forwarding to Whisper.
    Returns text and confidence score.
    """
    audio_bytes = await file.read()
    content_type = file.content_type or ""
    filename = file.filename or "audio.webm"

    # If the upload is webm, convert to normalized wav with preprocessing
    if "webm" in content_type or filename.endswith(".webm"):
        audio_bytes = await convert_webm_to_wav_normalized(audio_bytes)
        filename = "audio.wav"

    text, confidence = await _transcribe_with_confidence(audio_bytes)

    result: dict[str, Any] = {"text": text, "confidence": confidence}

    # If confidence is low, warn the caller
    if text and confidence < _LOW_CONFIDENCE_THRESHOLD:
        result["low_confidence"] = True
        logger.warning(
            "Low confidence transcription (%.2f): '%s'", confidence, text[:80]
        )

    return result


@app.post("/api/tts")
async def text_to_speech(request: TTSRequest):
    """Convert text to speech via ElevenLabs and return MP3 audio."""
    if not settings.elevenlabs_api_key or not settings.voice_id:
        return Response(
            content=json.dumps({"error": "ElevenLabs not configured"}),
            status_code=503,
            media_type="application/json",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{settings.voice_id}",
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": _sanitize_for_tts(request.text),
                    "model_id": settings.elevenlabs_model_id,
                    "voice_settings": {
                        "stability": 0.75,
                        "similarity_boost": 0.80,
                        "style": 0.15,
                    },
                },
            )
            resp.raise_for_status()
            return Response(content=resp.content, media_type="audio/mpeg")
    except httpx.HTTPError as exc:
        logger.error("ElevenLabs TTS failed: %s", exc)
        return Response(
            content=json.dumps({"error": f"TTS failed: {exc}"}),
            status_code=502,
            media_type="application/json",
        )


# ---------------------------------------------------------------------------
# WebSocket: /ws/telemetry
# ---------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """Stream simulator telemetry to the browser.

    Connects (or reconnects) to the SimConnect bridge on demand and
    proxies telemetry as JSON. Falls back to polling if the bridge
    subscriber model isn't active.
    """
    await ws.accept()
    logger.info("Telemetry WebSocket client connected")

    import websockets as ws_lib

    bridge_url = settings.simconnect_bridge_url

    try:
        while True:
            # Try to connect directly to the SimConnect bridge WebSocket
            try:
                async with ws_lib.connect(bridge_url) as bridge_ws:
                    logger.info(
                        "Telemetry proxy connected to bridge at %s", bridge_url
                    )
                    await ws.send_json(
                        {"type": "telemetry", "connected": True, "data": None}
                    )

                    async for raw_msg in bridge_ws:
                        try:
                            data = json.loads(raw_msg)
                            # Detect flight phase
                            if phase_detector and "position" in data:
                                try:
                                    state = SimState.model_validate(data)
                                    fp = phase_detector.update(state)
                                    data["flight_phase"] = fp.value
                                except Exception:
                                    pass
                            await ws.send_json({
                                "type": "telemetry",
                                "connected": True,
                                "data": data,
                            })
                        except json.JSONDecodeError:
                            pass

            except (ConnectionRefusedError, OSError, Exception) as exc:
                logger.debug("Bridge not available (%s), retrying in 3s", exc)
                await ws.send_json({
                    "type": "telemetry",
                    "connected": False,
                    "flight_phase": "PREFLIGHT",
                    "data": None,
                })
                await asyncio.sleep(3.0)

    except WebSocketDisconnect:
        logger.info("Telemetry WebSocket client disconnected")
    except Exception as exc:
        logger.warning("Telemetry WebSocket error: %s", exc)


# ---------------------------------------------------------------------------
# WebSocket: /ws/chat  (with barge-in / interruption support)
# ---------------------------------------------------------------------------

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """Chat with MERLIN, with barge-in interruption support.

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
      {"type": "listening"}                   -- MERLIN is ready for input
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

                user_text, confidence = await _transcribe_audio_bytes_with_confidence(
                    audio_bytes, pending_audio_mime or "audio/webm"
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
                    await ws.send_json({"type": "error", "content": "Invalid JSON"})
                    continue

                # Handle audio_start marker (next message will be binary)
                if msg.get("type") == "audio_start":
                    pending_audio_mime = msg.get("mime", "audio/webm")
                    # Barge-in: if MERLIN is speaking and user starts recording
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

                # Barge-in: cancel if user sends text while MERLIN is responding
                await _cancel_active_response()

            else:
                continue

            # Reset interrupt event for the new response
            interrupt_event.clear()

            # Launch response streaming as a cancellable task
            active_response_task = asyncio.create_task(
                _stream_response(ws, user_text, interrupt_event)
            )

    except WebSocketDisconnect:
        logger.info("Chat WebSocket client disconnected")
        await _cancel_active_response()
    except Exception as exc:
        logger.warning("Chat WebSocket error: %s", exc)
        await _cancel_active_response()


async def _stream_response(
    ws: WebSocket,
    user_text: str,
    interrupt: asyncio.Event,
) -> None:
    """Stream Claude response with sentence-level TTS. Cancellable via interrupt event.

    This runs as a task so it can be cancelled when the user barges in.
    """
    tts_enabled = bool(settings.elevenlabs_api_key and settings.voice_id)
    sentence_buffer = ""
    full_response = ""

    # TTS queue ensures audio chunks are sent in order
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _tts_sender() -> None:
        """Sequentially synthesize and send TTS for queued sentences."""
        while True:
            if interrupt.is_set():
                break
            try:
                sentence = await asyncio.wait_for(tts_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if sentence is None:
                break  # Poison pill -- done
            if interrupt.is_set():
                break
            await _send_tts_chunk(ws, sentence)

    tts_task = asyncio.create_task(_tts_sender()) if tts_enabled else None

    try:
        assert claude_client is not None
        # Pass current sim state so Claude has telemetry context
        current_sim_state = None
        if _sim_connected and sim_client is not None:
            current_sim_state = sim_client.state
            if phase_detector:
                detected = phase_detector.update(current_sim_state)
                current_sim_state.flight_phase = detected

        async for chunk in claude_client.chat(
            user_text, sim_state=current_sim_state
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
        await ws.send_json({"type": "error", "content": f"Chat error: {exc}"})
    finally:
        # Flush any remaining text before sending poison pill
        if tts_task and sentence_buffer.strip() and not interrupt.is_set():
            await tts_queue.put(sentence_buffer.strip())
        # Signal TTS sender to finish
        if tts_task:
            await tts_queue.put(None)
            try:
                await asyncio.wait_for(tts_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                tts_task.cancel()

    if not interrupt.is_set():
        await ws.send_json({"type": "done"})

        # Brief pause after MERLIN finishes before signalling readiness
        await asyncio.sleep(_POST_SPEECH_PAUSE_SECS)
        await ws.send_json({"type": "listening"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _sanitize_for_tts(text: str) -> str:
    """Strip markdown, special characters, and convert shorthand for clean TTS.

    Converts LLM output into plain speakable text so ElevenLabs doesn't
    try to pronounce asterisks, bullets, dashes, or formatting tokens.
    """
    # --- Markdown removal (order matters) ---

    # Code blocks (``` ... ```) → just the content
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)

    # Inline code `text` → just text
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Markdown links [text](url) → just the link text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Bold+italic ***text*** or ___text___
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text)

    # Bold **text** or __text__
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text)

    # Italic *text* or _text_ (but not mid-word underscores like pre_flight)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)

    # Strikethrough ~~text~~
    text = re.sub(r"~~(.+?)~~", r"\1", text)

    # Headings: ### text → text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)

    # Blockquotes: > text → text
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    # Horizontal rules (---, ***, ___) → pause
    text = re.sub(r"^[-*_]{3,}\s*$", ".", text, flags=re.MULTILINE)

    # Bullet points (-, *, •) at line start → natural pause
    text = re.sub(r"^\s*[-*•]\s+", ". ", text, flags=re.MULTILINE)

    # Numbered lists: 1. or 1) → natural pause
    text = re.sub(r"^\s*\d+[.)]\s+", ". ", text, flags=re.MULTILINE)

    # --- Special character replacement ---

    # Any remaining stray asterisks (not caught by patterns above)
    text = text.replace("*", "")

    # Dashes and hyphens
    text = text.replace("—", ", ")   # em dash
    text = text.replace("–", " to ")  # en dash
    # Leave regular hyphens in compound words (pre-flight, cross-check)

    # Other symbols
    text = text.replace("…", "...")
    text = text.replace("°", " degrees")
    text = text.replace("±", " plus or minus ")
    text = text.replace("&", " and ")
    text = text.replace("|", ", ")
    text = text.replace("~", "approximately ")

    # Slash: preserve in frequencies like 121.7/118.3, expand otherwise
    text = re.sub(r"(\d)\s*/\s*(\d)", r"\1 slash \2", text)
    text = re.sub(r"(?<!\d)/(?!\d)", " ", text)

    # --- Aviation abbreviation expansion ---

    text = re.sub(r"\bft\b", "feet", text)
    text = re.sub(r"\bkts?\b", "knots", text)
    text = re.sub(r"\bfpm\b", "feet per minute", text)
    text = re.sub(r"\bnm\b", "nautical miles", text)
    text = re.sub(r"\bFL(\d+)\b", r"flight level \1", text)
    text = re.sub(
        r"\bRWY\s*(\d+[LRC]?)\b", r"runway \1", text, flags=re.IGNORECASE
    )
    text = re.sub(r"\bHDG\b", "heading", text, flags=re.IGNORECASE)
    text = re.sub(r"\bALT\b", "altitude", text, flags=re.IGNORECASE)
    text = re.sub(r"\bIAS\b", "indicated airspeed", text)
    text = re.sub(r"\bVS\b", "vertical speed", text)
    text = re.sub(r"\bAP\b", "autopilot", text)
    text = re.sub(r"\binHg\b", "inches of mercury", text)

    # --- Whitespace cleanup ---

    text = _MULTI_SPACE_RE.sub(" ", text)
    # Collapse multiple newlines into a sentence break
    text = re.sub(r"\n+", ". ", text)
    # Clean up repeated periods/commas from list conversions
    text = re.sub(r"[.,]{2,}", ".", text)
    text = re.sub(r"\.\s*,", ".", text)

    return text.strip()


def _split_at_sentence(text: str) -> tuple[str, str]:
    """Split text at a natural speech boundary. Returns (complete, remaining).

    Looks for sentence-ending punctuation first. If the buffer is getting long
    (>120 chars) without a sentence break, falls back to splitting at commas,
    semicolons, or colons to keep TTS chunks flowing.
    """
    # First try: sentence-ending punctuation (.!?) followed by space or end
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ".!?" and (
            i + 1 >= len(text) or text[i + 1] in " \n"
        ):
            return text[: i + 1].strip(), text[i + 1 :].lstrip()

    # Fallback for long buffers: split at clause boundaries (, ; :)
    if len(text) > 120:
        for i in range(len(text) - 1, -1, -1):
            if text[i] in ",;:" and i + 1 < len(text) and text[i + 1] == " ":
                return text[: i + 1].strip(), text[i + 1 :].lstrip()

    # Force-split very long buffers with no punctuation at all
    if len(text) > 200:
        # Split at last space
        last_space = text.rfind(" ", 0, 180)
        if last_space > 0:
            return text[:last_space].strip(), text[last_space:].lstrip()

    return "", text  # No boundary found yet — keep buffering


# Shared httpx client for TTS (avoid creating per-request)
_tts_client: httpx.AsyncClient | None = None


async def _get_tts_client() -> httpx.AsyncClient:
    global _tts_client
    if _tts_client is None or _tts_client.is_closed:
        _tts_client = httpx.AsyncClient(timeout=30.0)
    return _tts_client


async def _send_tts_chunk(ws: WebSocket, text: str) -> None:
    """Synthesize a sentence and send the audio back over WebSocket."""
    # Clean markdown/special chars so TTS doesn't read formatting tokens
    clean_text = _sanitize_for_tts(text)
    if not clean_text:
        return

    try:
        client = await _get_tts_client()
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{settings.voice_id}/stream",
            headers={
                "xi-api-key": settings.elevenlabs_api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": clean_text,
                "model_id": settings.elevenlabs_model_id,
                "voice_settings": {
                    "stability": 0.75,
                    "similarity_boost": 0.80,
                    "style": 0.15,
                },
            },
        )
        resp.raise_for_status()
        # Send audio as binary WebSocket frame -- browser will play it
        await ws.send_json({"type": "tts_audio", "size": len(resp.content)})
        await ws.send_bytes(resp.content)
    except Exception as exc:
        logger.warning("TTS chunk failed: %s", exc)


async def _transcribe_with_confidence(
    audio_bytes: bytes,
) -> tuple[str, float]:
    """Send audio to Whisper with aviation prompt and return (text, confidence).

    Uses verbose_json output to extract per-segment confidence scoring.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.whisper_url}/asr",
                files={"audio_file": ("audio.wav", audio_bytes, "audio/wav")},
                params={
                    "encode": "true",
                    "task": "transcribe",
                    "language": "en",
                    "output": "json",
                    "initial_prompt": AVIATION_PROMPT,
                },
            )
            resp.raise_for_status()
            # Whisper may return plain text instead of JSON for some inputs
            try:
                data = resp.json()
            except Exception:
                text = resp.text.strip()
                logger.warning("Whisper returned plain text instead of JSON: %s", text[:80])
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
        logger.error("Whisper transcription failed: %s", exc)
        return "", 0.0


async def _transcribe_audio_bytes_with_confidence(
    audio_bytes: bytes,
    mime_type: str = "audio/webm",
) -> tuple[str, float]:
    """Convert browser audio to preprocessed WAV, transcribe with confidence."""
    if "webm" in mime_type or "ogg" in mime_type:
        audio_bytes = await convert_webm_to_wav_normalized(audio_bytes)

    return await _transcribe_with_confidence(audio_bytes)


async def _transcribe_audio_bytes(
    audio_bytes: bytes,
    mime_type: str = "audio/webm",
) -> str:
    """Convert browser audio to wav and send to Whisper. Legacy wrapper."""
    text, _ = await _transcribe_audio_bytes_with_confidence(audio_bytes, mime_type)
    return text


async def _transcribe_base64_audio(audio_b64: str) -> str:
    """Decode base64 audio and send to Whisper for transcription."""
    import base64

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        logger.warning("Failed to decode base64 audio")
        return ""

    # Assume webm from browser MediaRecorder; convert to normalized wav
    audio_bytes = await convert_webm_to_wav_normalized(audio_bytes)

    text, confidence = await _transcribe_with_confidence(audio_bytes)

    if confidence < _LOW_CONFIDENCE_THRESHOLD:
        logger.warning(
            "Low confidence base64 transcription (%.2f): '%s'",
            confidence,
            text[:60],
        )

    return text
