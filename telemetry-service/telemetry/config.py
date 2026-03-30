"""Telemetry service configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class TelemetryServiceSettings(BaseSettings):
    model_config = {
        "env_prefix": "TELEMETRY_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    host: str = Field(default="0.0.0.0", description="Bind address")
    port: int = Field(default=3839, description="Service port")
    stale_adapter_timeout: float = Field(
        default=15.0,
        description="Seconds before an adapter is considered stale",
    )
    state_path: str = Field(
        default="/data/state.json",
        description="Path for state persistence file",
    )
    state_write_interval: float = Field(
        default=5.0,
        description="Min seconds between state persistence writes",
    )
    log_level: str = Field(default="INFO", description="Log level")


def load_settings() -> TelemetryServiceSettings:
    return TelemetryServiceSettings()
