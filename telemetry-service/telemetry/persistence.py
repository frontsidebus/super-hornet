"""State persistence for telemetry service.

Saves last-known telemetry state to a JSON file on disk using atomic
writes (tmp + rename) to prevent corruption on crash. Writes are
throttled to at most once per ``write_interval`` seconds.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .schema import TelemetryEnvelope

logger = logging.getLogger(__name__)


class StatePersistence:
    """Persists telemetry state to a JSON file with throttled atomic writes."""

    def __init__(self, path: Path, write_interval: float = 5.0) -> None:
        self._path = path
        self._write_interval = write_interval
        self._last_write: float = 0.0

    async def save(self, envelope: TelemetryEnvelope) -> None:
        """Save envelope to disk if write_interval has elapsed.

        Uses atomic write pattern: write to .tmp then rename to target.
        Includes ``_persisted_at`` timestamp in the JSON payload.
        """
        now = time.monotonic()
        if now - self._last_write < self._write_interval:
            return
        self._last_write = now

        data = envelope.model_dump(mode="json")
        data["_persisted_at"] = time.time()

        tmp = self._path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._path)

        logger.debug("Persisted telemetry state to %s", self._path)

    def load(self) -> TelemetryEnvelope | None:
        """Load persisted state from disk.

        Returns ``None`` if the file does not exist or contains invalid JSON.
        Strips the ``_persisted_at`` metadata key before validation.
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
            data.pop("_persisted_at", None)
            return TelemetryEnvelope.model_validate(data)
        except Exception:
            logger.warning("Failed to load persisted state from %s", self._path, exc_info=True)
            return None
