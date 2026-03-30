"""Kokoro TTS backend implementing the TTSProvider ABC.

Wraps kokoro-onnx for local, free TTS inference via ONNX runtime.
Audio is generated as float32 numpy arrays and converted to
PCM int16 LE mono at 24000 Hz.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np

from .base import TTSProvider

logger = logging.getLogger(__name__)

try:
    from kokoro_onnx import Kokoro
except ImportError:
    Kokoro = None  # type: ignore[assignment,misc]


class KokoroTTS(TTSProvider):
    """Local Kokoro TTS backend via kokoro-onnx.

    Produces PCM int16 LE mono audio at 24000 Hz.
    Uses ONNX runtime for inference -- no GPU or API key required.
    """

    def __init__(
        self,
        model_path: str,
        voices_path: str,
        voice: str = "af_heart",
    ) -> None:
        if Kokoro is None:
            raise ImportError(
                "kokoro-onnx is required for Kokoro TTS. Install with: pip install kokoro-onnx"
            )

        model_file = Path(model_path)
        if not model_file.is_file():
            raise FileNotFoundError(
                f"Kokoro model file not found: {model_path}. "
                "Download from https://github.com/thewh1teagle/kokoro-onnx/releases"
            )

        voices_file = Path(voices_path)
        if not voices_file.is_file():
            raise FileNotFoundError(
                f"Kokoro voices file not found: {voices_path}. "
                "Download from https://github.com/thewh1teagle/kokoro-onnx/releases"
            )

        self._voice = voice
        self._cancelled = False
        self._kokoro = Kokoro(model_path, voices_path)

    @property
    def sample_rate(self) -> int:
        """Output sample rate in Hz."""
        return 24000

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text via kokoro-onnx, return PCM int16 bytes.

        Runs inference in a thread to avoid blocking the event loop.
        """
        samples, _sr = await asyncio.to_thread(
            self._kokoro.create, text, voice=self._voice, speed=1.0, lang="en-us"
        )
        return self._float32_to_int16_bytes(samples)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio sentence-by-sentence, yielding PCM int16 chunks.

        Checks cancellation between sentences. Per D-03, Kokoro finishes
        the current sentence before stopping.
        """
        self._cancelled = False

        async for samples, _sr in self._kokoro.create_stream(
            text, voice=self._voice, speed=1.0, lang="en-us"
        ):
            yield self._float32_to_int16_bytes(samples)
            if self._cancelled:
                break

    async def cancel(self) -> None:
        """Cancel streaming after the current sentence completes."""
        self._cancelled = True

    async def aclose(self) -> None:
        """No-op -- Kokoro uses in-process ONNX runtime, no external connections."""
        pass

    @staticmethod
    def _float32_to_int16_bytes(samples: np.ndarray) -> bytes:
        """Convert float32 [-1.0, 1.0] samples to PCM int16 LE bytes."""
        int16_samples = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        return int16_samples.tobytes()
