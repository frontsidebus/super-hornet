"""WebSocket client for the SimConnect bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

import websockets
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FlightPhase(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    TAXI = "TAXI"
    TAKEOFF = "TAKEOFF"
    CLIMB = "CLIMB"
    CRUISE = "CRUISE"
    DESCENT = "DESCENT"
    APPROACH = "APPROACH"
    LANDING = "LANDING"
    LANDED = "LANDED"


# ---------------------------------------------------------------------------
# Connection state tracking
# ---------------------------------------------------------------------------


class ConnectionState(str, Enum):
    """WebSocket connection lifecycle states."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"


# ---------------------------------------------------------------------------
# Pydantic models matching the SimConnect bridge JSON field names exactly
# ---------------------------------------------------------------------------


class Position(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0
    altitude_msl: float = 0.0  # feet MSL
    altitude_agl: float = 0.0  # feet AGL


class Attitude(BaseModel):
    pitch: float = 0.0  # degrees
    bank: float = 0.0  # degrees
    heading_true: float = 0.0  # degrees true
    heading_magnetic: float = 0.0  # degrees magnetic


class Speeds(BaseModel):
    indicated_airspeed: float = 0.0  # knots
    true_airspeed: float = 0.0  # knots
    ground_speed: float = 0.0  # knots
    mach: float = 0.0
    vertical_speed: float = 0.0  # feet per minute


class EngineData(BaseModel):
    """Single-engine parameter block as sent by the bridge."""

    rpm: float = 0.0
    manifold_pressure: float = 0.0
    fuel_flow_gph: float = 0.0
    egt: float = 0.0
    oil_temp: float = 0.0
    oil_pressure: float = 0.0


class Engines(BaseModel):
    """Engine section from the bridge, containing a count and array."""

    engine_count: int = 0
    engines: list[EngineData] = Field(default_factory=list)

    @property
    def active_engines(self) -> list[EngineData]:
        """Return only the engines that are actually installed (up to engine_count)."""
        return self.engines[: self.engine_count]


class AutopilotState(BaseModel):
    master: bool = False
    heading: float = 0.0
    altitude: float = 0.0
    vertical_speed: float = 0.0
    airspeed: float = 0.0


class RadioState(BaseModel):
    com1: float = 0.0
    com2: float = 0.0
    nav1: float = 0.0
    nav2: float = 0.0


class FuelState(BaseModel):
    total_gallons: float = 0.0
    total_weight_lbs: float = 0.0


class Environment(BaseModel):
    wind_speed_kts: float = 0.0
    wind_direction: float = 0.0  # degrees
    visibility_sm: float = 0.0  # statute miles
    temperature_c: float = 0.0  # celsius
    barometer_inhg: float = 29.92  # inHg


class SurfaceState(BaseModel):
    gear_handle: bool = False
    flaps_percent: float = 0.0
    spoilers_percent: float = 0.0


class SimState(BaseModel):
    """Complete snapshot of the simulator state.

    Field names match the SimConnect bridge broadcast JSON exactly.
    """

    timestamp: str = ""
    connected: bool = False
    aircraft: str = ""
    position: Position = Field(default_factory=Position)
    attitude: Attitude = Field(default_factory=Attitude)
    speeds: Speeds = Field(default_factory=Speeds)
    engines: Engines = Field(default_factory=Engines)
    autopilot: AutopilotState = Field(default_factory=AutopilotState)
    radios: RadioState = Field(default_factory=RadioState)
    fuel: FuelState = Field(default_factory=FuelState)
    environment: Environment = Field(default_factory=Environment)
    surfaces: SurfaceState = Field(default_factory=SurfaceState)
    # Computed / enriched by the orchestrator (not from bridge)
    flight_phase: FlightPhase = FlightPhase.PREFLIGHT

    @property
    def on_ground(self) -> bool:
        """Derived from altitude AGL -- on the ground if below 10 feet."""
        return self.position.altitude_agl < 10

    def telemetry_summary(self) -> str:
        """One-line summary of key flight parameters for context injection."""
        parts = [
            f"Phase: {self.flight_phase.value}",
            f"Alt: {self.position.altitude_msl:.0f}ft",
            f"IAS: {self.speeds.indicated_airspeed:.0f}kt",
            f"HDG: {self.attitude.heading_magnetic:.0f}\u00b0",
            f"VS: {self.speeds.vertical_speed:+.0f}fpm",
        ]
        if not self.on_ground:
            parts.append(f"GS: {self.speeds.ground_speed:.0f}kt")
        if self.autopilot.master:
            parts.append("AP:ON")
        return " | ".join(parts)


StateCallback = Callable[[SimState], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Health status for subsystem monitoring
# ---------------------------------------------------------------------------


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


class SimConnectClient:
    """Manages the WebSocket connection to the SimConnect bridge.

    Features:
    - Automatic reconnection with exponential backoff
    - Heartbeat mechanism to detect stale connections
    - Connection state tracking
    - Telemetry batching via delta detection
    """

    # Reconnection parameters
    RECONNECT_BASE_DELAY: float = 1.0  # seconds
    RECONNECT_MAX_DELAY: float = 30.0  # seconds
    RECONNECT_BACKOFF_FACTOR: float = 2.0

    # Heartbeat parameters
    HEARTBEAT_INTERVAL: float = 5.0  # seconds between heartbeat checks
    HEARTBEAT_TIMEOUT: float = 15.0  # seconds before considering connection stale

    def __init__(
        self,
        url: str,
        auto_reconnect: bool = True,
    ) -> None:
        self._url = url
        self._auto_reconnect = auto_reconnect
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._state = SimState()
        self._subscribers: list[StateCallback] = []
        self._listen_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._connection_state = ConnectionState.DISCONNECTED
        self._last_message_time: float = 0.0
        self._reconnect_count: int = 0
        self._messages_received: int = 0
        self._last_state_json: str = ""  # for delta detection

    @property
    def state(self) -> SimState:
        return self._state

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    @property
    def last_message_age(self) -> float:
        """Seconds since the last message was received."""
        if self._last_message_time == 0.0:
            return float("inf")
        return time.monotonic() - self._last_message_time

    @property
    def stats(self) -> dict[str, Any]:
        """Connection statistics for diagnostics."""
        return {
            "connection_state": self._connection_state.value,
            "reconnect_count": self._reconnect_count,
            "messages_received": self._messages_received,
            "last_message_age_s": round(self.last_message_age, 1),
            "url": self._url,
        }

    async def connect(self) -> None:
        """Connect to the SimConnect bridge WebSocket server."""
        self._connection_state = ConnectionState.CONNECTING
        logger.info("Connecting to SimConnect bridge at %s", self._url)
        try:
            self._ws = await websockets.connect(self._url)
            self._connection_state = ConnectionState.CONNECTED
            self._last_message_time = time.monotonic()
            self._listen_task = asyncio.create_task(self._listen_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("Connected to SimConnect bridge")
        except Exception:
            self._connection_state = ConnectionState.DISCONNECTED
            raise

    async def disconnect(self) -> None:
        """Disconnect and cancel all background tasks."""
        self._auto_reconnect = False  # prevent reconnect during shutdown
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connection_state = ConnectionState.DISCONNECTED
        logger.info("Disconnected from SimConnect bridge")

    async def get_state(self) -> SimState:
        """Return the cached sim state (updated continuously by the broadcast)."""
        return self._state

    def subscribe(self, callback: StateCallback) -> None:
        self._subscribers.append(callback)

    # -------------------------------------------------------------------
    # Heartbeat monitoring
    # -------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Periodically check that the bridge is still sending data.

        If no message has been received within HEARTBEAT_TIMEOUT, the
        connection is considered stale and a reconnect is triggered.
        """
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                age = self.last_message_age
                if age > self.HEARTBEAT_TIMEOUT:
                    logger.warning(
                        "No data from bridge for %.1fs (timeout=%.1fs); "
                        "connection may be stale",
                        age,
                        self.HEARTBEAT_TIMEOUT,
                    )
                    # Send a ping to verify the connection is alive
                    if self._ws is not None:
                        try:
                            pong = await asyncio.wait_for(
                                self._ws.ping(), timeout=5.0
                            )
                            await asyncio.wait_for(pong, timeout=5.0)
                            logger.debug("Ping/pong succeeded; connection alive")
                        except Exception:
                            logger.warning(
                                "Ping failed; triggering reconnect"
                            )
                            if self._ws is not None:
                                await self._ws.close()
                            break
        except asyncio.CancelledError:
            raise

    # -------------------------------------------------------------------
    # Reconnection with exponential backoff
    # -------------------------------------------------------------------

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        if not self._auto_reconnect:
            return

        self._connection_state = ConnectionState.RECONNECTING
        delay = self.RECONNECT_BASE_DELAY

        while self._auto_reconnect:
            self._reconnect_count += 1
            logger.info(
                "Reconnection attempt %d (delay=%.1fs)",
                self._reconnect_count,
                delay,
            )
            try:
                await asyncio.sleep(delay)
                self._ws = await websockets.connect(self._url)
                self._connection_state = ConnectionState.CONNECTED
                self._last_message_time = time.monotonic()
                logger.info(
                    "Reconnected to SimConnect bridge (attempt %d)",
                    self._reconnect_count,
                )
                # Restart heartbeat
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop()
                )
                return
            except Exception as exc:
                logger.warning(
                    "Reconnection attempt %d failed: %s",
                    self._reconnect_count,
                    exc,
                )
                delay = min(
                    delay * self.RECONNECT_BACKOFF_FACTOR,
                    self.RECONNECT_MAX_DELAY,
                )

        self._connection_state = ConnectionState.DISCONNECTED

    # -------------------------------------------------------------------
    # Receive loop
    # -------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Background loop that receives state broadcasts from the bridge.

        The bridge sends the full state JSON directly (no wrapping ``type``
        field).  We identify state broadcasts by checking for the ``position``
        key.  Messages that contain a ``type`` field (e.g. ``state_response``)
        are logged and ignored -- the broadcast is the authoritative source.

        On connection loss, triggers automatic reconnection if enabled.
        """
        while True:
            assert self._ws is not None
            try:
                async for message in self._ws:
                    self._last_message_time = time.monotonic()
                    self._messages_received += 1
                    try:
                        data = json.loads(message)

                        # The bridge broadcasts raw state JSON -- identify it
                        # by the presence of the "position" key.
                        if "position" in data:
                            # Delta detection: skip processing if the state
                            # JSON is identical to the last one received. This
                            # avoids unnecessary Pydantic validation + callback
                            # overhead when the sim is paused or on the ground.
                            msg_str = message if isinstance(
                                message, str
                            ) else message.decode()
                            if msg_str == self._last_state_json:
                                continue
                            self._last_state_json = msg_str

                            # Preserve the current flight_phase (set by the
                            # orchestrator's phase detector) across updates.
                            current_phase = self._state.flight_phase
                            self._state = SimState.model_validate(data)
                            self._state.flight_phase = current_phase

                            for cb in self._subscribers:
                                try:
                                    await cb(self._state)
                                except Exception:
                                    logger.exception(
                                        "Error in state subscriber callback"
                                    )
                        elif "type" in data:
                            # Informational response (e.g. state_response).
                            logger.debug(
                                "Received typed message from bridge: %s",
                                data.get("type"),
                            )
                        else:
                            logger.debug(
                                "Ignoring unrecognised bridge message"
                            )

                    except json.JSONDecodeError:
                        logger.warning(
                            "Received invalid JSON from bridge"
                        )
            except websockets.ConnectionClosed:
                logger.warning("SimConnect bridge connection closed")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected error in listen loop")

            # Connection lost -- attempt reconnect
            self._connection_state = ConnectionState.DISCONNECTED
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()

            if not self._auto_reconnect:
                break

            await self._reconnect()
            if self._connection_state != ConnectionState.CONNECTED:
                break  # reconnect gave up
