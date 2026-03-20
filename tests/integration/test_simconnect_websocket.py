"""Integration tests for the SimConnectClient WebSocket protocol.

These tests run a mock WebSocket server (no real MSFS needed) and verify
that the Python client correctly connects, receives state, and handles
edge cases.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets

from orchestrator.sim_client import FlightPhase, SimConnectClient, SimState

# Import the mock server from conftest
from .conftest import MockSimConnectServer

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnection:
    async def test_connect_and_disconnect(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """Client should connect and cleanly disconnect."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()
        assert client._ws is not None
        await client.disconnect()
        assert client._ws is None

    async def test_connect_to_invalid_url_raises(self) -> None:
        """Connecting to a non-existent server should raise."""
        client = SimConnectClient("ws://127.0.0.1:1")  # almost certainly nothing here
        with pytest.raises(Exception):
            await client.connect()

    async def test_multiple_connect_disconnect_cycles(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """Client should handle repeated connect/disconnect."""
        client = SimConnectClient(mock_simconnect_server.url)
        for _ in range(3):
            await client.connect()
            await client.disconnect()


# ---------------------------------------------------------------------------
# get_state request / response
# ---------------------------------------------------------------------------


class TestGetState:
    async def test_get_state_returns_sim_state(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """get_state should return a valid SimState parsed from the server response."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()
        try:
            state = await client.get_state()
            assert isinstance(state, SimState)
            assert state.aircraft_title == "Cessna 172S Skyhawk"
            assert state.position.latitude == pytest.approx(28.4294, abs=0.001)
            assert state.speeds.indicated == pytest.approx(110.0)
            assert state.flight_phase == FlightPhase.CRUISE
        finally:
            await client.disconnect()

    async def test_get_state_without_connection_raises(self) -> None:
        """get_state before connect() should raise ConnectionError."""
        client = SimConnectClient("ws://127.0.0.1:9999")
        with pytest.raises(ConnectionError, match="Not connected"):
            await client.get_state()

    async def test_get_state_updates_internal_state(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """After get_state, client.state should reflect the fetched data."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()
        try:
            # Initial state is default (empty)
            initial = client.state
            assert initial.aircraft_title == ""

            # After get_state, internal state should update
            fetched = await client.get_state()
            assert client.state.aircraft_title == "Cessna 172S Skyhawk"
            assert client.state is fetched
        finally:
            await client.disconnect()

    async def test_get_state_with_modified_server_state(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """Modifying server state should be reflected in subsequent get_state calls."""
        mock_simconnect_server.sim_state["aircraft_title"] = "Piper PA-28 Cherokee"
        mock_simconnect_server.sim_state["on_ground"] = True

        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()
        try:
            state = await client.get_state()
            assert state.aircraft_title == "Piper PA-28 Cherokee"
            assert state.on_ground is True
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# State update subscriptions
# ---------------------------------------------------------------------------


class TestStateSubscription:
    async def test_subscriber_receives_state_updates(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """Subscribed callbacks should fire when the server broadcasts state_update."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()

        received_states: list[SimState] = []

        async def on_update(state: SimState) -> None:
            received_states.append(state)

        client.subscribe(on_update)

        try:
            # Give the listen loop a moment to start
            await asyncio.sleep(0.1)

            # Server broadcasts a state update
            await mock_simconnect_server.broadcast_state_update()
            await asyncio.sleep(0.2)

            assert len(received_states) >= 1
            assert received_states[0].aircraft_title == "Cessna 172S Skyhawk"
        finally:
            await client.disconnect()

    async def test_multiple_subscribers(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """Multiple subscribers should all receive the same update."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()

        results_a: list[SimState] = []
        results_b: list[SimState] = []

        async def cb_a(state: SimState) -> None:
            results_a.append(state)

        async def cb_b(state: SimState) -> None:
            results_b.append(state)

        client.subscribe(cb_a)
        client.subscribe(cb_b)

        try:
            await asyncio.sleep(0.1)
            await mock_simconnect_server.broadcast_state_update()
            await asyncio.sleep(0.2)

            assert len(results_a) >= 1
            assert len(results_b) >= 1
        finally:
            await client.disconnect()

    async def test_subscriber_error_does_not_crash_loop(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """A failing subscriber callback should not kill the listen loop."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()

        good_results: list[SimState] = []

        async def bad_callback(state: SimState) -> None:
            raise ValueError("deliberate test error")

        async def good_callback(state: SimState) -> None:
            good_results.append(state)

        client.subscribe(bad_callback)
        client.subscribe(good_callback)

        try:
            await asyncio.sleep(0.1)
            await mock_simconnect_server.broadcast_state_update()
            await asyncio.sleep(0.2)

            # Despite the bad callback, the good one should still have fired
            assert len(good_results) >= 1
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Reconnection / disconnect handling
# ---------------------------------------------------------------------------


class TestReconnection:
    async def test_server_disconnect_stops_listen_loop(
        self, mock_simconnect_server: MockSimConnectServer
    ) -> None:
        """When the server closes, the listen loop should exit gracefully."""
        client = SimConnectClient(mock_simconnect_server.url)
        await client.connect()

        await asyncio.sleep(0.1)

        # Stop the server to simulate a disconnect
        await mock_simconnect_server.stop()
        await asyncio.sleep(0.5)

        # The listen task should have completed (not hang)
        assert client._listen_task is not None
        assert client._listen_task.done()

    async def test_disconnect_is_safe_when_not_connected(self) -> None:
        """Calling disconnect when never connected should not raise."""
        client = SimConnectClient("ws://127.0.0.1:1")
        await client.disconnect()  # should be a no-op

    async def test_reconnect_after_server_restart(self) -> None:
        """Client should be able to reconnect to a restarted server."""
        server = MockSimConnectServer()
        port = await server.start()
        url = server.url

        client = SimConnectClient(url)
        await client.connect()
        state1 = await client.get_state()
        assert state1.aircraft_title == "Cessna 172S Skyhawk"
        await client.disconnect()

        # Stop and restart server on a new instance (same port may differ)
        await server.stop()

        server2 = MockSimConnectServer(port=port)
        server2.sim_state["aircraft_title"] = "Diamond DA40"
        await server2.start()

        try:
            client2 = SimConnectClient(server2.url)
            await client2.connect()
            state2 = await client2.get_state()
            assert state2.aircraft_title == "Diamond DA40"
            await client2.disconnect()
        finally:
            await server2.stop()


# ---------------------------------------------------------------------------
# Telemetry summary (lightweight, no server needed)
# ---------------------------------------------------------------------------


class TestTelemetrySummary:
    def test_summary_format(self) -> None:
        """telemetry_summary should produce a pipe-separated one-liner."""
        state = SimState(
            flight_phase=FlightPhase.CRUISE,
            position={"altitude": 8500},
            speeds={"indicated": 120, "ground_speed": 135, "vertical_speed": 100},
            attitude={"heading": 270},
            on_ground=False,
        )
        summary = state.telemetry_summary()
        assert "Phase: CRUISE" in summary
        assert "Alt: 8500ft" in summary
        assert "IAS: 120kt" in summary
        assert "GS: 135kt" in summary
        assert "|" in summary

    def test_summary_on_ground_omits_gs(self) -> None:
        state = SimState(on_ground=True, speeds={"ground_speed": 5})
        summary = state.telemetry_summary()
        assert "GS:" not in summary

    def test_summary_autopilot_indicator(self) -> None:
        state = SimState(autopilot={"master": True})
        summary = state.telemetry_summary()
        assert "AP:ON" in summary
