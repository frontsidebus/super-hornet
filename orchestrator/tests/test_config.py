"""Tests for orchestrator.config — Settings loading and validation."""

from __future__ import annotations

import os

import pytest

from orchestrator.config import Settings, load_settings


class TestSettingsDefaults:
    """Verify default values when only required fields are provided."""

    def test_default_simconnect_url(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SIMCONNECT_BRIDGE_URL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.simconnect_bridge_url == "ws://localhost:8080"

    def test_default_whisper_model(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHISPER_MODEL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.whisper_model == "base"

    def test_default_whisper_url(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHISPER_URL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.whisper_url == "http://localhost:9000"

    def test_default_screen_capture_disabled(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCREEN_CAPTURE_ENABLED", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.screen_capture_enabled is False

    def test_default_screen_capture_fps(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCREEN_CAPTURE_FPS", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.screen_capture_fps == 1

    def test_default_claude_model(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_MODEL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.claude_model == "claude-sonnet-4-20250514"

    def test_default_chromadb_path(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHROMADB_PATH", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.chromadb_path == "./data/chromadb"

    def test_default_elevenlabs_key_empty(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.elevenlabs_api_key == ""

    def test_default_voice_id_empty(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOICE_ID", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.voice_id == ""


class TestSettingsEnvOverrides:
    """Verify that environment variables override defaults."""

    def test_env_overrides_simconnect_url(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.simconnect_bridge_url == "ws://localhost:9999"

    def test_env_overrides_whisper_model(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.whisper_model == "tiny"

    def test_env_overrides_screen_capture_fps(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.screen_capture_fps == 2

    def test_env_overrides_claude_model(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.claude_model == "claude-sonnet-4-20250514"

    def test_env_overrides_chromadb_path(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.chromadb_path == "/tmp/test_chromadb"

    def test_env_overrides_api_key(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.anthropic_api_key == "sk-ant-test-key-000"


class TestSettingsValidation:
    """Test that validation catches problems with required fields."""

    def test_missing_anthropic_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear all potentially set env vars
        for key in ("ANTHROPIC_API_KEY",):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(Exception):
            Settings()

    def test_screen_capture_enabled_parses_true(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCREEN_CAPTURE_ENABLED", "true")
        s = Settings()
        assert s.screen_capture_enabled is True

    def test_screen_capture_enabled_parses_one(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCREEN_CAPTURE_ENABLED", "1")
        s = Settings()
        assert s.screen_capture_enabled is True


class TestLoadSettings:
    """Test the load_settings() convenience function."""

    def test_load_settings_returns_settings_instance(self, mock_env_vars: dict[str, str]) -> None:
        s = load_settings()
        assert isinstance(s, Settings)
        assert s.anthropic_api_key == "sk-ant-test-key-000"
