"""Tests for TTSProvider ABC contract enforcement."""

from __future__ import annotations

import pytest


class TestTTSProviderABC:
    """Verify TTSProvider enforces abstract method implementation."""

    def test_cannot_instantiate_directly(self) -> None:
        """TTSProvider is abstract and cannot be instantiated."""
        from orchestrator.tts.base import TTSProvider

        with pytest.raises(TypeError):
            TTSProvider()  # type: ignore[abstract]

    def test_complete_subclass_can_be_instantiated(self) -> None:
        """A subclass implementing all abstract methods is valid."""
        from collections.abc import AsyncIterator

        from orchestrator.tts.base import TTSProvider

        class CompleteTTS(TTSProvider):
            @property
            def sample_rate(self) -> int:
                return 24000

            async def synthesize(self, text: str) -> bytes:
                return b""

            async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
                yield b""

            async def cancel(self) -> None:
                pass

            async def aclose(self) -> None:
                pass

        provider = CompleteTTS()
        assert provider.sample_rate == 24000

    def test_missing_synthesize_raises_type_error(self) -> None:
        """Subclass missing synthesize() cannot be instantiated."""
        from collections.abc import AsyncIterator

        from orchestrator.tts.base import TTSProvider

        class MissingSynthesize(TTSProvider):
            @property
            def sample_rate(self) -> int:
                return 24000

            async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
                yield b""

            async def cancel(self) -> None:
                pass

            async def aclose(self) -> None:
                pass

        with pytest.raises(TypeError):
            MissingSynthesize()  # type: ignore[abstract]

    def test_missing_synthesize_stream_raises_type_error(self) -> None:
        """Subclass missing synthesize_stream() cannot be instantiated."""
        from orchestrator.tts.base import TTSProvider

        class MissingSynthesizeStream(TTSProvider):
            @property
            def sample_rate(self) -> int:
                return 24000

            async def synthesize(self, text: str) -> bytes:
                return b""

            async def cancel(self) -> None:
                pass

            async def aclose(self) -> None:
                pass

        with pytest.raises(TypeError):
            MissingSynthesizeStream()  # type: ignore[abstract]

    def test_missing_cancel_raises_type_error(self) -> None:
        """Subclass missing cancel() cannot be instantiated."""
        from collections.abc import AsyncIterator

        from orchestrator.tts.base import TTSProvider

        class MissingCancel(TTSProvider):
            @property
            def sample_rate(self) -> int:
                return 24000

            async def synthesize(self, text: str) -> bytes:
                return b""

            async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
                yield b""

            async def aclose(self) -> None:
                pass

        with pytest.raises(TypeError):
            MissingCancel()  # type: ignore[abstract]

    def test_missing_aclose_raises_type_error(self) -> None:
        """Subclass missing aclose() cannot be instantiated."""
        from collections.abc import AsyncIterator

        from orchestrator.tts.base import TTSProvider

        class MissingAclose(TTSProvider):
            @property
            def sample_rate(self) -> int:
                return 24000

            async def synthesize(self, text: str) -> bytes:
                return b""

            async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
                yield b""

            async def cancel(self) -> None:
                pass

        with pytest.raises(TypeError):
            MissingAclose()  # type: ignore[abstract]

    def test_missing_sample_rate_raises_type_error(self) -> None:
        """Subclass missing sample_rate property cannot be instantiated."""
        from collections.abc import AsyncIterator

        from orchestrator.tts.base import TTSProvider

        class MissingSampleRate(TTSProvider):
            async def synthesize(self, text: str) -> bytes:
                return b""

            async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
                yield b""

            async def cancel(self) -> None:
                pass

            async def aclose(self) -> None:
                pass

        with pytest.raises(TypeError):
            MissingSampleRate()  # type: ignore[abstract]

    def test_sample_rate_returns_int(self) -> None:
        """sample_rate property returns an integer."""
        from collections.abc import AsyncIterator

        from orchestrator.tts.base import TTSProvider

        class IntSampleRate(TTSProvider):
            @property
            def sample_rate(self) -> int:
                return 24000

            async def synthesize(self, text: str) -> bytes:
                return b""

            async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
                yield b""

            async def cancel(self) -> None:
                pass

            async def aclose(self) -> None:
                pass

        provider = IntSampleRate()
        assert isinstance(provider.sample_rate, int)
