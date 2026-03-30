"""TTSProvider abstract base class defining the TTS backend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class TTSProvider(ABC):
    """Base class for TTS backends. All output is PCM int16 at 24000 Hz mono."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Output sample rate in Hz."""
        ...

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text into complete PCM audio bytes (int16 LE mono)."""
        ...

    @abstractmethod
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM chunks as they become available."""
        ...
        yield b""  # pragma: no cover — required for async generator typing

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel in-flight synthesis. Granularity is provider-specific."""
        ...

    @abstractmethod
    async def aclose(self) -> None:
        """Release resources (HTTP clients, model refs)."""
        ...
