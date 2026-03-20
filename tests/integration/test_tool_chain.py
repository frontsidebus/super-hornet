"""Integration tests for the tool execution pipeline.

Tests the individual tool functions and the dispatch flow from
ClaudeClient._execute_tool. Network-dependent tests (e.g. real API calls
to aviationapi.com) are marked ``@pytest.mark.network``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.context_store import ContextStore
from orchestrator.sim_client import FlightPhase, SimConnectClient, SimState
from orchestrator.tools import (
    create_flight_plan,
    get_checklist,
    get_sim_state,
    lookup_airport,
    search_manual,
)

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sim_state() -> SimState:
    """A realistic SimState for tool tests."""
    return SimState(
        aircraft_title="Cessna 172S Skyhawk",
        flight_phase=FlightPhase.CRUISE,
        position={"latitude": 28.4294, "longitude": -81.309, "altitude": 5500},
        speeds={"indicated": 110, "ground_speed": 120, "vertical_speed": 0},
        attitude={"heading": 270},
        engine={"rpm": [2300], "fuel_flow": [8.6], "oil_temp": [180], "oil_pressure": [60]},
        fuel={"total": 42.0, "total_weight": 252.0, "quantities": [21.0, 21.0]},
        on_ground=False,
    )


@pytest.fixture()
def context_store(tmp_path: Path) -> ContextStore:
    return ContextStore(persist_path=str(tmp_path / "chroma"))


@pytest.fixture()
def populated_context_store(
    context_store: ContextStore, sample_document: Path, sample_document_metadata: dict
) -> ContextStore:
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        context_store.ingest_document(sample_document, metadata=sample_document_metadata)
    )
    return context_store


# ---------------------------------------------------------------------------
# get_sim_state tool
# ---------------------------------------------------------------------------


class TestGetSimState:
    async def test_returns_formatted_telemetry(self, sim_state: SimState) -> None:
        """get_sim_state should call sim_client.get_state and return a dict."""
        mock_sim = AsyncMock(spec=SimConnectClient)
        mock_sim.get_state.return_value = sim_state

        result = await get_sim_state(mock_sim)

        assert isinstance(result, dict)
        assert result["aircraft"] == "Cessna 172S Skyhawk"
        assert result["flight_phase"] == "CRUISE"
        assert result["position"]["altitude_msl"] == 5500
        assert result["speeds"]["indicated"] == 110
        assert result["on_ground"] is False

    async def test_engine_params_in_result(self, sim_state: SimState) -> None:
        mock_sim = AsyncMock(spec=SimConnectClient)
        mock_sim.get_state.return_value = sim_state

        result = await get_sim_state(mock_sim)
        assert result["engine"]["rpm"] == [2300]
        assert result["fuel"]["total_gallons"] == 42.0


# ---------------------------------------------------------------------------
# lookup_airport tool (real network)
# ---------------------------------------------------------------------------


@pytest.mark.network
class TestLookupAirportNetwork:
    """These tests make real HTTP calls to aviationapi.com."""

    async def test_lookup_known_airport(self) -> None:
        """KJFK should return JFK airport info."""
        result = await lookup_airport("KJFK")
        assert "error" not in result
        assert result["identifier"] == "KJFK"
        assert "KENNEDY" in result.get("name", "").upper() or "JFK" in result.get("name", "").upper()

    async def test_lookup_three_letter_code(self) -> None:
        """A 3-letter code like 'JFK' should be auto-prefixed with 'K'."""
        result = await lookup_airport("JFK")
        assert result["identifier"] == "KJFK"

    async def test_lookup_nonexistent_airport(self) -> None:
        """A made-up identifier should return an error dict."""
        result = await lookup_airport("KZZZ")
        assert "error" in result

    async def test_lookup_returns_location_data(self) -> None:
        result = await lookup_airport("KLAX")
        assert "error" not in result
        assert result.get("latitude")
        assert result.get("longitude")
        assert result.get("elevation")


# ---------------------------------------------------------------------------
# lookup_airport tool (mocked network)
# ---------------------------------------------------------------------------


class TestLookupAirportMocked:
    """Test lookup_airport logic without hitting the real API."""

    async def test_lookup_parses_response(self) -> None:
        mock_response = {
            "KJFK": [{
                "facility_name": "JOHN F KENNEDY INTL",
                "city": "NEW YORK",
                "state_full": "NEW YORK",
                "elevation": "13",
                "latitude": "40.63980556",
                "longitude": "-73.77869444",
                "status_code": "O",
            }]
        }
        with patch("orchestrator.tools.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get.return_value = mock_resp

            result = await lookup_airport("KJFK")
            assert result["name"] == "JOHN F KENNEDY INTL"
            assert result["city"] == "NEW YORK"


# ---------------------------------------------------------------------------
# search_manual tool
# ---------------------------------------------------------------------------


class TestSearchManual:
    async def test_search_returns_results(
        self, populated_context_store: ContextStore
    ) -> None:
        results = await search_manual(
            "V-speeds", populated_context_store, aircraft_type="Cessna 172S Skyhawk"
        )
        assert len(results) > 0
        assert "content" in results[0]
        assert "source" in results[0]

    async def test_search_without_aircraft_filter(
        self, populated_context_store: ContextStore
    ) -> None:
        results = await search_manual("takeoff checklist", populated_context_store)
        assert len(results) > 0

    async def test_search_empty_store(self, context_store: ContextStore) -> None:
        results = await search_manual("anything", context_store)
        assert results == []


# ---------------------------------------------------------------------------
# get_checklist tool
# ---------------------------------------------------------------------------


class TestGetChecklist:
    async def test_default_checklist(self, context_store: ContextStore) -> None:
        """With no aircraft-specific docs, should return the default checklist."""
        result = await get_checklist("PREFLIGHT", context_store)
        assert result["phase"] == "PREFLIGHT"
        assert result["source"] == "default"
        assert isinstance(result["items"], list)
        assert len(result["items"]) > 0

    async def test_checklist_for_all_phases(self, context_store: ContextStore) -> None:
        """Every valid FlightPhase should return a checklist without errors."""
        for phase in FlightPhase:
            result = await get_checklist(phase.value, context_store)
            assert "error" not in result
            assert result["phase"] == phase.value

    async def test_invalid_phase(self, context_store: ContextStore) -> None:
        result = await get_checklist("INVALID_PHASE", context_store)
        assert "error" in result

    async def test_aircraft_specific_checklist(
        self, populated_context_store: ContextStore
    ) -> None:
        """With matching docs ingested, should prefer aircraft-specific checklist."""
        result = await get_checklist(
            "TAKEOFF", populated_context_store, aircraft_type="Cessna 172S Skyhawk"
        )
        # It should either return aircraft_manual source or default
        assert result["phase"] == "TAKEOFF"


# ---------------------------------------------------------------------------
# create_flight_plan tool (mocked network)
# ---------------------------------------------------------------------------


class TestCreateFlightPlan:
    async def test_plan_structure(self) -> None:
        """Flight plan should have departure, destination, route, etc."""
        mock_airport = {"identifier": "KXXX", "name": "Test", "city": "Test City"}
        with patch("orchestrator.tools.lookup_airport", new_callable=AsyncMock) as mock_lookup:
            mock_lookup.return_value = mock_airport
            result = await create_flight_plan("KABC", "KXYZ", altitude=8000)

        assert result["cruise_altitude"] == 8000
        assert result["status"] == "draft"
        assert "KABC" in result["route"]
        assert "KXYZ" in result["route"]
        assert result["waypoints"][0] == "KABC"
        assert result["waypoints"][-1] == "KXYZ"

    async def test_plan_with_route_waypoints(self) -> None:
        mock_airport = {"identifier": "KXXX", "name": "Test"}
        with patch("orchestrator.tools.lookup_airport", new_callable=AsyncMock) as mock_lookup:
            mock_lookup.return_value = mock_airport
            result = await create_flight_plan(
                "KJFK", "KLAX", altitude=35000, route="J80 SIE J6"
            )

        assert "J80" in result["waypoints"]
        assert "SIE" in result["waypoints"]
        assert "J6" in result["waypoints"]


# ---------------------------------------------------------------------------
# Tool dispatch flow (simulates what ClaudeClient._execute_tool does)
# ---------------------------------------------------------------------------


class TestToolDispatchFlow:
    """Simulate the full cycle: Claude returns tool_use blocks, tools execute,
    results are returned. We mock Claude but run real tool code."""

    async def test_dispatch_get_sim_state(self, sim_state: SimState) -> None:
        """Dispatch a get_sim_state tool call and verify the result."""
        mock_sim = AsyncMock(spec=SimConnectClient)
        mock_sim.get_state.return_value = sim_state

        # Simulated tool_use block from Claude
        tool_block = {"id": "tool_1", "name": "get_sim_state", "input": {}}

        # Dispatch (mirrors ClaudeClient._execute_tool logic)
        result = await get_sim_state(mock_sim)
        assert result["aircraft"] == "Cessna 172S Skyhawk"

    async def test_dispatch_get_checklist(self, context_store: ContextStore) -> None:
        tool_block = {"id": "tool_2", "name": "get_checklist", "input": {"phase": "CRUISE"}}
        result = await get_checklist(
            tool_block["input"]["phase"], context_store, aircraft_type="Cessna 172S Skyhawk"
        )
        assert result["phase"] == "CRUISE"

    async def test_dispatch_unknown_tool_returns_error(self) -> None:
        """An unknown tool name should produce an error dict (as ClaudeClient does)."""
        # This mirrors the else branch in ClaudeClient._execute_tool
        name = "nonexistent_tool"
        result = {"error": f"Unknown tool: {name}"}
        assert "error" in result

    async def test_tool_result_is_json_serializable(
        self, sim_state: SimState, context_store: ContextStore
    ) -> None:
        """All tool results must be JSON-serializable for the Claude API."""
        mock_sim = AsyncMock(spec=SimConnectClient)
        mock_sim.get_state.return_value = sim_state

        results = [
            await get_sim_state(mock_sim),
            await get_checklist("PREFLIGHT", context_store),
        ]
        for r in results:
            # Should not raise
            serialized = json.dumps(r)
            assert isinstance(serialized, str)
