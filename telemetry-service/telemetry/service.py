"""FastAPI telemetry service with adapter ingest and consumer broadcast."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .adapter_manager import AdapterManager
from .adapter_protocol import (
    ServiceError,
    ServiceHeartbeatAck,
    ServiceStateResponse,
    ServiceSubscribeAck,
    parse_adapter_message,
    parse_consumer_message,
)
from .config import TelemetryServiceSettings, load_settings
from .persistence import StatePersistence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

settings: TelemetryServiceSettings = load_settings()
manager = AdapterManager(stale_timeout=settings.stale_adapter_timeout)
persistence = StatePersistence(
    path=Path(settings.state_path),
    write_interval=settings.state_write_interval,
)

# Registration timeout (seconds). Module-level for easy test patching.
REGISTER_TIMEOUT: float = 5.0


# ---------------------------------------------------------------------------
# Lifespan -- background tasks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background cleanup task."""
    logger.info("Telemetry service starting")

    async def _cleanup_loop() -> None:
        while True:
            await asyncio.sleep(5.0)
            await manager.cleanup_stale_adapters()

    cleanup_task = asyncio.create_task(_cleanup_loop())

    # Restore persisted state so get_current_state works before adapters connect
    restored = persistence.load()
    if restored:
        manager.set_restored_state(restored)
        logger.info("Restored persisted telemetry state from %s", settings.state_path)

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Telemetry service stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Super Hornet Telemetry Service",
    description="Universal telemetry hub for sim adapters and consumers",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "adapters": manager.adapter_count,
        "consumers": manager.consumer_count,
    }


@app.get("/api/adapters")
async def list_adapters():
    return {"adapters": manager.get_active_adapters()}


# ---------------------------------------------------------------------------
# WebSocket: /ws/ingest -- adapter connections
# ---------------------------------------------------------------------------


@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    """Adapter ingest endpoint.

    Protocol:
    1. Adapter connects and sends a ``register`` message within 5s.
    2. Service responds with ``register_ack``.
    3. Adapter streams ``telemetry`` and ``status`` messages.
    4. On disconnect, adapter is unregistered.
    """
    await ws.accept()
    adapter_id: str | None = None

    try:
        # Wait for registration
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=REGISTER_TIMEOUT)
        except asyncio.TimeoutError:
            await ws.send_text(
                ServiceError(
                    message="Registration timeout: send a register message within 5s"
                ).model_dump_json()
            )
            await ws.close(code=4001, reason="Registration timeout")
            return

        data = json.loads(raw)
        msg = parse_adapter_message(data)

        if msg is None or msg.type != "register":
            await ws.send_text(
                ServiceError(message="First message must be type 'register'").model_dump_json()
            )
            await ws.close(code=4002, reason="Invalid registration")
            return

        # Register the adapter
        ack = await manager.register_adapter(
            ws=ws,
            adapter_id=msg.adapter_id,
            sim_name=msg.sim_name,
            vehicle_type=msg.vehicle_type,
            version=msg.version,
        )
        adapter_id = msg.adapter_id
        await ws.send_text(ack.model_dump_json())

        # Stream telemetry
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg = parse_adapter_message(data)

            if msg is None:
                continue

            if msg.type == "telemetry":
                await manager.update_telemetry(adapter_id, msg.data)
                await persistence.save(msg.data)
            elif msg.type == "status":
                await manager.update_adapter_status(adapter_id, msg.connected, msg.vehicle_name)

    except WebSocketDisconnect:
        logger.info("Adapter disconnected: %s", adapter_id or "unknown")
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON from adapter %s: %s", adapter_id, exc)
    except Exception as exc:
        logger.warning("Adapter connection error (%s): %s", adapter_id, exc)
    finally:
        if adapter_id:
            await manager.unregister_adapter(adapter_id)


# ---------------------------------------------------------------------------
# WebSocket: /ws/telemetry -- consumer connections
# ---------------------------------------------------------------------------


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """Consumer telemetry endpoint.

    Broadcasts telemetry from all active adapters. Supports subscribe,
    get_state, and heartbeat messages.
    """
    await ws.accept()
    consumer = await manager.add_consumer(ws)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(ServiceError(message="Invalid JSON").model_dump_json())
                continue

            msg = parse_consumer_message(data)
            if msg is None:
                msg_type = data.get("type")
                if msg_type:
                    await ws.send_text(
                        ServiceError(message=f"Unknown request type: {msg_type}").model_dump_json()
                    )
                continue

            if msg.type == "subscribe":
                fields = msg.fields if msg.fields else None
                manager.set_consumer_subscription(consumer, fields)
                ack = ServiceSubscribeAck(fields=msg.fields or ["all"])
                await ws.send_text(ack.model_dump_json())

            elif msg.type == "get_state":
                current = manager.get_current_state()
                if current:
                    await ws.send_text(current.model_dump_json())
                else:
                    resp = ServiceStateResponse(message="No adapter data available yet.")
                    await ws.send_text(resp.model_dump_json())

            elif msg.type == "heartbeat":
                ack = ServiceHeartbeatAck(
                    timestamp=datetime.now(UTC).isoformat(),
                    clients=manager.consumer_count,
                    adapters=manager.adapter_count,
                )
                await ws.send_text(ack.model_dump_json())

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("Consumer connection error: %s", exc)
    finally:
        await manager.remove_consumer(consumer)
