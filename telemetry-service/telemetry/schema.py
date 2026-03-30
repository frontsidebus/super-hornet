"""Universal telemetry data models.

These models define the canonical telemetry schema that all adapters must
conform to. Core fields (position, attitude, speeds, environment) are
universal across vehicle types. Vehicle-specific data is carried in the
``extensions`` dict keyed by vehicle type (e.g. ``aircraft``, ``spacecraft``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Core models -- shared across all vehicle types
# ---------------------------------------------------------------------------


class Position(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0
    altitude_msl: float = 0.0
    altitude_agl: float = 0.0


class Attitude(BaseModel):
    pitch: float = 0.0
    bank: float = 0.0
    heading_true: float = 0.0
    heading_magnetic: float = 0.0


class Speeds(BaseModel):
    indicated_airspeed: float = 0.0
    true_airspeed: float = 0.0
    ground_speed: float = 0.0
    mach: float = 0.0
    vertical_speed: float = 0.0


class Environment(BaseModel):
    wind_speed_kts: float = 0.0
    wind_direction: float = 0.0
    visibility_sm: float = 0.0
    temperature_c: float = 0.0
    barometer_inhg: float = 29.92


# ---------------------------------------------------------------------------
# Aircraft-specific extension models (Airdale upstream compat)
# ---------------------------------------------------------------------------


class EngineData(BaseModel):
    rpm: float = 0.0
    manifold_pressure: float = 0.0
    fuel_flow_gph: float = 0.0
    egt: float = 0.0
    oil_temp: float = 0.0
    oil_pressure: float = 0.0


class Engines(BaseModel):
    engine_count: int = 0
    engines: list[EngineData] = Field(default_factory=list)

    @property
    def active_engines(self) -> list[EngineData]:
        return self.engines[: self.engine_count]


class AutopilotState(BaseModel):
    master: bool = False
    heading: float = 0.0
    altitude: float = 0.0
    vertical_speed: float = 0.0
    airspeed: float = 0.0


class RadioState(BaseModel):
    com1: float = 0.0
    com2: float = 0.0
    nav1: float = 0.0
    nav2: float = 0.0


class FuelState(BaseModel):
    total_gallons: float = 0.0
    total_weight_lbs: float = 0.0


class SurfaceState(BaseModel):
    gear_handle: bool = False
    flaps_percent: float = 0.0
    spoilers_percent: float = 0.0


class AircraftExtensions(BaseModel):
    """Aircraft-specific telemetry extensions."""

    engines: Engines | None = None
    autopilot: AutopilotState | None = None
    radios: RadioState | None = None
    fuel: FuelState | None = None
    surfaces: SurfaceState | None = None


# ---------------------------------------------------------------------------
# Spacecraft-specific extension models (Star Citizen)
# ---------------------------------------------------------------------------


class ShieldState(BaseModel):
    """Shield face strengths (0-100 percent each)."""

    front: float = 100.0
    rear: float = 100.0
    left: float = 100.0
    right: float = 100.0
    up: bool = True

    @property
    def average(self) -> float:
        """Average shield strength across all four faces."""
        return (self.front + self.rear + self.left + self.right) / 4.0


class QuantumDriveState(BaseModel):
    fuel_percent: float = 100.0
    spooling: bool = False
    active: bool = False


class WeaponState(BaseModel):
    armed: bool = False
    missiles_remaining: int = 0


class PowerState(BaseModel):
    on: bool = False
    hydrogen_fuel_percent: float = 100.0


class NavigationState(BaseModel):
    speed_scm: float = 0.0
    speed_max: float = 0.0
    decoupled_mode: bool = False
    landing_gear_down: bool = False


class PlayerLocationState(BaseModel):
    system: str = ""
    body: str = ""
    zone: str = ""
    in_ship: bool = False
    in_vehicle: bool = False
    crime_stat: int = 0


class CombatTelemetryState(BaseModel):
    under_attack: bool = False
    target_name: str = ""
    target_distance_km: float = 0.0
    hostile_count: int = 0
    friendly_count: int = 0


class SpacecraftExtensions(BaseModel):
    """Star Citizen spacecraft telemetry extensions.

    Mapped from orchestrator game_state.py models (ShipStatus,
    PlayerStatus, CombatState) into the universal telemetry schema.
    """

    shields: ShieldState | None = None
    quantum_drive: QuantumDriveState | None = None
    weapons: WeaponState | None = None
    power: PowerState | None = None
    navigation: NavigationState | None = None
    player: PlayerLocationState | None = None
    combat: CombatTelemetryState | None = None
    hull_percent: float = 100.0


# ---------------------------------------------------------------------------
# Universal telemetry envelope
# ---------------------------------------------------------------------------


class TelemetryEnvelope(BaseModel):
    """Top-level telemetry message wrapping data from any adapter.

    Core fields are universal across all vehicle types. Vehicle-specific
    data goes into ``extensions`` keyed by vehicle type.
    """

    adapter_id: str = ""
    sim_name: str = ""
    vehicle_type: str = "aircraft"
    timestamp: str = ""
    connected: bool = False
    vehicle_name: str = ""

    # Core telemetry (all vehicle types)
    position: Position | None = None
    attitude: Attitude | None = None
    speeds: Speeds | None = None
    environment: Environment | None = None

    # Vehicle-specific extensions keyed by type
    extensions: dict[str, Any] = Field(default_factory=dict)

    def with_aircraft_extensions(self, ext: AircraftExtensions) -> TelemetryEnvelope:
        """Convenience to set aircraft extensions."""
        self.extensions["aircraft"] = ext.model_dump(exclude_none=True)
        return self

    @property
    def aircraft(self) -> AircraftExtensions | None:
        """Parse aircraft extensions if present."""
        raw = self.extensions.get("aircraft")
        if raw is None:
            return None
        if isinstance(raw, AircraftExtensions):
            return raw
        return AircraftExtensions.model_validate(raw)

    def with_spacecraft_extensions(self, ext: SpacecraftExtensions) -> TelemetryEnvelope:
        """Convenience to set spacecraft extensions."""
        self.extensions["spacecraft"] = ext.model_dump(exclude_none=True)
        return self

    @property
    def spacecraft(self) -> SpacecraftExtensions | None:
        """Parse spacecraft extensions if present."""
        raw = self.extensions.get("spacecraft")
        if raw is None:
            return None
        if isinstance(raw, SpacecraftExtensions):
            return raw
        return SpacecraftExtensions.model_validate(raw)

    def to_legacy_simstate(self) -> dict[str, Any]:
        """Convert to the legacy SimState JSON format for backward compat.

        Returns a flat dict matching the original SimConnect bridge output
        so existing consumers that expect the old format still work.
        """
        result: dict[str, Any] = {
            "timestamp": self.timestamp,
            "connected": self.connected,
            "aircraft": self.vehicle_name,
        }
        if self.position:
            result["position"] = self.position.model_dump()
        if self.attitude:
            result["attitude"] = self.attitude.model_dump()
        if self.speeds:
            result["speeds"] = self.speeds.model_dump()
        if self.environment:
            result["environment"] = self.environment.model_dump()

        # Flatten aircraft extensions to top level
        aircraft_ext = self.aircraft
        if aircraft_ext:
            if aircraft_ext.engines:
                result["engines"] = aircraft_ext.engines.model_dump()
            if aircraft_ext.autopilot:
                result["autopilot"] = aircraft_ext.autopilot.model_dump()
            if aircraft_ext.radios:
                result["radios"] = aircraft_ext.radios.model_dump()
            if aircraft_ext.fuel:
                result["fuel"] = aircraft_ext.fuel.model_dump()
            if aircraft_ext.surfaces:
                result["surfaces"] = aircraft_ext.surfaces.model_dump()

        return result
