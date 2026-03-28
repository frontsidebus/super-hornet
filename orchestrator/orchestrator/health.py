"""Health monitoring infrastructure for subsystem tracking.

These classes are game-agnostic and reusable across any orchestrator
configuration.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ConnectionState(StrEnum):
    """Generic connection lifecycle states."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"


class SubsystemHealth(BaseModel):
    """Health status for a single subsystem."""

    name: str
    healthy: bool = False
    last_seen: float = 0.0  # monotonic timestamp
    message: str = ""

    @property
    def age_seconds(self) -> float:
        """Seconds since last successful health check."""
        if self.last_seen == 0.0:
            return float("inf")
        return time.monotonic() - self.last_seen


class HealthMonitor:
    """Tracks health of all subsystems the orchestrator depends on."""

    def __init__(self) -> None:
        self._subsystems: dict[str, SubsystemHealth] = {}

    def register(self, name: str) -> None:
        """Register a subsystem for health tracking."""
        self._subsystems[name] = SubsystemHealth(name=name)

    def update(
        self, name: str, healthy: bool, message: str = ""
    ) -> None:
        """Update the health status of a subsystem."""
        if name not in self._subsystems:
            self.register(name)
        sub = self._subsystems[name]
        sub.healthy = healthy
        sub.message = message
        if healthy:
            sub.last_seen = time.monotonic()

    def get(self, name: str) -> SubsystemHealth | None:
        """Get health status for a named subsystem."""
        return self._subsystems.get(name)

    def all_healthy(self) -> bool:
        """Return True if all registered subsystems are healthy."""
        return all(s.healthy for s in self._subsystems.values())

    def summary(self) -> dict[str, dict[str, Any]]:
        """Return a summary dict suitable for JSON serialization."""
        return {
            name: {
                "healthy": sub.healthy,
                "age_seconds": round(sub.age_seconds, 1),
                "message": sub.message,
            }
            for name, sub in self._subsystems.items()
        }
