"""Tracks connected adapters and manages telemetry broadcasting to consumers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .adapter_protocol import ServiceRegisterAck
from .schema import TelemetryEnvelope

logger = logging.getLogger(__name__)


@dataclass
class AdapterConnection:
    """Tracks a single connected adapter."""

    adapter_id: str
    sim_name: str
    vehicle_type: str
    version: str
    websocket: WebSocket
    registered_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    last_state: TelemetryEnvelope | None = None
    frames_received: int = 0


@dataclass
class ConsumerConnection:
    """Tracks a single connected consumer."""

    websocket: WebSocket
    subscribed_fields: list[str] | None = None
    messages_sent: int = 0


class AdapterManager:
    """Central manager for adapter connections and consumer broadcasting."""

    def __init__(self, stale_timeout: float = 15.0) -> None:
        self._adapters: dict[str, AdapterConnection] = {}
        self._consumers: list[ConsumerConnection] = []
        self._stale_timeout = stale_timeout
        self._lock = asyncio.Lock()
        self._consumer_lock = asyncio.Lock()
        self._last_broadcast_hash: str = ""
        self._restored_state: TelemetryEnvelope | None = None

    # -------------------------------------------------------------------
    # Adapter management
    # -------------------------------------------------------------------

    async def register_adapter(
        self,
        ws: WebSocket,
        adapter_id: str,
        sim_name: str,
        vehicle_type: str,
        version: str = "1.0",
    ) -> ServiceRegisterAck:
        """Register a new adapter connection."""
        async with self._lock:
            if adapter_id in self._adapters:
                logger.info(
                    "Adapter '%s' reconnected, replacing old connection",
                    adapter_id,
                )

            conn = AdapterConnection(
                adapter_id=adapter_id,
                sim_name=sim_name,
                vehicle_type=vehicle_type,
                version=version,
                websocket=ws,
            )
            self._adapters[adapter_id] = conn

        logger.info(
            "Adapter registered: %s (sim=%s, vehicle=%s, v%s)",
            adapter_id,
            sim_name,
            vehicle_type,
            version,
        )
        return ServiceRegisterAck(adapter_id=adapter_id, accepted=True)

    async def unregister_adapter(self, adapter_id: str) -> None:
        """Remove an adapter connection."""
        async with self._lock:
            removed = self._adapters.pop(adapter_id, None)
        if removed:
            logger.info(
                "Adapter unregistered: %s (received %d frames)",
                adapter_id,
                removed.frames_received,
            )

    async def update_telemetry(self, adapter_id: str, envelope: TelemetryEnvelope) -> None:
        """Update the latest telemetry from an adapter and broadcast."""
        async with self._lock:
            conn = self._adapters.get(adapter_id)
            if conn is None:
                logger.warning("Telemetry from unregistered adapter: %s", adapter_id)
                return
            conn.last_state = envelope
            conn.last_seen = time.monotonic()
            conn.frames_received += 1

        await self._broadcast_to_consumers(envelope)

    async def update_adapter_status(
        self, adapter_id: str, connected: bool, vehicle_name: str = ""
    ) -> None:
        """Update adapter status from a status heartbeat."""
        async with self._lock:
            conn = self._adapters.get(adapter_id)
            if conn is None:
                return
            conn.last_seen = time.monotonic()
            if conn.last_state:
                conn.last_state.connected = connected
                conn.last_state.vehicle_name = vehicle_name

    def get_active_adapters(self) -> list[dict[str, Any]]:
        """Return info about all non-stale adapters."""
        now = time.monotonic()
        result = []
        for conn in self._adapters.values():
            age = now - conn.last_seen
            result.append(
                {
                    "adapter_id": conn.adapter_id,
                    "sim_name": conn.sim_name,
                    "vehicle_type": conn.vehicle_type,
                    "version": conn.version,
                    "connected": conn.last_state.connected if conn.last_state else False,
                    "vehicle_name": conn.last_state.vehicle_name if conn.last_state else "",
                    "frames_received": conn.frames_received,
                    "last_seen_seconds_ago": round(age, 1),
                    "stale": age > self._stale_timeout,
                }
            )
        return result

    def set_restored_state(self, envelope: TelemetryEnvelope) -> None:
        """Set a restored state from persistence as initial fallback."""
        self._restored_state = envelope

    def get_current_state(self) -> TelemetryEnvelope | None:
        """Return the most recent telemetry from any active adapter.

        Falls back to restored state from persistence if no active adapter
        has sent telemetry yet.
        """
        best: AdapterConnection | None = None
        for conn in self._adapters.values():
            if conn.last_state is None:
                continue
            if best is None or conn.last_seen > best.last_seen:
                best = conn
        if best:
            return best.last_state
        return self._restored_state

    @property
    def adapter_count(self) -> int:
        return len(self._adapters)

    @property
    def consumer_count(self) -> int:
        return len(self._consumers)

    # -------------------------------------------------------------------
    # Stale adapter cleanup
    # -------------------------------------------------------------------

    async def cleanup_stale_adapters(self) -> None:
        """Remove adapters that haven't sent data recently."""
        now = time.monotonic()
        stale_ids = []
        async with self._lock:
            for adapter_id, conn in self._adapters.items():
                if now - conn.last_seen > self._stale_timeout:
                    stale_ids.append(adapter_id)
            for adapter_id in stale_ids:
                self._adapters.pop(adapter_id, None)

        for adapter_id in stale_ids:
            logger.warning("Removed stale adapter: %s", adapter_id)

    # -------------------------------------------------------------------
    # Consumer management
    # -------------------------------------------------------------------

    async def add_consumer(self, ws: WebSocket) -> ConsumerConnection:
        """Register a new consumer connection."""
        conn = ConsumerConnection(websocket=ws)
        async with self._consumer_lock:
            self._consumers.append(conn)
        logger.info("Consumer connected [total: %d]", len(self._consumers))
        return conn

    async def remove_consumer(self, conn: ConsumerConnection) -> None:
        """Remove a consumer connection."""
        async with self._consumer_lock:
            try:
                self._consumers.remove(conn)
            except ValueError:
                pass
        logger.info(
            "Consumer disconnected (sent %d msgs) [remaining: %d]",
            conn.messages_sent,
            len(self._consumers),
        )

    def set_consumer_subscription(
        self, conn: ConsumerConnection, fields: list[str] | None
    ) -> None:
        """Set field filter for a consumer."""
        conn.subscribed_fields = fields
        logger.info(
            "Consumer subscribed to: %s",
            "all" if not fields else ", ".join(fields),
        )

    # -------------------------------------------------------------------
    # Broadcasting
    # -------------------------------------------------------------------

    async def _broadcast_to_consumers(self, envelope: TelemetryEnvelope) -> None:
        """Send telemetry to all connected consumers with delta detection."""
        async with self._consumer_lock:
            if not self._consumers:
                return

            # Delta detection: skip broadcast if payload unchanged
            hash_data = envelope.model_dump(mode="json")
            hash_data.pop("timestamp", None)
            hash_data.pop("adapter_id", None)
            hash_input = json.dumps(hash_data, sort_keys=True)
            payload_hash = hashlib.md5(hash_input.encode()).hexdigest()
            if payload_hash == self._last_broadcast_hash:
                return
            self._last_broadcast_hash = payload_hash

            # Send full TelemetryEnvelope JSON (not legacy SimState)
            full_data = envelope.model_dump(mode="json")
            full_json: str | None = None
            dead_consumers: list[ConsumerConnection] = []

            for consumer in self._consumers:
                try:
                    if consumer.subscribed_fields:
                        filtered = self._filter_state(full_data, consumer.subscribed_fields)
                        await consumer.websocket.send_text(json.dumps(filtered))
                    else:
                        if full_json is None:
                            full_json = json.dumps(full_data)
                        await consumer.websocket.send_text(full_json)
                    consumer.messages_sent += 1
                except Exception:
                    dead_consumers.append(consumer)

            for consumer in dead_consumers:
                try:
                    self._consumers.remove(consumer)
                except ValueError:
                    pass

    @staticmethod
    def _filter_state(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        """Filter telemetry to only requested top-level fields."""
        result: dict[str, Any] = {
            "timestamp": data.get("timestamp"),
            "connected": data.get("connected"),
        }
        for field_name in fields:
            key = field_name.lower()
            if key in data:
                result[key] = data[key]
        return result
