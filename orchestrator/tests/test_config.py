"""Tests for orchestrator.config — Settings loading and validation."""

from __future__ import annotations

import os

import pytest

from orchestrator.config import Settings, load_settings


class TestSettingsDefaults:
    """Verify default values when only required fields are provided."""

    def test_default_sc_game_log_path_empty(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SC_GAME_LOG_PATH", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.sc_game_log_path == ""

    def test_default_vision_enabled(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISION_ENABLED", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.vision_enabled is True

    def test_default_input_simulation_disabled(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INPUT_SIMULATION_ENABLED", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.input_simulation_enabled is False

    def test_default_knowledge_base_collection(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KNOWLEDGE_BASE_COLLECTION", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.knowledge_base_collection == "hornet_knowledge"

    def test_default_skill_library_collection(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKILL_LIBRARY_COLLECTION", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.skill_library_collection == "hornet_skills"

    def test_default_whisper_model(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHISPER_MODEL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.whisper_model == "medium"

    def test_default_whisper_url(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHISPER_URL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.whisper_url == "http://localhost:9090"

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

    def test_default_chromadb_url(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHROMADB_URL", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.chromadb_url == "http://localhost:8000"

    def test_default_elevenlabs_key_empty(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.elevenlabs_api_key == ""

    def test_default_voice_id_empty(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
        s = Settings(anthropic_api_key="sk-test")
        assert s.voice_id == ""


class TestSettingsEnvOverrides:
    """Verify that environment variables override defaults."""

    def test_env_overrides_sc_game_log_path(self, mock_env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SC_GAME_LOG_PATH", "/tmp/game.log")
        s = Settings()
        assert s.sc_game_log_path == "/tmp/game.log"

    def test_env_overrides_whisper_model(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.whisper_model == "tiny"

    def test_env_overrides_screen_capture_fps(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.screen_capture_fps == 2

    def test_env_overrides_claude_model(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.claude_model == "claude-sonnet-4-20250514"

    def test_env_overrides_chromadb_url(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.chromadb_url == "http://chromadb-test:9999"

    def test_env_overrides_api_key(self, mock_env_vars: dict[str, str]) -> None:
        s = Settings()
        assert s.anthropic_api_key == "sk-ant-test-key-000"


class TestSettingsValidation:
    """Test that validation catches problems with required fields."""

    def test_missing_anthropic_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Prevent loading real .env file
        monkeypatch.setattr("orchestrator.config.Settings.model_config", {
            "env_file": "",
            "env_file_encoding": "utf-8",
            "extra": "ignore",
        })
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
