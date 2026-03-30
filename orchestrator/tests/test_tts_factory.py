"""Tests for TTS factory function and config fields."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest

from orchestrator.tts.base import TTSProvider


class _StubTTS(TTSProvider):
    """Minimal concrete TTSProvider for testing."""

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


def _inject_fake_module(module_name: str, attr_name: str, mock_cls: MagicMock) -> types.ModuleType:
    """Insert a fake module into sys.modules with a given attribute."""
    mod = types.ModuleType(module_name)
    setattr(mod, attr_name, mock_cls)
    sys.modules[module_name] = mod
    return mod


class TestCreateTTSProvider:
    """Factory function dispatches on tts_provider setting."""

    def test_elevenlabs_returns_correct_instance(self) -> None:
        """create_tts_provider with 'elevenlabs' returns ElevenLabsTTS."""
        from orchestrator.tts import create_tts_provider

        settings = MagicMock()
        settings.tts_provider = "elevenlabs"
        settings.elevenlabs_api_key = "test-key"
        settings.elevenlabs_voice_id = "test-voice"
        settings.elevenlabs_model_id = "eleven_flash_v2_5"

        mock_instance = _StubTTS()
        mock_cls = MagicMock(return_value=mock_instance)
        _inject_fake_module("orchestrator.tts.elevenlabs", "ElevenLabsTTS", mock_cls)
        try:
            provider = create_tts_provider(settings)
            assert provider is mock_instance
            mock_cls.assert_called_once_with(
                api_key="test-key",
                voice_id="test-voice",
                model_id="eleven_flash_v2_5",
            )
        finally:
            sys.modules.pop("orchestrator.tts.elevenlabs", None)

    def test_kokoro_returns_correct_instance(self) -> None:
        """create_tts_provider with 'kokoro' returns KokoroTTS."""
        from orchestrator.tts import create_tts_provider

        settings = MagicMock()
        settings.tts_provider = "kokoro"
        settings.kokoro_model_path = "models/kokoro-v1.0.onnx"
        settings.kokoro_voices_path = "models/voices-v1.0.bin"
        settings.kokoro_voice = "af_heart"

        mock_instance = _StubTTS()
        mock_cls = MagicMock(return_value=mock_instance)
        _inject_fake_module("orchestrator.tts.kokoro", "KokoroTTS", mock_cls)
        try:
            provider = create_tts_provider(settings)
            assert provider is mock_instance
            mock_cls.assert_called_once_with(
                model_path="models/kokoro-v1.0.onnx",
                voices_path="models/voices-v1.0.bin",
                voice="af_heart",
            )
        finally:
            sys.modules.pop("orchestrator.tts.kokoro", None)

    def test_invalid_provider_raises_value_error(self) -> None:
        """create_tts_provider with unknown provider raises ValueError."""
        from orchestrator.tts import create_tts_provider

        settings = MagicMock()
        settings.tts_provider = "invalid"

        with pytest.raises(ValueError, match="Unknown TTS provider"):
            create_tts_provider(settings)

    def test_case_insensitive(self) -> None:
        """create_tts_provider is case-insensitive."""
        from orchestrator.tts import create_tts_provider

        settings = MagicMock()
        settings.tts_provider = "ElevenLabs"
        settings.elevenlabs_api_key = "key"
        settings.elevenlabs_voice_id = "vid"
        settings.elevenlabs_model_id = "mid"

        mock_instance = _StubTTS()
        mock_cls = MagicMock(return_value=mock_instance)
        _inject_fake_module("orchestrator.tts.elevenlabs", "ElevenLabsTTS", mock_cls)
        try:
            provider = create_tts_provider(settings)
            assert provider is mock_instance
        finally:
            sys.modules.pop("orchestrator.tts.elevenlabs", None)

    def test_whitespace_stripped(self) -> None:
        """create_tts_provider strips whitespace from provider name."""
        from orchestrator.tts import create_tts_provider

        settings = MagicMock()
        settings.tts_provider = "  kokoro  "
        settings.kokoro_model_path = "m.onnx"
        settings.kokoro_voices_path = "v.bin"
        settings.kokoro_voice = "af_heart"

        mock_instance = _StubTTS()
        mock_cls = MagicMock(return_value=mock_instance)
        _inject_fake_module("orchestrator.tts.kokoro", "KokoroTTS", mock_cls)
        try:
            provider = create_tts_provider(settings)
            assert provider is mock_instance
        finally:
            sys.modules.pop("orchestrator.tts.kokoro", None)


class TestSettingsTTSFields:
    """Settings class has TTS provider config fields."""

    def test_tts_provider_default_elevenlabs(self) -> None:
        """Settings.tts_provider defaults to 'elevenlabs'."""
        from orchestrator.config import Settings

        s = Settings(anthropic_api_key="test")
        assert s.tts_provider == "elevenlabs"

    def test_kokoro_model_path_exists(self) -> None:
        """Settings has kokoro_model_path field."""
        from orchestrator.config import Settings

        s = Settings(anthropic_api_key="test")
        assert hasattr(s, "kokoro_model_path")
        assert isinstance(s.kokoro_model_path, str)

    def test_kokoro_voices_path_exists(self) -> None:
        """Settings has kokoro_voices_path field."""
        from orchestrator.config import Settings

        s = Settings(anthropic_api_key="test")
        assert hasattr(s, "kokoro_voices_path")
        assert isinstance(s.kokoro_voices_path, str)

    def test_kokoro_voice_exists(self) -> None:
        """Settings has kokoro_voice field."""
        from orchestrator.config import Settings

        s = Settings(anthropic_api_key="test")
        assert hasattr(s, "kokoro_voice")
        assert isinstance(s.kokoro_voice, str)
