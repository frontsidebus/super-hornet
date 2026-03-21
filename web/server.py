"""FastAPI backend for the MERLIN AI co-pilot web UI.

Bridges the browser frontend to the orchestrator components: SimConnect
telemetry streaming, Claude chat with the MERLIN persona, Whisper STT,
and ElevenLabs TTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Path setup — make the orchestrator package importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from orchestrator.orchestrator.claude_client import ClaudeClient  # noqa: E402
from orchestrator.orchestrator.config import load_settings  # noqa: E402
from orchestrator.orchestrator.context_store import ContextStore  # noqa: E402
from orchestrator.orchestrator.flight_phase import FlightPhaseDetector  # noqa: E402
from orchestrator.orchestrator.sim_client import SimConnectClient, SimState  # noqa: E402

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


# ---------------------------------------------------------------------------
# Lifespan — start / stop background services
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sim_client, claude_client, context_store, phase_detector, _sim_connected

    logger.info("Starting MERLIN web server")

    # Context store (ChromaDB) — degrades gracefully if unavailable
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

    logger.info("MERLIN web server ready on port 3000")
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
    return {"message": "MERLIN web UI — place index.html in web/static/"}


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
    via ffmpeg before forwarding to Whisper.
    """
    audio_bytes = await file.read()
    content_type = file.content_type or ""
    filename = file.filename or "audio.webm"

    # If the upload is webm, convert to wav with ffmpeg
    if "webm" in content_type or filename.endswith(".webm"):
        audio_bytes = await _convert_webm_to_wav(audio_bytes)
        filename = "audio.wav"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.whisper_url}/asr",
                files={"audio_file": (filename, audio_bytes, "audio/wav")},
                params={
                    "encode": "true",
                    "task": "transcribe",
                    "language": "en",
                    "output": "json",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()
            return {"text": text}
    except httpx.HTTPError as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return {"text": "", "error": f"Transcription failed: {exc}"}


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
                    "text": request.text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
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

    If the SimConnect bridge is connected, registers a subscriber that
    forwards every state update. Otherwise sends periodic disconnected
    status messages so the browser knows the sim is offline.
    """
    await ws.accept()
    logger.info("Telemetry WebSocket client connected")

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)

    async def _on_state(state: SimState) -> None:
        """Push state into the per-client queue, dropping if full."""
        payload = {
            "type": "telemetry",
            "connected": True,
            "flight_phase": state.flight_phase.value,
            "data": json.loads(state.model_dump_json()),
        }
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop oldest to keep up
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    subscribed = False
    if _sim_connected and sim_client is not None:
        sim_client.subscribe(_on_state)
        subscribed = True

    try:
        if subscribed:
            # Stream from the subscriber queue
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=5.0)
                    await ws.send_json(payload)
                except asyncio.TimeoutError:
                    # Send a heartbeat so the browser knows we are alive
                    await ws.send_json({"type": "heartbeat", "connected": _sim_connected})
        else:
            # No sim connection — send periodic disconnected status
            while True:
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
    finally:
        # Remove the subscriber callback to avoid leaking references
        if subscribed and sim_client is not None:
            try:
                sim_client._subscribers.remove(_on_state)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# WebSocket: /ws/chat
# ---------------------------------------------------------------------------

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """Chat with MERLIN.

    Receives JSON: {"text": "...", "audio_base64": "..."}
    If audio_base64 is present, transcribes it first via Whisper.
    Streams response as:
      {"type": "text", "content": "..."}   — streamed chunks
      {"type": "audio_url", "url": "..."}  — TTS URL for the full reply
      {"type": "done"}                     — signals end of response
    """
    await ws.accept()
    logger.info("Chat WebSocket client connected")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "content": "Invalid JSON"})
                continue

            user_text = msg.get("text", "")
            audio_b64 = msg.get("audio_base64", "")

            # Transcribe audio if provided
            if audio_b64:
                user_text = await _transcribe_base64_audio(audio_b64) or user_text

            if not user_text:
                await ws.send_json({"type": "error", "content": "No text or audio provided"})
                continue

            # Send back the resolved user text (useful when it came from audio)
            if audio_b64:
                await ws.send_json({"type": "transcript", "content": user_text})

            # Stream Claude response
            full_response = ""
            try:
                assert claude_client is not None
                async for chunk in claude_client.chat(user_text):
                    full_response += chunk
                    await ws.send_json({"type": "text", "content": chunk})
            except Exception as exc:
                logger.exception("Claude chat error")
                await ws.send_json({"type": "error", "content": f"Chat error: {exc}"})
                continue

            # Provide TTS URL if ElevenLabs is configured and there is text
            if (
                full_response.strip()
                and settings.elevenlabs_api_key
                and settings.voice_id
            ):
                tts_url = "/api/tts"
                await ws.send_json({"type": "audio_url", "url": tts_url, "text": full_response})

            await ws.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info("Chat WebSocket client disconnected")
    except Exception as exc:
        logger.warning("Chat WebSocket error: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _convert_webm_to_wav(webm_bytes: bytes) -> bytes:
    """Convert webm audio to wav using ffmpeg in a subprocess."""
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as src:
        src.write(webm_bytes)
        src_path = src.name

    dst_path = src_path.replace(".webm", ".wav")

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le", dst_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("ffmpeg conversion failed: %s", stderr.decode(errors="replace"))
            return webm_bytes  # fall back to raw bytes

        return Path(dst_path).read_bytes()
    except FileNotFoundError:
        logger.warning("ffmpeg not found; sending raw webm to Whisper")
        return webm_bytes
    finally:
        # Clean up temp files
        for p in (src_path, dst_path):
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


async def _transcribe_base64_audio(audio_b64: str) -> str:
    """Decode base64 audio and send to Whisper for transcription."""
    import base64

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        logger.warning("Failed to decode base64 audio")
        return ""

    # Assume webm from browser MediaRecorder; convert to wav
    audio_bytes = await _convert_webm_to_wav(audio_bytes)

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
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("text", "").strip()
    except Exception as exc:
        logger.error("Whisper transcription (base64) failed: %s", exc)
        return ""
