"""Configuration management via environment variables and .env files."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    anthropic_api_key: str = Field(description="Anthropic API key for Claude")
    elevenlabs_api_key: str = Field(default="", description="ElevenLabs API key for TTS")

    simconnect_bridge_url: str = Field(
        default="ws://localhost:8080",
        description="WebSocket URL for the SimConnect bridge",
    )

    whisper_model: str = Field(
        default="base",
        description="Whisper model size for local STT",
    )

    whisper_url: str = Field(
        default="http://localhost:9090",
        description="URL of the local Whisper ASR HTTP service",
    )

    voice_id: str = Field(
        default="",
        description="ElevenLabs voice ID for TTS output",
    )

    screen_capture_enabled: bool = Field(
        default=False,
        description="Enable screen capture for vision-based analysis",
    )

    screen_capture_fps: int = Field(
        default=1,
        description="Frames per second for screen capture",
    )

    claude_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude model identifier",
    )

    chromadb_path: str = Field(
        default="./data/chromadb",
        description="Path to ChromaDB persistent storage",
    )


def load_settings() -> Settings:
    return Settings()
