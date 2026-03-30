"""Shared test fixtures for telemetry service tests."""

from __future__ import annotations

import pytest

from telemetry.schema import (
    CombatTelemetryState,
    NavigationState,
    PlayerLocationState,
    PowerState,
    QuantumDriveState,
    ShieldState,
    SpacecraftExtensions,
    TelemetryEnvelope,
    WeaponState,
)


@pytest.fixture
def sample_spacecraft_extensions() -> SpacecraftExtensions:
    """Full SpacecraftExtensions with realistic Star Citizen data."""
    return SpacecraftExtensions(
        shields=ShieldState(
            front=80.0,
            rear=60.0,
            left=100.0,
            right=100.0,
            up=True,
        ),
        quantum_drive=QuantumDriveState(fuel_percent=75.0),
        weapons=WeaponState(armed=True, missiles_remaining=4),
        power=PowerState(on=True, hydrogen_fuel_percent=90.0),
        navigation=NavigationState(speed_scm=250.0, speed_max=1200.0),
        player=PlayerLocationState(
            system="Stanton",
            body="Hurston",
            zone="Lorville",
            in_ship=True,
            crime_stat=0,
        ),
        combat=CombatTelemetryState(under_attack=False, hostile_count=2),
    )


@pytest.fixture
def sample_sc_envelope(
    sample_spacecraft_extensions: SpacecraftExtensions,
) -> TelemetryEnvelope:
    """TelemetryEnvelope configured for Star Citizen."""
    envelope = TelemetryEnvelope(
        adapter_id="sc-adapter-01",
        sim_name="star_citizen",
        vehicle_type="spacecraft",
        timestamp="2025-01-01T00:00:00Z",
        connected=True,
        vehicle_name="F7C-M Super Hornet",
    )
    envelope.with_spacecraft_extensions(sample_spacecraft_extensions)
    return envelope
