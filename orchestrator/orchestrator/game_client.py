"""Game state client that aggregates data from perception modules.

Replaces SimConnectClient. Instead of a WebSocket connection to a
SimConnect bridge, this composes state from:
- LogParserModule (game.log events)
- VisionModule (screen capture + Claude Vision)
- API clients (UEX, Wiki -- for enrichment, not real-time)

Maintains the same subscription/callback pattern as SimConnectClient
for backward compatibility with the Orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from .game_state import GameActivity, GameState
from .health import ConnectionState
from .log_patterns import extract_ship_name

if TYPE_CHECKING:
    from .log_parser import LogParserModule
    from .vision import VisionModule

logger = logging.getLogger(__name__)

StateCallback = Callable[[GameState], Coroutine[Any, Any, None]]


class GameStateClient:
    """Aggregates game state from perception modules.

    Polls perception sources at a configurable interval and composes
    a unified GameState snapshot. Subscribers are notified on each update.
    """

    def __init__(
        self,
        log_parser: LogParserModule | None = None,
        vision_module: VisionModule | None = None,
        update_interval: float = 1.0,
    ) -> None:
        self._log_parser = log_parser
        self._vision_module = vision_module
        self._update_interval = update_interval
        self._state = GameState()
        self._subscribers: list[StateCallback] = []
        self._update_task: asyncio.Task[None] | None = None
        self._connection_state = ConnectionState.DISCONNECTED
        self._last_update_time: float = 0.0

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    @property
    def last_update_age(self) -> float:
        """Seconds since the last state update."""
        if self._last_update_time == 0.0:
            return float("inf")
        return time.monotonic() - self._last_update_time

    async def connect(self) -> None:
        """Start perception modules and begin state aggregation."""
        self._connection_state = ConnectionState.CONNECTING
        logger.info("Starting game state aggregation")

        if self._log_parser:
            try:
                await self._log_parser.start()
                logger.info("Log parser started")
            except Exception:
                logger.warning("Log parser failed to start", exc_info=True)

        if self._vision_module:
            try:
                await self._vision_module.start()
                logger.info("Vision module started")
            except Exception:
                logger.warning("Vision module failed to start", exc_info=True)

        self._connection_state = ConnectionState.CONNECTED
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("Game state client connected")

    async def disconnect(self) -> None:
        """Stop all perception modules and background tasks."""
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

        if self._log_parser:
            await self._log_parser.stop()
        if self._vision_module:
            await self._vision_module.stop()

        self._connection_state = ConnectionState.DISCONNECTED
        logger.info("Game state client disconnected")

    async def get_state(self) -> GameState:
        """Return the current aggregated game state."""
        return self._state

    def subscribe(self, callback: StateCallback) -> None:
        """Register a callback for state updates."""
        self._subscribers.append(callback)

    async def _update_loop(self) -> None:
        """Background loop that aggregates state from perception modules."""
        while True:
            try:
                new_state = await self._compose_state()
                self._state = new_state
                self._last_update_time = time.monotonic()

                for cb in self._subscribers:
                    try:
                        await cb(self._state)
                    except Exception:
                        logger.exception("Error in state subscriber callback")

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in state update loop")

            await asyncio.sleep(self._update_interval)

    async def _compose_state(self) -> GameState:
        """Build a GameState from all available perception sources."""
        state = GameState(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        # Ingest log parser data
        if self._log_parser:
            events = self._log_parser.latest_events
            state.raw_log_events = [
                {"type": e.event_type.value, "data": e.data, "raw": e.raw_line}
                for e in events[-20:]  # keep last 20 events
            ]

            system, body, zone = self._log_parser.current_location
            state.player.location_system = system
            state.player.location_body = body
            state.player.location_zone = zone

            # Detect ship from recent log events (entity references like ANVL_Asgard_123)
            for e in reversed(events[-50:]):
                ship_name = e.data.get("ship")
                if not ship_name and e.raw_line:
                    ship_name = extract_ship_name(e.raw_line)
                if ship_name:
                    state.ship.name = ship_name
                    state.player.in_ship = True
                    break

        # Ingest vision data (if available and recently updated)
        if self._vision_module and self._vision_module.latest_analysis:
            state.vision_data = self._vision_module.latest_analysis

        return state
