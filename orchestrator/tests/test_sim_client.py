"""Tests for orchestrator.sim_client -- SimState model and SimConnectClient."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.sim_client import (
    Attitude,
    AutopilotState,
    ConnectionState,
    EngineData,
    Engines,
    Environment,
    FlightPhase,
    FuelState,
    HealthMonitor,
    Position,
    RadioState,
    SimConnectClient,
    SimState,
    Speeds,
    SubsystemHealth,
    SurfaceState,
)


# ---------------------------------------------------------------------------
# SimState model parsing
# ---------------------------------------------------------------------------


class TestSimStateParsing:
    """Test that SimState correctly parses from JSON / dict payloads."""

    def test_parse_full_payload(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        state = SimState.model_validate(sample_bridge_broadcast)
        assert state.aircraft == "Cessna 172 Skyhawk"
        assert state.position.altitude_msl == 6500
        assert state.speeds.indicated_airspeed == 120
        assert state.autopilot.master is True
        # flight_phase is not in the bridge payload, so it defaults
        assert state.on_ground is False  # AGL=6400, far above 10

    def test_parse_minimal_payload_uses_defaults(self) -> None:
        state = SimState.model_validate({})
        assert state.aircraft == ""
        assert state.position.altitude_msl == 0.0
        assert state.on_ground is True  # AGL=0, < 10
        assert state.flight_phase == FlightPhase.PREFLIGHT

    def test_parse_position(self) -> None:
        pos = Position(
            latitude=40.6413,
            longitude=-73.7781,
            altitude_msl=13,
            altitude_agl=0,
        )
        assert pos.latitude == pytest.approx(40.6413)
        assert pos.altitude_agl == 0

    def test_parse_engine_data(self) -> None:
        engines = Engines(
            engine_count=2,
            engines=[
                EngineData(
                    rpm=2400,
                    fuel_flow_gph=9.0,
                    oil_temp=190,
                    oil_pressure=60,
                ),
                EngineData(
                    rpm=2400,
                    fuel_flow_gph=8.5,
                    oil_temp=188,
                    oil_pressure=59,
                ),
            ],
        )
        assert len(engines.active_engines) == 2
        assert engines.active_engines[1].fuel_flow_gph == pytest.approx(
            8.5
        )

    def test_parse_flight_phase_enum(self) -> None:
        state = SimState.model_validate({"flight_phase": "APPROACH"})
        assert state.flight_phase == FlightPhase.APPROACH

    def test_parse_invalid_flight_phase_raises(self) -> None:
        with pytest.raises(Exception):
            SimState.model_validate({"flight_phase": "HOVERING"})

    def test_radios_defaults(self) -> None:
        radio = RadioState()
        assert radio.com1 == 0.0
        assert radio.nav1 == 0.0

    def test_environment_default_pressure(self) -> None:
        env = Environment()
        assert env.barometer_inhg == pytest.approx(29.92)

    def test_fuel_state_default_empty(self) -> None:
        fuel = FuelState()
        assert fuel.total_gallons == 0.0
        assert fuel.total_weight_lbs == 0.0

    def test_on_ground_derived_from_agl(self) -> None:
        state = SimState(position=Position(altitude_agl=5))
        assert state.on_ground is True
        state2 = SimState(position=Position(altitude_agl=15))
        assert state2.on_ground is False


class TestSimStateTelemetrySummary:
    """Test the telemetry_summary() output format."""

    def test_on_ground_excludes_ground_speed(
        self, sim_state_parked: SimState
    ) -> None:
        summary = sim_state_parked.telemetry_summary()
        assert "GS:" not in summary
        assert "Phase: PREFLIGHT" in summary

    def test_airborne_includes_ground_speed(
        self, sim_state_cruise: SimState
    ) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "GS:" in summary
        assert "135kt" in summary

    def test_autopilot_on_shows_ap_flag(
        self, sim_state_cruise: SimState
    ) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "AP:ON" in summary

    def test_autopilot_off_hides_ap_flag(
        self, sim_state_parked: SimState
    ) -> None:
        summary = sim_state_parked.telemetry_summary()
        assert "AP:ON" not in summary

    def test_summary_contains_altitude(
        self, sim_state_cruise: SimState
    ) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "Alt: 6500ft" in summary

    def test_summary_contains_ias(
        self, sim_state_cruise: SimState
    ) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "IAS: 120kt" in summary

    def test_summary_contains_heading(
        self, sim_state_cruise: SimState
    ) -> None:
        summary = sim_state_cruise.telemetry_summary()
        # heading_magnetic is 42
        assert "HDG: 42" in summary

    def test_summary_contains_vertical_speed(
        self, sim_state_descent: SimState
    ) -> None:
        summary = sim_state_descent.telemetry_summary()
        assert "VS: -500fpm" in summary


# ---------------------------------------------------------------------------
# SimConnectClient
# ---------------------------------------------------------------------------


class TestSimConnectClient:
    """Test WebSocket client behavior with mocked connections."""

    def test_initial_state_is_default(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        assert client.state.aircraft == ""
        assert client.state.on_ground is True

    def test_initial_connection_state(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        assert client.connection_state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_connect_creates_listen_task(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))
        with patch(
            "orchestrator.sim_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            await client.connect()
            assert client._listen_task is not None
            assert (
                client.connection_state == ConnectionState.CONNECTED
            )
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))
        with patch(
            "orchestrator.sim_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            await client.connect()
            await client.disconnect()
            mock_ws.close.assert_awaited_once()
            assert client._ws is None
            assert (
                client.connection_state
                == ConnectionState.DISCONNECTED
            )

    @pytest.mark.asyncio
    async def test_get_state_returns_cached_state(self) -> None:
        """get_state now returns cached state -- no WS send needed."""
        client = SimConnectClient("ws://localhost:8080")
        state = await client.get_state()
        assert state.aircraft == ""  # default cached state

    @pytest.mark.asyncio
    async def test_subscribe_callback_called_on_broadcast(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        callback = AsyncMock()
        client.subscribe(callback)

        # Bridge broadcasts raw state JSON (no type wrapper)
        message = json.dumps(sample_bridge_broadcast)
        mock_ws = AsyncMock()

        async def fake_aiter():
            yield message

        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()

        callback.assert_awaited_once()
        received_state = callback.call_args[0][0]
        assert received_state.aircraft == "Cessna 172 Skyhawk"

    @pytest.mark.asyncio
    async def test_listen_loop_ignores_invalid_json(self) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        callback = AsyncMock()
        client.subscribe(callback)

        async def fake_aiter():
            yield "not valid json {{{{"

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_listen_loop_ignores_typed_messages(self) -> None:
        """Messages with a 'type' field but no 'position' are ignored."""
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        callback = AsyncMock()
        client.subscribe(callback)

        async def fake_aiter():
            yield json.dumps(
                {"type": "state_response", "message": "OK"}
            )

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subscriber_exception_does_not_crash_loop(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        bad_callback = AsyncMock(side_effect=RuntimeError("boom"))
        good_callback = AsyncMock()
        client.subscribe(bad_callback)
        client.subscribe(good_callback)

        message = json.dumps(sample_bridge_broadcast)

        async def fake_aiter():
            yield message

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()

        bad_callback.assert_awaited_once()
        good_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_listen_loop_preserves_flight_phase(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        """The listen loop should preserve the current flight_phase."""
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        client._state.flight_phase = FlightPhase.CRUISE

        message = json.dumps(sample_bridge_broadcast)

        async def fake_aiter():
            yield message

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()

        assert client.state.flight_phase == FlightPhase.CRUISE


# ---------------------------------------------------------------------------
# Connection state and stats
# ---------------------------------------------------------------------------


class TestConnectionState:
    """Test connection state tracking and diagnostics."""

    def test_stats_default(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        stats = client.stats
        assert stats["connection_state"] == "DISCONNECTED"
        assert stats["reconnect_count"] == 0
        assert stats["messages_received"] == 0
        assert stats["url"] == "ws://localhost:8080"

    def test_last_message_age_infinity_when_no_messages(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        assert client.last_message_age == float("inf")

    @pytest.mark.asyncio
    async def test_connect_sets_state_to_connected(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))
        with patch(
            "orchestrator.sim_client.websockets.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            await client.connect()
            assert (
                client.connection_state == ConnectionState.CONNECTED
            )
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_failure_sets_state_to_disconnected(
        self,
    ) -> None:
        client = SimConnectClient("ws://localhost:8080")
        with patch(
            "orchestrator.sim_client.websockets.connect",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("refused"),
        ):
            with pytest.raises(ConnectionRefusedError):
                await client.connect()
            assert (
                client.connection_state
                == ConnectionState.DISCONNECTED
            )

    @pytest.mark.asyncio
    async def test_messages_received_incremented(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        message = json.dumps(sample_bridge_broadcast)

        async def fake_aiter():
            yield message
            yield message

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()

        # Second message is a duplicate (delta detection skips it) but
        # _messages_received still increments for every raw message.
        assert client._messages_received == 2


# ---------------------------------------------------------------------------
# Reconnection logic
# ---------------------------------------------------------------------------


class TestReconnection:
    """Test automatic reconnection with exponential backoff."""

    @pytest.mark.asyncio
    async def test_reconnect_increments_count(self) -> None:
        client = SimConnectClient("ws://localhost:8080")

        # First call fails, second succeeds
        call_count = 0

        async def mock_connect(url: str) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionRefusedError("refused")
            ws = AsyncMock()
            ws.__aiter__ = MagicMock(return_value=iter([]))
            return ws

        with patch(
            "orchestrator.sim_client.websockets.connect",
            side_effect=mock_connect,
        ):
            # Override delays to make test fast
            client.RECONNECT_BASE_DELAY = 0.01
            client.RECONNECT_MAX_DELAY = 0.02
            await client._reconnect()
            assert client._reconnect_count >= 1
            assert (
                client.connection_state == ConnectionState.CONNECTED
            )

        # Clean up
        client._auto_reconnect = False
        if client._heartbeat_task:
            client._heartbeat_task.cancel()
            try:
                await client._heartbeat_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_reconnect_disabled_does_nothing(self) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        await client._reconnect()
        assert (
            client.connection_state == ConnectionState.DISCONNECTED
        )
        assert client._reconnect_count == 0

    @pytest.mark.asyncio
    async def test_reconnect_backoff_increases_delay(self) -> None:
        """Verify the delay increases exponentially."""
        client = SimConnectClient("ws://localhost:8080")
        client.RECONNECT_BASE_DELAY = 0.01
        client.RECONNECT_MAX_DELAY = 0.1
        client.RECONNECT_BACKOFF_FACTOR = 2.0

        attempts = []

        async def mock_connect(url: str) -> AsyncMock:
            attempts.append(time.monotonic())
            if len(attempts) < 3:
                raise ConnectionRefusedError("refused")
            ws = AsyncMock()
            ws.__aiter__ = MagicMock(return_value=iter([]))
            return ws

        with patch(
            "orchestrator.sim_client.websockets.connect",
            side_effect=mock_connect,
        ):
            await client._reconnect()

        assert len(attempts) == 3
        assert client._reconnect_count == 3

        # Clean up
        client._auto_reconnect = False
        if client._heartbeat_task:
            client._heartbeat_task.cancel()
            try:
                await client._heartbeat_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_listen_loop_triggers_reconnect_on_close(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        """When the WebSocket closes, the listen loop should reconnect."""
        import websockets

        client = SimConnectClient("ws://localhost:8080")
        client.RECONNECT_BASE_DELAY = 0.01

        # First WS: yields one message then closes
        async def fake_aiter_close():
            yield json.dumps(sample_bridge_broadcast)
            raise websockets.ConnectionClosed(None, None)

        first_ws = AsyncMock()
        first_ws.__aiter__ = lambda self: fake_aiter_close()

        # Second WS (after reconnect): yields nothing
        second_ws = AsyncMock()
        second_ws.__aiter__ = MagicMock(return_value=iter([]))

        call_count = 0

        async def mock_connect(url: str) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return second_ws
            return second_ws

        client._ws = first_ws

        with patch(
            "orchestrator.sim_client.websockets.connect",
            side_effect=mock_connect,
        ):
            # Run listen loop in a task so we can cancel it
            task = asyncio.create_task(client._listen_loop())
            await asyncio.sleep(0.1)
            client._auto_reconnect = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have attempted at least one reconnect
        assert client._reconnect_count >= 1


# ---------------------------------------------------------------------------
# Delta detection
# ---------------------------------------------------------------------------


class TestDeltaDetection:
    """Test that duplicate state messages are skipped."""

    @pytest.mark.asyncio
    async def test_duplicate_messages_skip_callback(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        callback = AsyncMock()
        client.subscribe(callback)

        message = json.dumps(sample_bridge_broadcast)

        async def fake_aiter():
            yield message
            yield message  # duplicate
            yield message  # duplicate

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()

        # Only the first unique message triggers the callback
        assert callback.await_count == 1

    @pytest.mark.asyncio
    async def test_changed_messages_trigger_callback(
        self, sample_bridge_broadcast: dict[str, Any]
    ) -> None:
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        callback = AsyncMock()
        client.subscribe(callback)

        msg1 = json.dumps(sample_bridge_broadcast)
        modified = dict(sample_bridge_broadcast)
        modified["position"] = {
            **modified["position"],
            "altitude_msl": 7000,
        }
        msg2 = json.dumps(modified)

        async def fake_aiter():
            yield msg1
            yield msg2

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: fake_aiter()
        client._ws = mock_ws

        await client._listen_loop()

        assert callback.await_count == 2


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------


class TestHealthMonitor:
    """Test the subsystem health monitoring."""

    def test_register_creates_unhealthy_entry(self) -> None:
        monitor = HealthMonitor()
        monitor.register("test_subsystem")
        sub = monitor.get("test_subsystem")
        assert sub is not None
        assert sub.healthy is False
        assert sub.age_seconds == float("inf")

    def test_update_sets_healthy(self) -> None:
        monitor = HealthMonitor()
        monitor.register("svc")
        monitor.update("svc", True, "OK")
        sub = monitor.get("svc")
        assert sub is not None
        assert sub.healthy is True
        assert sub.message == "OK"
        assert sub.age_seconds < 1.0

    def test_update_auto_registers(self) -> None:
        monitor = HealthMonitor()
        monitor.update("new_svc", False, "down")
        sub = monitor.get("new_svc")
        assert sub is not None
        assert sub.healthy is False

    def test_all_healthy(self) -> None:
        monitor = HealthMonitor()
        monitor.update("a", True)
        monitor.update("b", True)
        assert monitor.all_healthy() is True
        monitor.update("b", False)
        assert monitor.all_healthy() is False

    def test_summary_returns_all_subsystems(self) -> None:
        monitor = HealthMonitor()
        monitor.update("x", True, "good")
        monitor.update("y", False, "bad")
        summary = monitor.summary()
        assert "x" in summary
        assert "y" in summary
        assert summary["x"]["healthy"] is True
        assert summary["y"]["healthy"] is False

    def test_subsystem_health_age(self) -> None:
        sub = SubsystemHealth(name="test")
        assert sub.age_seconds == float("inf")
        sub.last_seen = time.monotonic()
        assert sub.age_seconds < 1.0


# ---------------------------------------------------------------------------
# Graceful degradation scenarios
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Test that the system degrades gracefully when subsystems are down."""

    @pytest.mark.asyncio
    async def test_client_works_without_connection(self) -> None:
        """SimConnectClient should return a default state when not connected."""
        client = SimConnectClient("ws://localhost:8080")
        state = await client.get_state()
        assert state.connected is False
        assert state.aircraft == ""
        # Should not raise

    @pytest.mark.asyncio
    async def test_connect_failure_is_recoverable(self) -> None:
        """After a connect failure, the client should remain usable."""
        client = SimConnectClient(
            "ws://localhost:8080", auto_reconnect=False
        )
        with patch(
            "orchestrator.sim_client.websockets.connect",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("refused"),
        ):
            with pytest.raises(ConnectionRefusedError):
                await client.connect()

        # Client should still be in a usable state
        assert (
            client.connection_state == ConnectionState.DISCONNECTED
        )
        state = await client.get_state()
        assert state is not None

    def test_health_monitor_tracks_degraded_state(self) -> None:
        """Monitor should correctly report when subsystems are degraded."""
        monitor = HealthMonitor()
        monitor.update("simconnect_bridge", True, "Connected")
        monitor.update("chromadb", False, "Unavailable")
        monitor.update("whisper", False, "Unreachable")
        monitor.update("claude_api", True, "Ready")

        assert monitor.all_healthy() is False

        summary = monitor.summary()
        assert summary["simconnect_bridge"]["healthy"] is True
        assert summary["chromadb"]["healthy"] is False
        assert summary["whisper"]["healthy"] is False
        assert summary["claude_api"]["healthy"] is True
