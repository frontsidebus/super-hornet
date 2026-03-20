"""Tests for orchestrator.tools — tool function implementations."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from orchestrator.sim_client import (
    Attitude,
    AutopilotState,
    EngineParams,
    Environment,
    FlightPhase,
    FuelState,
    Position,
    SimConnectClient,
    SimState,
    Speeds,
    SurfaceState,
)
from orchestrator.tools import (
    DEFAULT_CHECKLISTS,
    create_flight_plan,
    get_checklist,
    get_sim_state,
    lookup_airport,
    search_manual,
)


# ---------------------------------------------------------------------------
# get_sim_state
# ---------------------------------------------------------------------------


class TestGetSimState:
    """Test formatted sim state retrieval."""

    @pytest.mark.asyncio
    async def test_returns_formatted_dict(self, sim_state_cruise: SimState) -> None:
        mock_client = MagicMock(spec=SimConnectClient)
        mock_client.get_state = AsyncMock(return_value=sim_state_cruise)

        result = await get_sim_state(mock_client)

        assert result["aircraft"] == "Cessna 172 Skyhawk"
        assert result["flight_phase"] == "CRUISE"
        assert result["on_ground"] is False
        assert result["position"]["altitude_msl"] == 6500
        assert result["position"]["altitude_agl"] == 6400
        assert result["speeds"]["indicated"] == 120
        assert result["autopilot"]["engaged"] is True

    @pytest.mark.asyncio
    async def test_position_rounding(self) -> None:
        state = SimState(
            position=Position(latitude=28.429412345, longitude=-81.30912345, altitude=6543.7, altitude_agl=6443.2),
        )
        mock_client = MagicMock(spec=SimConnectClient)
        mock_client.get_state = AsyncMock(return_value=state)
        result = await get_sim_state(mock_client)
        assert result["position"]["lat"] == pytest.approx(28.429412, abs=1e-6)
        assert result["position"]["altitude_msl"] == 6544

    @pytest.mark.asyncio
    async def test_engine_params_formatting(self) -> None:
        state = SimState(
            engine=EngineParams(rpm=[2412.6], fuel_flow=[9.37], oil_temp=[192.4], oil_pressure=[61.8]),
        )
        mock_client = MagicMock(spec=SimConnectClient)
        mock_client.get_state = AsyncMock(return_value=state)
        result = await get_sim_state(mock_client)
        assert result["engine"]["rpm"] == [2413]
        assert result["engine"]["fuel_flow"] == [9.4]
        assert result["engine"]["oil_temp"] == [192]

    @pytest.mark.asyncio
    async def test_fuel_formatting(self) -> None:
        state = SimState(fuel=FuelState(total=42.37, total_weight=252.22))
        mock_client = MagicMock(spec=SimConnectClient)
        mock_client.get_state = AsyncMock(return_value=state)
        result = await get_sim_state(mock_client)
        assert result["fuel"]["total_gallons"] == pytest.approx(42.4, abs=0.1)
        assert result["fuel"]["total_weight_lbs"] == pytest.approx(252.2, abs=0.1)

    @pytest.mark.asyncio
    async def test_environment_wind_string(self) -> None:
        state = SimState(
            environment=Environment(wind_direction=270.4, wind_speed=12.3),
        )
        mock_client = MagicMock(spec=SimConnectClient)
        mock_client.get_state = AsyncMock(return_value=state)
        result = await get_sim_state(mock_client)
        assert result["environment"]["wind"] == "270° at 12kt"

    @pytest.mark.asyncio
    async def test_surfaces_state(self, sim_state_approach: SimState) -> None:
        mock_client = MagicMock(spec=SimConnectClient)
        mock_client.get_state = AsyncMock(return_value=sim_state_approach)
        result = await get_sim_state(mock_client)
        assert result["surfaces"]["gear_down"] is True
        assert result["surfaces"]["flaps"] == 2
        assert result["surfaces"]["spoilers"] is False


# ---------------------------------------------------------------------------
# lookup_airport
# ---------------------------------------------------------------------------


class TestLookupAirport:
    """Test airport lookup with mocked HTTP responses."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_lookup_icao(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KJFK"}).mock(
            return_value=httpx.Response(200, json={
                "KJFK": [{
                    "facility_name": "JOHN F KENNEDY INTL",
                    "city": "NEW YORK",
                    "state_full": "NEW YORK",
                    "elevation": "13",
                    "latitude": "40.6413",
                    "longitude": "-73.7781",
                    "status_code": "O",
                }],
            })
        )
        result = await lookup_airport("KJFK")
        assert result["identifier"] == "KJFK"
        assert result["name"] == "JOHN F KENNEDY INTL"
        assert result["city"] == "NEW YORK"

    @pytest.mark.asyncio
    @respx.mock
    async def test_three_letter_code_gets_k_prefix(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KJFK"}).mock(
            return_value=httpx.Response(200, json={
                "KJFK": [{"facility_name": "JOHN F KENNEDY INTL"}],
            })
        )
        result = await lookup_airport("JFK")
        assert result["identifier"] == "KJFK"

    @pytest.mark.asyncio
    @respx.mock
    async def test_four_letter_code_starting_with_k_no_prefix(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KLAX"}).mock(
            return_value=httpx.Response(200, json={
                "KLAX": [{"facility_name": "LOS ANGELES INTL"}],
            })
        )
        result = await lookup_airport("KLAX")
        assert result["identifier"] == "KLAX"

    @pytest.mark.asyncio
    @respx.mock
    async def test_airport_not_found(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KZZZ"}).mock(
            return_value=httpx.Response(200, json={"KZZZ": []})
        )
        result = await lookup_airport("KZZZ")
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_airport_identifier_missing_from_response(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KXYZ"}).mock(
            return_value=httpx.Response(200, json={})
        )
        result = await lookup_airport("KXYZ")
        assert "error" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_returns_error_dict(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KJFK"}).mock(
            return_value=httpx.Response(500)
        )
        result = await lookup_airport("KJFK")
        assert "error" in result
        assert "Lookup failed" in result["error"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_whitespace_and_case_normalization(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KJFK"}).mock(
            return_value=httpx.Response(200, json={"KJFK": [{"facility_name": "JFK"}]})
        )
        result = await lookup_airport("  kjfk  ")
        assert result["identifier"] == "KJFK"

    @pytest.mark.asyncio
    @respx.mock
    async def test_dict_response_instead_of_list(self) -> None:
        """API sometimes returns a dict instead of a list for the airport."""
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KJFK"}).mock(
            return_value=httpx.Response(200, json={
                "KJFK": {"facility_name": "JOHN F KENNEDY INTL", "city": "NEW YORK"},
            })
        )
        result = await lookup_airport("KJFK")
        assert result["name"] == "JOHN F KENNEDY INTL"


