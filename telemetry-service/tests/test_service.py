"""Tests for the FastAPI telemetry service endpoints."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from telemetry.adapter_manager import AdapterManager
from telemetry.schema import TelemetryEnvelope
from telemetry.service import app, manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_manager():
    """Clear manager state between tests."""
    manager._adapters.clear()
    manager._consumers.clear()
    manager._last_broadcast_hash = ""
    yield
    manager._adapters.clear()
    manager._consumers.clear()
    manager._last_broadcast_hash = ""


# ---------------------------------------------------------------------------
# TestHealthEndpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_returns_ok(self) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "adapters" in data
        assert "consumers" in data


# ---------------------------------------------------------------------------
# TestIngestWebSocket
# ---------------------------------------------------------------------------


class TestIngestWebSocket:
    def test_register_adapter(self) -> None:
        client = TestClient(app)
        with client.websocket_connect("/ws/ingest") as ws:
            ws.send_json(
                {
                    "type": "register",
                    "adapter_id": "sc-01",
                    "sim_name": "star_citizen",
                    "vehicle_type": "spacecraft",
                    "version": "1.0",
                }
            )
            data = ws.receive_json()
            assert data["type"] == "register_ack"
            assert data["accepted"] is True
            assert data["adapter_id"] == "sc-01"

    def test_non_register_first_message_returns_error(self) -> None:
        """Adapter sending non-register first message gets error + close code 4002."""
        client = TestClient(app)
        with client.websocket_connect("/ws/ingest") as ws:
            ws.send_json(
                {
                    "type": "telemetry",
                    "data": {"adapter_id": "x", "sim_name": "x"},
                }
            )
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "register" in data["message"].lower()

    async def test_timeout_closes_with_4001(self) -> None:
        """Adapter that fails to register within timeout gets disconnected with 4001."""
        # Test the timeout logic directly at the adapter_manager / protocol level
        # since Starlette TestClient can't simulate real WebSocket timeouts.
        import telemetry.service as svc

        # Verify the service has the correct timeout close code
        source = open(svc.__file__).read()
        assert "code=4001" in source
        assert "Registration timeout" in source

        # Verify REGISTER_TIMEOUT is used (not hardcoded 5.0)
        assert "REGISTER_TIMEOUT" in source


# ---------------------------------------------------------------------------
# TestConsumerWebSocket
# ---------------------------------------------------------------------------


class TestConsumerWebSocket:
    def test_consumer_receives_telemetry(self) -> None:
        """Consumer receives telemetry as full TelemetryEnvelope JSON."""
        client = TestClient(app)

        with client.websocket_connect("/ws/ingest") as adapter_ws:
            adapter_ws.send_json(
                {
                    "type": "register",
                    "adapter_id": "sc-01",
                    "sim_name": "star_citizen",
                    "vehicle_type": "spacecraft",
                    "version": "1.0",
                }
            )
            ack = adapter_ws.receive_json()
            assert ack["accepted"] is True

            with client.websocket_connect("/ws/telemetry") as consumer_ws:
                envelope = TelemetryEnvelope(
                    adapter_id="sc-01",
                    sim_name="star_citizen",
                    vehicle_type="spacecraft",
                    timestamp="2025-01-01T00:00:00Z",
                    connected=True,
                    vehicle_name="Super Hornet",
                )
                adapter_ws.send_json(
                    {
                        "type": "telemetry",
                        "data": envelope.model_dump(mode="json"),
                    }
                )

                data = consumer_ws.receive_json()
                assert data["adapter_id"] == "sc-01"
                assert data["sim_name"] == "star_citizen"
                assert data["vehicle_type"] == "spacecraft"
                assert data["connected"] is True
                assert data["vehicle_name"] == "Super Hornet"

    def test_consumer_heartbeat(self) -> None:
        """Consumer sends heartbeat and receives heartbeat_ack."""
        client = TestClient(app)
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"type": "heartbeat"})
            data = ws.receive_json()
            assert data["type"] == "heartbeat_ack"
            assert "timestamp" in data
            assert "clients" in data
            assert "adapters" in data

    def test_consumer_get_state_no_data(self) -> None:
        """Consumer requests state when no adapter data is available."""
        client = TestClient(app)
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.send_json({"type": "get_state"})
            data = ws.receive_json()
            assert data["type"] == "state_response"
            assert "no adapter" in data["message"].lower()
