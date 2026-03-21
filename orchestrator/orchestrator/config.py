"""Configuration management via environment variables and .env files."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

# Find .env from project root regardless of CWD
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = {
        "env_file": str(_ENV_FILE) if _ENV_FILE.exists() else ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # ignore env vars we don't map
    }

    # --- API keys -----------------------------------------------------------
    anthropic_api_key: str = Field(description="Anthropic API key for Claude")
    elevenlabs_api_key: str = Field(default="", description="ElevenLabs API key for TTS")

    # --- Claude --------------------------------------------------------------
    claude_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude model identifier",
    )

    # --- SimConnect bridge ---------------------------------------------------
    simconnect_ws_host: str = Field(
        default="localhost",
        description="WebSocket host for the SimConnect bridge",
    )
    simconnect_ws_port: int = Field(
        default=8080,
        description="WebSocket port for the SimConnect bridge",
    )
    simconnect_bridge_url: str = Field(
        default="",
        description="Full WebSocket URL (constructed from host+port if empty)",
    )

    # --- Whisper STT ---------------------------------------------------------
    whisper_model: str = Field(
        default="base.en",
        description="Whisper model size (used by Docker service, not locally)",
    )
    whisper_url: str = Field(
        default="http://localhost:9090",
        description="URL of the local Whisper ASR HTTP service",
    )

    # --- ElevenLabs TTS ------------------------------------------------------
    elevenlabs_voice_id: str = Field(
        default="",
        description="ElevenLabs voice ID for TTS output",
    )

    # --- Screen capture ------------------------------------------------------
    screen_capture_enabled: bool = Field(
        default=False,
        description="Enable screen capture for vision-based analysis",
    )
    screen_capture_fps: int = Field(
        default=1,
        description="Frames per second for screen capture",
    )

    # --- ChromaDB (context store) --------------------------------------------
    chromadb_url: str = Field(
        default="http://localhost:8000",
        description="URL of the ChromaDB HTTP server (Docker)",
    )

    # --- Logging -------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Log level: DEBUG, INFO, WARNING, ERROR",
    )

    @model_validator(mode="after")
    def _build_derived(self) -> "Settings":
        # Construct the bridge URL from host + port when not explicitly set
        if not self.simconnect_bridge_url:
            self.simconnect_bridge_url = (
                f"ws://{self.simconnect_ws_host}:{self.simconnect_ws_port}"
            )
        # Alias: accept voice_id from env as ELEVENLABS_VOICE_ID
        return self

    @property
    def voice_id(self) -> str:
        """Convenience alias so callers can use settings.voice_id."""
        return self.elevenlabs_voice_id


def load_settings() -> Settings:
    return Settings()
