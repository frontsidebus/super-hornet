"""Configuration management via environment variables and .env files."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
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
        default="claude-haiku-4-5-20251001",
        description="Claude model identifier (haiku for speed, sonnet for quality)",
    )
    claude_max_tokens: int = Field(
        default=512,
        description="Default max tokens for Claude responses (keep tactical)",
    )
    claude_max_tokens_briefing: int = Field(
        default=2048,
        description="Max tokens for briefings, trade plans, and detailed responses",
    )
    claude_max_history: int = Field(
        default=20,
        description="Max message pairs to retain in conversation history",
    )

    # --- Star Citizen game.log -----------------------------------------------
    sc_game_log_path: str = Field(
        default="",
        description="Path to Star Citizen game.log file",
    )
    sc_install_path: str = Field(
        default="",
        description="Path to Star Citizen installation (for data extraction)",
    )

    # --- UEX Corp API --------------------------------------------------------
    uex_api_base_url: str = Field(
        default="https://uexcorp.space/api/2.0",
        description="UEX Corp API base URL",
    )
    uex_api_key: str = Field(
        default="",
        description="UEX Corp API bearer token (optional, increases rate limit)",
    )

    # --- Vision pipeline -----------------------------------------------------
    vision_enabled: bool = Field(
        default=True,
        description="Enable vision-based HUD reading via screen capture",
    )
    vision_fps: int = Field(
        default=1,
        description="Vision capture frames per second",
    )
    vision_roi_config_path: str = Field(
        default="",
        description="Path to ROI definitions YAML for HUD element regions",
    )

    # --- Input simulation ----------------------------------------------------
    input_simulation_enabled: bool = Field(
        default=False,
        description="Enable keyboard/mouse input simulation (requires user opt-in)",
    )

    # --- Whisper STT ---------------------------------------------------------
    whisper_model: str = Field(
        default="medium",
        description="Whisper model size (used by Docker service, not locally)",
    )
    whisper_url: str = Field(
        default="http://localhost:9090",
        description="URL of the local Whisper ASR HTTP service",
    )

    # --- ElevenLabs TTS ------------------------------------------------------
    elevenlabs_model_id: str = Field(
        default="eleven_flash_v2_5",
        description="ElevenLabs model ID for TTS (flash_v2_5 for low latency)",
    )
    elevenlabs_voice_id: str = Field(
        default="",
        description="ElevenLabs voice ID for TTS output",
    )

    # --- Screen capture (legacy, used by CaptureManager) ---------------------
    screen_capture_enabled: bool = Field(
        default=False,
        description="Enable screen capture for vision-based analysis",
    )
    screen_capture_fps: int = Field(
        default=1,
        description="Frames per second for screen capture",
    )

    # --- ChromaDB (context store + skill library) ----------------------------
    chromadb_url: str = Field(
        default="http://localhost:8000",
        description="URL of the ChromaDB HTTP server (Docker)",
    )
    knowledge_base_collection: str = Field(
        default="hornet_knowledge",
        description="ChromaDB collection name for knowledge base (RAG)",
    )
    skill_library_collection: str = Field(
        default="hornet_skills",
        description="ChromaDB collection name for skill library",
    )

    # --- Logging -------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Log level: DEBUG, INFO, WARNING, ERROR",
    )

    @property
    def voice_id(self) -> str:
        """Convenience alias so callers can use settings.voice_id."""
        return self.elevenlabs_voice_id


def load_settings() -> Settings:
    return Settings()
