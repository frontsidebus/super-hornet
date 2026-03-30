"""ElevenLabs TTS backend implementing the TTSProvider ABC.

Wraps the ElevenLabs REST API for batch synthesis and WebSocket API
for streaming synthesis. Audio is decoded from MP3 to PCM int16 LE
mono at 24000 Hz via ffmpeg subprocess.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import httpx
import websockets

from .base import TTSProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"


class ElevenLabsTTS(TTSProvider):
    """ElevenLabs cloud TTS backend.

    Produces PCM int16 LE mono audio at 24000 Hz.
    Supports both batch (REST) and streaming (WebSocket) synthesis.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._cancelled = False
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def sample_rate(self) -> int:
        """Output sample rate in Hz."""
        return 24000

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text via ElevenLabs REST API, return PCM int16 bytes.

        On error, logs a warning and returns empty bytes.
        """
        url = f"{_BASE_URL}/{self._voice_id}"
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.3,
            },
        }

        try:
            resp = await self._client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            mp3_data = resp.content
            logger.info("TTS synthesized %d bytes for: %s", len(mp3_data), text[:60])
            return await self._decode_mp3_to_pcm(mp3_data)
        except (httpx.HTTPError, OSError) as e:
            logger.warning("ElevenLabs synthesis failed: %s", e)
            return b""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio via ElevenLabs WebSocket, yield PCM int16 chunks.

        Opens a WebSocket connection, sends text, receives MP3 chunks,
        and decodes each to PCM int16 via ffmpeg.
        """
        self._cancelled = False
        ws_url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech"
            f"/{self._voice_id}/stream-input"
            f"?model_id={self._model_id}&output_format=mp3_44100_128"
        )

        async with websockets.connect(ws_url) as ws:
            # Send initial config
            await ws.send(
                json.dumps(
                    {
                        "text": " ",
                        "voice_settings": {
                            "stability": 0.5,
                            "similarity_boost": 0.75,
                            "style": 0.3,
                        },
                        "xi_api_key": self._api_key,
                    }
                )
            )

            # Send text and flush
            await ws.send(json.dumps({"text": text}))
            await ws.send(json.dumps({"text": ""}))

            # Receive audio chunks (ElevenLabs sends JSON with base64 audio)
            async for message in ws:
                if self._cancelled:
                    break
                if isinstance(message, str):
                    data = json.loads(message)
                    audio_b64 = data.get("audio")
                    if audio_b64:
                        mp3_chunk = base64.b64decode(audio_b64)
                        pcm = await self._decode_mp3_to_pcm(mp3_chunk)
                        if pcm:
                            yield pcm
                elif isinstance(message, bytes) and len(message) > 0:
                    pcm = await self._decode_mp3_to_pcm(message)
                    if pcm:
                        yield pcm

    async def cancel(self) -> None:
        """Cancel in-flight streaming synthesis."""
        self._cancelled = True

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _decode_mp3_to_pcm(self, mp3_data: bytes) -> bytes:
        """Decode MP3 bytes to PCM int16 LE mono at 24000 Hz via ffmpeg."""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=mp3_data)

        if proc.returncode != 0:
            logger.warning("ffmpeg decode failed: %s", stderr.decode()[:200])
            return b""

        return stdout
