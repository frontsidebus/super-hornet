"""Tests for VoiceOutput with TTSProvider abstraction.

Verifies that VoiceOutput delegates all TTS work to a TTSProvider instance
and plays returned PCM bytes directly via sounddevice.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from orchestrator.tts.base import TTSProvider
from orchestrator.voice import VoiceOutput

# Create a mock sounddevice module for tests (sounddevice requires audio hardware)
_mock_sd = MagicMock()
_mock_sd.play = MagicMock()
_mock_sd.wait = MagicMock()
_mock_sd.stop = MagicMock()


class MockTTSProvider(TTSProvider):
    """Mock TTS provider returning fixed PCM data for testing."""

    def __init__(self, sample_rate: int = 24000) -> None:
        self._sample_rate = sample_rate
        self._cancelled = False
        self.synthesize_calls: list[str] = []
        self.stream_calls: list[str] = []
        self.cancel_called = False

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize(self, text: str) -> bytes:
        self.synthesize_calls.append(text)
        # Return 100 samples of PCM int16 silence
        return np.zeros(100, dtype=np.int16).tobytes()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        self.stream_calls.append(text)
        self._cancelled = False
        for _ in range(3):
            if self._cancelled:
                break
            yield np.zeros(50, dtype=np.int16).tobytes()

    async def cancel(self) -> None:
        self._cancelled = True
        self.cancel_called = True

    async def aclose(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _mock_sounddevice():
    """Patch sounddevice for all tests (no audio hardware in CI)."""
    _mock_sd.reset_mock()
    with patch.dict(sys.modules, {"sounddevice": _mock_sd}):
        yield _mock_sd


class TestVoiceOutputConstructor:
    """VoiceOutput accepts TTSProvider instance, not raw API keys."""

    def test_accepts_provider(self) -> None:
        provider = MockTTSProvider()
        vo = VoiceOutput(provider)
        assert vo._provider is provider
        assert vo._sample_rate == 24000

    def test_sample_rate_from_provider(self) -> None:
        provider = MockTTSProvider(sample_rate=16000)
        vo = VoiceOutput(provider)
        assert vo._sample_rate == 16000


class TestVoiceOutputSpeak:
    """VoiceOutput.speak() calls provider.synthesize() and plays PCM."""

    @pytest.fixture
    def provider(self) -> MockTTSProvider:
        return MockTTSProvider()

    @pytest.fixture
    def voice_output(self, provider: MockTTSProvider) -> VoiceOutput:
        return VoiceOutput(provider)

    async def test_speak_calls_synthesize(
        self, voice_output: VoiceOutput, provider: MockTTSProvider
    ) -> None:
        await voice_output.speak("Hello commander")
        assert "Hello commander" in provider.synthesize_calls

    async def test_speak_empty_text_is_noop(
        self, voice_output: VoiceOutput, provider: MockTTSProvider
    ) -> None:
        await voice_output.speak("")
        await voice_output.speak("   ")
        assert provider.synthesize_calls == []

    async def test_speak_empty_bytes_no_playback(
        self, _mock_sounddevice: MagicMock
    ) -> None:
        provider = MockTTSProvider()
        provider.synthesize = AsyncMock(return_value=b"")
        vo = VoiceOutput(provider)
        await vo.speak("test")
        _mock_sounddevice.play.assert_not_called()

    async def test_speak_plays_pcm_via_sounddevice(
        self,
        voice_output: VoiceOutput,
        provider: MockTTSProvider,
        _mock_sounddevice: MagicMock,
    ) -> None:
        await voice_output.speak("Copy that")
        _mock_sounddevice.play.assert_called_once()
        _mock_sounddevice.wait.assert_called_once()


class TestVoiceOutputSpeakStreamed:
    """VoiceOutput.speak_streamed() buffers text and calls provider.synthesize_stream()."""

    async def test_streamed_calls_synthesize_stream(self) -> None:
        provider = MockTTSProvider()
        vo = VoiceOutput(provider)

        async def text_gen() -> AsyncIterator[str]:
            yield "Hello. "
            yield "World."

        await vo.speak_streamed(text_gen())
        assert len(provider.stream_calls) > 0

    async def test_streamed_respects_cancellation(self) -> None:
        provider = MockTTSProvider()
        vo = VoiceOutput(provider)

        async def slow_gen() -> AsyncIterator[str]:
            yield "First sentence. "
            vo.cancel()
            # Allow the ensure_future cancel task to execute
            await asyncio.sleep(0)
            yield "Second sentence."

        await vo.speak_streamed(slow_gen())
        # Allow any pending tasks to complete
        await asyncio.sleep(0.05)
        # After cancel, provider.cancel() should have been called
        assert provider.cancel_called


class TestVoiceOutputCancel:
    """VoiceOutput.cancel() calls provider.cancel() and stops sounddevice."""

    async def test_cancel_calls_provider_cancel(self) -> None:
        provider = MockTTSProvider()
        vo = VoiceOutput(provider)
        vo.cancel()
        # Give the async task a chance to run
        await asyncio.sleep(0.05)
        assert provider.cancel_called

    def test_cancel_sets_cancelled_flag(self) -> None:
        provider = MockTTSProvider()
        vo = VoiceOutput(provider)
        vo.cancel()
        assert vo._cancelled


class TestVoiceOutputImports:
    """VoiceOutput does NOT import from tts.elevenlabs or tts.kokoro."""

    def test_no_elevenlabs_import(self) -> None:
        import inspect

        source = inspect.getsource(VoiceOutput)
        assert "elevenlabs" not in source.lower()
        assert "xi-api-key" not in source
        assert "api.elevenlabs.io" not in source

    def test_no_direct_backend_import_in_module(self) -> None:
        import orchestrator.voice as voice_module

        source_file = voice_module.__file__
        assert source_file is not None
        with open(source_file) as f:
            contents = f.read()
        assert "from .tts.elevenlabs" not in contents
        assert "from .tts.kokoro" not in contents
        assert "import elevenlabs" not in contents