# ---------------------------------------------------------------------------
# search_manual
# ---------------------------------------------------------------------------


class TestSearchManual:
    """Test manual search with mocked context store."""

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[
            {"content": "V-speeds for C172: Vr=55, Vx=62, Vy=74", "metadata": {"source": "poh.pdf"}, "distance": 0.1},
            {"content": "Normal climb: 75-85 KIAS", "metadata": {"source": "poh.pdf"}, "distance": 0.2},
        ])

        result = await search_manual("V-speeds", mock_store)
        assert len(result) == 2
        assert result[0]["content"] == "V-speeds for C172: Vr=55, Vx=62, Vy=74"
        assert result[0]["source"] == "poh.pdf"

    @pytest.mark.asyncio
    async def test_passes_aircraft_type_filter(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        await search_manual("V-speeds", mock_store, aircraft_type="Cessna 172")
        mock_store.query.assert_awaited_once()
        call_kwargs = mock_store.query.call_args[1]
        assert call_kwargs["filters"] == {"aircraft_type": "Cessna 172"}

    @pytest.mark.asyncio
    async def test_no_aircraft_type_passes_no_filter(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        await search_manual("emergency procedures", mock_store, aircraft_type="")
        call_kwargs = mock_store.query.call_args[1]
        assert call_kwargs["filters"] is None

    @pytest.mark.asyncio
    async def test_custom_n_results(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        await search_manual("stall", mock_store, n_results=3)
        call_kwargs = mock_store.query.call_args[1]
        assert call_kwargs["n_results"] == 3

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await search_manual("nonexistent topic", mock_store)
        assert result == []


# ---------------------------------------------------------------------------
# get_checklist
# ---------------------------------------------------------------------------


class TestGetChecklist:
    """Test checklist retrieval and phase filtering."""

    @pytest.mark.asyncio
    async def test_default_checklist_preflight(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_checklist("PREFLIGHT", mock_store)
        assert result["phase"] == "PREFLIGHT"
        assert result["source"] == "default"
        assert "items" in result
        assert len(result["items"]) > 0

    @pytest.mark.asyncio
    async def test_default_checklist_all_phases(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        for phase in FlightPhase:
            result = await get_checklist(phase.value, mock_store)
            assert result["phase"] == phase.value

    @pytest.mark.asyncio
    async def test_case_insensitive_phase(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_checklist("preflight", mock_store)
        assert result["phase"] == "PREFLIGHT"

    @pytest.mark.asyncio
    async def test_invalid_phase_returns_error(self) -> None:
        mock_store = MagicMock()
        result = await get_checklist("HOVERING", mock_store)
        assert "error" in result
        assert "Unknown flight phase" in result["error"]

    @pytest.mark.asyncio
    async def test_aircraft_specific_checklist_from_store(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[
            {"content": "C172 Preflight: 1. Check fuel...", "metadata": {"source": "c172_checklist.pdf"}},
        ])

        result = await get_checklist("PREFLIGHT", mock_store, aircraft_type="Cessna 172")
        assert result["source"] == "aircraft_manual"
        assert result["aircraft"] == "Cessna 172"
        assert "C172 Preflight" in result["checklist"]

    @pytest.mark.asyncio
    async def test_fallback_to_default_when_store_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_checklist("TAKEOFF", mock_store, aircraft_type="Cessna 172")
        assert result["source"] == "default"
        assert result["aircraft"] == "Cessna 172"

    @pytest.mark.asyncio
    async def test_accepts_flight_phase_enum_directly(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_checklist(FlightPhase.CRUISE, mock_store)
        assert result["phase"] == "CRUISE"

    @pytest.mark.asyncio
    async def test_generic_aircraft_when_none_specified(self) -> None:
        mock_store = MagicMock()
        mock_store.query = AsyncMock(return_value=[])

        result = await get_checklist("LANDED", mock_store, aircraft_type="")
        assert result["aircraft"] == "generic"

    def test_default_checklists_cover_all_phases(self) -> None:
        for phase in FlightPhase:
            assert phase in DEFAULT_CHECKLISTS, f"Missing default checklist for {phase.value}"


# ---------------------------------------------------------------------------
# create_flight_plan
# ---------------------------------------------------------------------------


class TestCreateFlightPlan:
    """Test flight plan creation with mocked airport lookups."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_basic_flight_plan(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KJFK"}).mock(
            return_value=httpx.Response(200, json={"KJFK": [{"facility_name": "JFK INTL"}]})
        )
        respx.get("https://api.aviationapi.com/v1/airports", params={"apt": "KLAX"}).mock(
            return_value=httpx.Response(200, json={"KLAX": [{"facility_name": "LAX INTL"}]})
        )

        result = await create_flight_plan("KJFK", "KLAX")
        assert result["departure"]["name"] == "JFK INTL"
        assert result["destination"]["name"] == "LAX INTL"
        assert result["cruise_altitude"] == 5000
        assert result["status"] == "draft"
        assert result["waypoints"] == ["KJFK", "KLAX"]
        assert result["route"] == "KJFK KLAX"

    @pytest.mark.asyncio
    @respx.mock
    async def test_flight_plan_with_route(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports").mock(
            return_value=httpx.Response(200, json={})
        )

        result = await create_flight_plan("KJFK", "KLAX", altitude=35000, route="MERIT J80 BOS")
        assert result["cruise_altitude"] == 35000
        assert result["waypoints"] == ["KJFK", "MERIT", "J80", "BOS", "KLAX"]
        assert result["route"] == "KJFK MERIT J80 BOS KLAX"

    @pytest.mark.asyncio
    @respx.mock
    async def test_flight_plan_normalizes_identifiers(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports").mock(
            return_value=httpx.Response(200, json={})
        )

        result = await create_flight_plan("kjfk", "klax")
        assert result["waypoints"][0] == "KJFK"
        assert result["waypoints"][-1] == "KLAX"

    @pytest.mark.asyncio
    @respx.mock
    async def test_flight_plan_includes_notes(self) -> None:
        respx.get("https://api.aviationapi.com/v1/airports").mock(
            return_value=httpx.Response(200, json={})
        )

        result = await create_flight_plan("KJFK", "KLAX")
        assert "draft" in result["notes"].lower() or "verify" in result["notes"].lower()
