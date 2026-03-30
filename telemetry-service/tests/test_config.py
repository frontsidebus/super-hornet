"""Tests for telemetry service configuration."""

from __future__ import annotations

from telemetry.config import TelemetryServiceSettings


class TestConfig:
    """Tests for TelemetryServiceSettings."""

    def test_default_port(self) -> None:
        """Default port is 3839."""
        settings = TelemetryServiceSettings()
        assert settings.port == 3839

    def test_default_state_path(self) -> None:
        """Default state_path is /data/state.json."""
        settings = TelemetryServiceSettings()
        assert settings.state_path == "/data/state.json"
