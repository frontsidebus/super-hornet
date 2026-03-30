"""Tests for telemetry schema models."""

from __future__ import annotations

from telemetry.adapter_protocol import (
    AdapterRegister,
    AdapterTelemetry,
    parse_adapter_message,
)
from telemetry.schema import (
    Attitude,
    Environment,
    Position,
    ShieldState,
    SpacecraftExtensions,
    Speeds,
    TelemetryEnvelope,
)


class TestTelemetryEnvelope:
    """Tests for the TelemetryEnvelope model."""

    def test_envelope_round_trips_through_json(
        self, sample_sc_envelope: TelemetryEnvelope
    ) -> None:
        """Envelope serializes to JSON and deserializes back identically."""
        dumped = sample_sc_envelope.model_dump()
        restored = TelemetryEnvelope.model_validate(dumped)
        assert restored.adapter_id == sample_sc_envelope.adapter_id
        assert restored.sim_name == sample_sc_envelope.sim_name
        assert restored.vehicle_name == sample_sc_envelope.vehicle_name
        assert restored.connected is True

    def test_core_fields_serialize_correctly(self) -> None:
        """Position, attitude, speeds, environment serialize correctly."""
        envelope = TelemetryEnvelope(
            position=Position(latitude=1.0, longitude=2.0, altitude_msl=3.0),
            attitude=Attitude(pitch=10.0, bank=20.0, heading_true=180.0),
            speeds=Speeds(ground_speed=500.0, vertical_speed=10.0),
            environment=Environment(temperature_c=-50.0, visibility_sm=10.0),
        )
        dumped = envelope.model_dump()
        assert dumped["position"]["latitude"] == 1.0
        assert dumped["attitude"]["pitch"] == 10.0
        assert dumped["speeds"]["ground_speed"] == 500.0
        assert dumped["environment"]["temperature_c"] == -50.0

    def test_spacecraft_property_returns_none_when_absent(self) -> None:
        """spacecraft property returns None when no spacecraft extensions set."""
        envelope = TelemetryEnvelope()
        assert envelope.spacecraft is None

    def test_spacecraft_property_parses_extensions(
        self, sample_sc_envelope: TelemetryEnvelope
    ) -> None:
        """spacecraft property parses SpacecraftExtensions from extensions dict."""
        sc = sample_sc_envelope.spacecraft
        assert sc is not None
        assert sc.shields is not None
        assert sc.shields.front == 80.0
        assert sc.quantum_drive is not None
        assert sc.quantum_drive.fuel_percent == 75.0
        assert sc.weapons is not None
        assert sc.weapons.armed is True
        assert sc.weapons.missiles_remaining == 4

    def test_with_spacecraft_extensions_sets_dict(
        self, sample_spacecraft_extensions: SpacecraftExtensions
    ) -> None:
        """with_spacecraft_extensions populates extensions['spacecraft']."""
        envelope = TelemetryEnvelope()
        result = envelope.with_spacecraft_extensions(sample_spacecraft_extensions)
        assert "spacecraft" in result.extensions
        assert result.extensions["spacecraft"]["hull_percent"] == 100.0


class TestSpacecraftExtensions:
    """Tests for SpacecraftExtensions and sub-models."""

    def test_full_extensions_round_trip(
        self, sample_spacecraft_extensions: SpacecraftExtensions
    ) -> None:
        """Full SpacecraftExtensions round-trips through JSON."""
        dumped = sample_spacecraft_extensions.model_dump()
        restored = SpacecraftExtensions.model_validate(dumped)
        assert restored.shields is not None
        assert restored.shields.front == 80.0
        assert restored.quantum_drive is not None
        assert restored.quantum_drive.fuel_percent == 75.0
        assert restored.player is not None
        assert restored.player.system == "Stanton"

    def test_shield_average_computation(self) -> None:
        """ShieldState.average computes mean of four faces."""
        shields = ShieldState(front=80.0, rear=60.0, left=100.0, right=100.0)
        assert shields.average == 85.0

    def test_default_hull_percent(self) -> None:
        """Default SpacecraftExtensions has hull_percent=100.0."""
        ext = SpacecraftExtensions()
        assert ext.hull_percent == 100.0

    def test_partial_extensions_serialize(self) -> None:
        """Partial extensions (only shields) serialize with None for others."""
        ext = SpacecraftExtensions(
            shields=ShieldState(front=50.0),
        )
        dumped = ext.model_dump()
        assert dumped["shields"]["front"] == 50.0
        assert dumped["quantum_drive"] is None
        assert dumped["weapons"] is None
        assert dumped["power"] is None
        assert dumped["navigation"] is None
        assert dumped["player"] is None
        assert dumped["combat"] is None


class TestAdapterProtocol:
    """Tests for adapter protocol message parsing."""

    def test_parse_register_message(self) -> None:
        """parse_adapter_message with type='register' returns AdapterRegister."""
        data = {
            "type": "register",
            "adapter_id": "sc-01",
            "sim_name": "star_citizen",
            "vehicle_type": "spacecraft",
        }
        msg = parse_adapter_message(data)
        assert isinstance(msg, AdapterRegister)
        assert msg.adapter_id == "sc-01"
        assert msg.sim_name == "star_citizen"

    def test_parse_telemetry_message(self) -> None:
        """parse_adapter_message with type='telemetry' returns AdapterTelemetry."""
        data = {
            "type": "telemetry",
            "data": {
                "adapter_id": "sc-01",
                "sim_name": "star_citizen",
                "vehicle_type": "spacecraft",
                "timestamp": "2025-01-01T00:00:00Z",
                "connected": True,
                "vehicle_name": "Super Hornet",
            },
        }
        msg = parse_adapter_message(data)
        assert isinstance(msg, AdapterTelemetry)
        assert msg.data.vehicle_name == "Super Hornet"

    def test_parse_unknown_type_returns_none(self) -> None:
        """parse_adapter_message with unknown type returns None."""
        data = {"type": "unknown_msg", "foo": "bar"}
        msg = parse_adapter_message(data)
        assert msg is None
