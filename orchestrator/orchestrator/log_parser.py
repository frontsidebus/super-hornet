"""Real-time Star Citizen game.log parser.

Tails the game.log file asynchronously, parses each new line against
known patterns, and dispatches LogEvent objects to registered callbacks.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from orchestrator.log_patterns import LogEventType, match_line, parse_timestamp

logger = logging.getLogger(__name__)

# How often to poll for new lines (seconds)
_POLL_INTERVAL = 0.25

# Maximum number of recent events to retain
_MAX_RECENT_EVENTS = 100


class LogEvent(BaseModel):
    """A single parsed event from the Star Citizen game.log."""

    timestamp: datetime
    event_type: LogEventType
    raw_line: str
    data: dict[str, Any] = Field(default_factory=dict)


# Callback type: sync or async callables that receive a LogEvent
EventCallback = Callable[[LogEvent], None] | Callable[[LogEvent], Coroutine[Any, Any, None]]


class LogParserModule:
    """Asynchronous game.log tail-and-parse engine.

    Usage::

        parser = LogParserModule("/path/to/game.log")
        parser.subscribe(my_callback)
        await parser.start()
        # ... later ...
        await parser.stop()
    """

    def __init__(self, log_path: str) -> None:
        self._log_path = Path(log_path)
        self._callbacks: list[EventCallback] = []
        self._recent_events: deque[LogEvent] = deque(maxlen=_MAX_RECENT_EVENTS)
        self._current_location: tuple[str, str, str] = ("unknown", "unknown", "unknown")
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin asynchronous file tailing."""
        if self._running:
            logger.warning("LogParserModule is already running.")
            return
        if not self._log_path.exists():
            logger.error("Log file not found: %s", self._log_path)
            raise FileNotFoundError(f"Log file not found: {self._log_path}")
        self._running = True
        self._task = asyncio.create_task(self._tail_loop(), name="log-parser-tail")
        logger.info("LogParserModule started, tailing %s", self._log_path)

    async def stop(self) -> None:
        """Stop tailing and cancel the background task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("LogParserModule stopped.")

    def subscribe(self, callback: EventCallback) -> None:
        """Register a callback to receive parsed LogEvents.

        Callbacks may be synchronous or async functions. They are invoked
        in registration order for each parsed event.
        """
        self._callbacks.append(callback)

    @property
    def latest_events(self) -> list[LogEvent]:
        """Return the most recent LogEvents (up to 100)."""
        return list(self._recent_events)

    @property
    def current_location(self) -> tuple[str, str, str]:
        """Return (system, body, zone) from the most recent location event."""
        return self._current_location

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _tail_loop(self) -> None:
        """Async loop that reads new lines from game.log.

        Opens the file, seeks to the end, then polls for new data at
        a fixed interval. Uses standard file I/O in an executor to
        avoid blocking the event loop.
        """
        loop = asyncio.get_running_loop()

        try:
            # Open the file and seek to end so we only process new lines
            fp = await loop.run_in_executor(None, lambda: open(self._log_path, "r", encoding="utf-8", errors="replace"))
            try:
                await loop.run_in_executor(None, lambda: fp.seek(0, 2))  # seek to EOF

                buffer = ""
                while self._running:
                    chunk = await loop.run_in_executor(None, lambda: fp.read(8192))
                    if chunk:
                        buffer += chunk
                        # Split into complete lines; keep any trailing partial line
                        lines = buffer.split("\n")
                        buffer = lines.pop()  # last element is partial or empty

                        for line in lines:
                            stripped = line.rstrip("\r")
                            if stripped:
                                event = self._parse_line(stripped)
                                if event is not None:
                                    await self._dispatch(event)
                    else:
                        await asyncio.sleep(_POLL_INTERVAL)
            finally:
                await loop.run_in_executor(None, fp.close)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in log tail loop")
            raise

    def _parse_line(self, line: str) -> LogEvent | None:
        """Parse a single log line into a LogEvent using log_patterns.

        Returns None if the line does not match any known pattern.
        """
        result = match_line(line)
        if result is None:
            return None

        event_type, data = result

        timestamp = parse_timestamp(line)
        if timestamp is None:
            timestamp = datetime.now()

        event = LogEvent(
            timestamp=timestamp,
            event_type=event_type,
            raw_line=line,
            data=data,
        )

        # Track location changes
        if event_type == LogEventType.LOCATION_CHANGE:
            self._current_location = (
                data.get("system", self._current_location[0]),
                data.get("body", self._current_location[1]),
                data.get("zone", self._current_location[2]),
            )

        return event

    async def _dispatch(self, event: LogEvent) -> None:
        """Store the event and notify all subscribers."""
        self._recent_events.append(event)

        for callback in self._callbacks:
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Error in log event callback")
