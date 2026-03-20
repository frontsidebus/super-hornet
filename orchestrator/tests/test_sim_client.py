"""Tests for orchestrator.sim_client — SimState model and SimConnectClient."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.sim_client import (
    Attitude,
    AutopilotState,
    EngineParams,
    Environment,
    FlightPhase,
    FuelState,
    Position,
    RadioState,
    SimConnectClient,
    SimState,
    Speeds,
    SurfaceState,
)


# ---------------------------------------------------------------------------
# SimState model parsing
# ---------------------------------------------------------------------------


class TestSimStateParsing:
    """Test that SimState correctly parses from JSON / dict payloads."""

    def test_parse_full_payload(self, sample_state_json: dict[str, Any]) -> None:
        state = SimState.model_validate(sample_state_json)
        assert state.aircraft_title == "Cessna 172 Skyhawk"
        assert state.position.altitude == 6500
        assert state.speeds.indicated == 120
        assert state.autopilot.master is True
        assert state.flight_phase == FlightPhase.CRUISE
        assert state.on_ground is False

    def test_parse_minimal_payload_uses_defaults(self) -> None:
        state = SimState.model_validate({})
        assert state.aircraft_title == ""
        assert state.position.altitude == 0.0
        assert state.on_ground is True
        assert state.flight_phase == FlightPhase.PREFLIGHT

    def test_parse_position(self) -> None:
        pos = Position(latitude=40.6413, longitude=-73.7781, altitude=13, altitude_agl=0)
        assert pos.latitude == pytest.approx(40.6413)
        assert pos.altitude_agl == 0

    def test_parse_engine_params_multiple_engines(self) -> None:
        ep = EngineParams(rpm=[2400, 2400], n1=[85.0, 84.5], fuel_flow=[120.0, 118.5])
        assert len(ep.rpm) == 2
        assert ep.n1[1] == pytest.approx(84.5)

    def test_parse_flight_phase_enum(self) -> None:
        state = SimState.model_validate({"flight_phase": "APPROACH"})
        assert state.flight_phase == FlightPhase.APPROACH

    def test_parse_invalid_flight_phase_raises(self) -> None:
        with pytest.raises(Exception):
            SimState.model_validate({"flight_phase": "HOVERING"})

    def test_radios_default_transponder(self) -> None:
        radio = RadioState()
        assert radio.transponder == 1200

    def test_environment_default_pressure(self) -> None:
        env = Environment()
        assert env.pressure == pytest.approx(29.92)

    def test_fuel_state_default_empty(self) -> None:
        fuel = FuelState()
        assert fuel.quantities == []
        assert fuel.total == 0.0


class TestSimStateTelemetrySummary:
    """Test the telemetry_summary() output format."""

    def test_on_ground_excludes_ground_speed(self, sim_state_parked: SimState) -> None:
        summary = sim_state_parked.telemetry_summary()
        assert "GS:" not in summary
        assert "Phase: PREFLIGHT" in summary

    def test_airborne_includes_ground_speed(self, sim_state_cruise: SimState) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "GS:" in summary
        assert "135kt" in summary

    def test_autopilot_on_shows_ap_flag(self, sim_state_cruise: SimState) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "AP:ON" in summary

    def test_autopilot_off_hides_ap_flag(self, sim_state_parked: SimState) -> None:
        summary = sim_state_parked.telemetry_summary()
        assert "AP:ON" not in summary

    def test_summary_contains_altitude(self, sim_state_cruise: SimState) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "Alt: 6500ft" in summary

    def test_summary_contains_ias(self, sim_state_cruise: SimState) -> None:
        summary = sim_state_cruise.telemetry_summary()
        assert "IAS: 120kt" in summary

    def test_summary_contains_heading(self, sim_state_cruise: SimState) -> None:
        summary = sim_state_cruise.telemetry_summary()
        # heading is 45
        assert "HDG: 45" in summary

    def test_summary_contains_vertical_speed(self, sim_state_descent: SimState) -> None:
        summary = sim_state_descent.telemetry_summary()
        assert "VS: -500fpm" in summary


# ---------------------------------------------------------------------------
# SimConnectClient
# ---------------------------------------------------------------------------


class TestSimConnectClient:
    """Test WebSocket client behavior with mocked connections."""

    def test_initial_state_is_default(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        assert client.state.aircraft_title == ""
        assert client.state.on_ground is True

    @pytest.mark.asyncio
    async def test_connect_creates_listen_task(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))
        with patch("orchestrator.sim_client.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await client.connect()
            assert client._listen_task is not None
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))
        with patch("orchestrator.sim_client.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await client.connect()
            await client.disconnect()
            mock_ws.close.assert_awaited_once()
            assert client._ws is None

    @pytest.mark.asyncio
    async def test_get_state_sends_request_and_parses(self, sample_state_json: dict[str, Any]) -> None:
        client = SimConnectClient("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps(sample_state_json))
        client._ws = mock_ws

        state = await client.get_state()
        mock_ws.send.assert_awaited_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "get_state"
        assert state.aircraft_title == "Cessna 172 Skyhawk"

    @pytest.mark.asyncio
    async def test_get_state_without_connection_raises(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        with pytest.raises(ConnectionError, match="Not connected"):
            await client.get_state()

    @pytest.mark.asyncio
    async def test_subscribe_callback_called_on_state_update(self, sample_state_json: dict[str, Any]) -> None:
        client = SimConnectClient("ws://localhost:8080")
        callback = AsyncMock()
        client.subscribe(callback)

        message = json.dumps({"type": "state_update", "data": sample_state_json})
        mock_ws = AsyncMock()

        # Simulate the listen loop receiving one message then closing
        async def fake_aiter(self):
            yield message

        mock_ws.__aiter__ = fake_aiter.__get__(mock_ws)
        client._ws = mock_ws

        # Run the listen loop directly
        await client._listen_loop()

        callback.assert_awaited_once()
        received_state = callback.call_args[0][0]
        assert received_state.aircraft_title == "Cessna 172 Skyhawk"

    @pytest.mark.asyncio
    async def test_listen_loop_ignores_invalid_json(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        callback = AsyncMock()
        client.subscribe(callback)

        async def fake_aiter(self):
            yield "not valid json {{{{"

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = fake_aiter.__get__(mock_ws)
        client._ws = mock_ws

        await client._listen_loop()
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_listen_loop_ignores_non_state_update_messages(self) -> None:
        client = SimConnectClient("ws://localhost:8080")
        callback = AsyncMock()
        client.subscribe(callback)

        async def fake_aiter(self):
            yield json.dumps({"type": "ping"})

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = fake_aiter.__get__(mock_ws)
        client._ws = mock_ws

        await client._listen_loop()
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subscriber_exception_does_not_crash_loop(self, sample_state_json: dict[str, Any]) -> None:
        client = SimConnectClient("ws://localhost:8080")
        bad_callback = AsyncMock(side_effect=RuntimeError("boom"))
        good_callback = AsyncMock()
        client.subscribe(bad_callback)
        client.subscribe(good_callback)

        message = json.dumps({"type": "state_update", "data": sample_state_json})

        async def fake_aiter(self):
            yield message

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = fake_aiter.__get__(mock_ws)
        client._ws = mock_ws

        await client._listen_loop()

        bad_callback.assert_awaited_once()
        good_callback.assert_awaited_once()
