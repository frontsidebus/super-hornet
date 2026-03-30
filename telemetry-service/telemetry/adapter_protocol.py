"""Adapter <-> telemetry service WebSocket protocol messages.

Adapters connect to the service's ingest endpoint and exchange these
message types. All messages are JSON with a ``type`` discriminator field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .schema import TelemetryEnvelope

# ---------------------------------------------------------------------------
# Adapter -> Service messages
# ---------------------------------------------------------------------------


class AdapterRegister(BaseModel):
    """Sent by the adapter immediately after connecting."""

    type: Literal["register"] = "register"
    adapter_id: str
    sim_name: str  # e.g. "star_citizen", "msfs2024", "xplane12"
    vehicle_type: str = "aircraft"  # "aircraft", "spacecraft", etc.
    version: str = "1.0"


class AdapterTelemetry(BaseModel):
    """Sent by the adapter on each telemetry frame."""

    type: Literal["telemetry"] = "telemetry"
    data: TelemetryEnvelope


class AdapterStatus(BaseModel):
    """Periodic status heartbeat from the adapter."""

    type: Literal["status"] = "status"
    connected: bool = False
    vehicle_name: str = ""


# ---------------------------------------------------------------------------
# Service -> Adapter messages
# ---------------------------------------------------------------------------


class ServiceRegisterAck(BaseModel):
    """Sent by the service after successful adapter registration."""

    type: Literal["register_ack"] = "register_ack"
    adapter_id: str
    accepted: bool = True
    message: str = ""


class ServiceError(BaseModel):
    """Sent by the service when an adapter message is invalid."""

    type: Literal["error"] = "error"
    message: str


# ---------------------------------------------------------------------------
# Consumer -> Service messages (on the /ws/telemetry endpoint)
# ---------------------------------------------------------------------------


class ConsumerSubscribe(BaseModel):
    """Consumer requests to receive only specific fields."""

    type: Literal["subscribe"] = "subscribe"
    fields: list[str] = Field(default_factory=list)


class ConsumerGetState(BaseModel):
    """Consumer requests the current state immediately."""

    type: Literal["get_state"] = "get_state"


class ConsumerHeartbeat(BaseModel):
    """Consumer heartbeat ping."""

    type: Literal["heartbeat"] = "heartbeat"


# ---------------------------------------------------------------------------
# Service -> Consumer messages
# ---------------------------------------------------------------------------


class ServiceSubscribeAck(BaseModel):
    type: Literal["subscribe_ack"] = "subscribe_ack"
    fields: list[str] = Field(default_factory=list)


class ServiceHeartbeatAck(BaseModel):
    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    timestamp: str = ""
    clients: int = 0
    adapters: int = 0


class ServiceStateResponse(BaseModel):
    type: Literal["state_response"] = "state_response"
    message: str = "Full state will be delivered on next update cycle."


# ---------------------------------------------------------------------------
# Helper to parse incoming messages
# ---------------------------------------------------------------------------


def parse_adapter_message(
    data: dict[str, Any],
) -> AdapterRegister | AdapterTelemetry | AdapterStatus | None:
    """Parse a raw JSON dict into a typed adapter message."""
    msg_type = data.get("type")
    if msg_type == "register":
        return AdapterRegister.model_validate(data)
    if msg_type == "telemetry":
        return AdapterTelemetry.model_validate(data)
    if msg_type == "status":
        return AdapterStatus.model_validate(data)
    return None


def parse_consumer_message(
    data: dict[str, Any],
) -> ConsumerSubscribe | ConsumerGetState | ConsumerHeartbeat | None:
    """Parse a raw JSON dict into a typed consumer message."""
    msg_type = data.get("type")
    if msg_type == "subscribe":
        return ConsumerSubscribe.model_validate(data)
    if msg_type == "get_state":
        return ConsumerGetState.model_validate(data)
    if msg_type == "heartbeat":
        return ConsumerHeartbeat.model_validate(data)
    return None
