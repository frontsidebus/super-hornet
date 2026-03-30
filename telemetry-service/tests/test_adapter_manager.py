"""Tests for AdapterManager: registration, broadcast, delta detection, cleanup."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from telemetry.adapter_manager import AdapterManager
from telemetry.schema import Position, TelemetryEnvelope


# ---------------------------------------------------------------------------
# Mock WebSocket
# ---------------------------------------------------------------------------


class MockWebSocket:
    """Records sent messages and can optionally raise on send."""

    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.sent: list[str] = []
        self.fail_on_send = fail_on_send

    async def send_text(self, data: str) -> None:
        if self.fail_on_send:
            raise ConnectionError("WebSocket closed")
        self.sent.append(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(
    adapter_id: str = "test-adapter",
    timestamp: str = "2025-01-01T00:00:00Z",
    latitude: float = 10.0,
) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        adapter_id=adapter_id,
        sim_name="star_citizen",
        vehicle_type="spacecraft",
        timestamp=timestamp,
        connected=True,
        vehicle_name="Super Hornet",
        position=Position(latitude=latitude, longitude=20.0),
    )


# ---------------------------------------------------------------------------
# TestAdapterRegistration
# ---------------------------------------------------------------------------


class TestAdapterRegistration:
    @pytest.fixture
    def manager(self) -> AdapterManager:
        return AdapterManager(stale_timeout=15.0)

    async def test_register_adapter(self, manager: AdapterManager) -> None:
        ws = MockWebSocket()
        ack = await manager.register_adapter(
            ws=ws,
            adapter_id="sc-01",
            sim_name="star_citizen",
            vehicle_type="spacecraft",
            version="1.0",
        )
        assert ack.accepted is True
        assert ack.adapter_id == "sc-01"
        assert manager.adapter_count == 1

    async def test_unregister_adapter(self, manager: AdapterManager) -> None:
        ws = MockWebSocket()
        await manager.register_adapter(
            ws=ws, adapter_id="sc-01", sim_name="star_citizen", vehicle_type="spacecraft"
        )
        assert manager.adapter_count == 1
        await manager.unregister_adapter("sc-01")
        assert manager.adapter_count == 0

    async def test_reregister_replaces_old(self, manager: AdapterManager) -> None:
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await manager.register_adapter(
            ws=ws1, adapter_id="sc-01", sim_name="star_citizen", vehicle_type="spacecraft"
        )
        await manager.register_adapter(
            ws=ws2, adapter_id="sc-01", sim_name="star_citizen", vehicle_type="spacecraft"
        )
        assert manager.adapter_count == 1
        # The connection should now reference ws2, not ws1
        adapters = manager.get_active_adapters()
        assert len(adapters) == 1


# ---------------------------------------------------------------------------
# TestConsumerBroadcast
# ---------------------------------------------------------------------------


class TestConsumerBroadcast:
    @pytest.fixture
    def manager(self) -> AdapterManager:
        return AdapterManager(stale_timeout=15.0)

    async def test_consumer_receives_telemetry(self, manager: AdapterManager) -> None:
        # Register adapter
        adapter_ws = MockWebSocket()
        await manager.register_adapter(
            ws=adapter_ws,
            adapter_id="sc-01",
            sim_name="star_citizen",
            vehicle_type="spacecraft",
        )

        # Add consumer
        consumer_ws = MockWebSocket()
        await manager.add_consumer(consumer_ws)

        # Send telemetry
        envelope = _make_envelope(adapter_id="sc-01")
        await manager.update_telemetry("sc-01", envelope)

        # Consumer should have received exactly one message
        assert len(consumer_ws.sent) == 1
        received = json.loads(consumer_ws.sent[0])
        assert received["adapter_id"] == "sc-01"
        assert received["sim_name"] == "star_citizen"
        assert received["vehicle_type"] == "spacecraft"
        assert received["connected"] is True
        # Ensure it's full TelemetryEnvelope format (has position as object)
        assert "position" in received
        assert received["position"]["latitude"] == 10.0

    async def test_dead_consumer_removed(self, manager: AdapterManager) -> None:
        adapter_ws = MockWebSocket()
        await manager.register_adapter(
            ws=adapter_ws,
            adapter_id="sc-01",
            sim_name="star_citizen",
            vehicle_type="spacecraft",
        )

        # Add a consumer that will fail on send
        dead_ws = MockWebSocket(fail_on_send=True)
        await manager.add_consumer(dead_ws)
        assert manager.consumer_count == 1

        # Send telemetry -- should clean up dead consumer
        envelope = _make_envelope(adapter_id="sc-01")
        await manager.update_telemetry("sc-01", envelope)
        assert manager.consumer_count == 0


# ---------------------------------------------------------------------------
# TestDeltaDetection
# ---------------------------------------------------------------------------


class TestDeltaDetection:
    @pytest.fixture
    def manager(self) -> AdapterManager:
        return AdapterManager(stale_timeout=15.0)

    async def test_duplicate_frame_not_broadcast(self, manager: AdapterManager) -> None:
        adapter_ws = MockWebSocket()
        await manager.register_adapter(
            ws=adapter_ws,
            adapter_id="sc-01",
            sim_name="star_citizen",
            vehicle_type="spacecraft",
        )

        consumer_ws = MockWebSocket()
        await manager.add_consumer(consumer_ws)

        # Send same telemetry twice (only timestamp differs)
        env1 = _make_envelope(adapter_id="sc-01", timestamp="2025-01-01T00:00:00Z")
        env2 = _make_envelope(adapter_id="sc-01", timestamp="2025-01-01T00:00:01Z")

        await manager.update_telemetry("sc-01", env1)
        await manager.update_telemetry("sc-01", env2)

        # Consumer should only receive the first one
        assert len(consumer_ws.sent) == 1

    async def test_changed_frame_is_broadcast(self, manager: AdapterManager) -> None:
        adapter_ws = MockWebSocket()
        await manager.register_adapter(
            ws=adapter_ws,
            adapter_id="sc-01",
            sim_name="star_citizen",
            vehicle_type="spacecraft",
        )

        consumer_ws = MockWebSocket()
        await manager.add_consumer(consumer_ws)

        # Send different telemetry
        env1 = _make_envelope(adapter_id="sc-01", latitude=10.0)
        env2 = _make_envelope(adapter_id="sc-01", latitude=20.0)

        await manager.update_telemetry("sc-01", env1)
        await manager.update_telemetry("sc-01", env2)

        # Consumer should receive both
        assert len(consumer_ws.sent) == 2


# ---------------------------------------------------------------------------
# TestStaleCleanup
# ---------------------------------------------------------------------------


class TestStaleCleanup:
    async def test_stale_adapter_removed(self) -> None:
        manager = AdapterManager(stale_timeout=1.0)
        ws = MockWebSocket()
        await manager.register_adapter(
            ws=ws, adapter_id="sc-01", sim_name="star_citizen", vehicle_type="spacecraft"
        )
        assert manager.adapter_count == 1

        # Patch time.monotonic to simulate staleness
        original_last_seen = manager._adapters["sc-01"].last_seen
        with patch("time.monotonic", return_value=original_last_seen + 2.0):
            await manager.cleanup_stale_adapters()

        assert manager.adapter_count == 0
