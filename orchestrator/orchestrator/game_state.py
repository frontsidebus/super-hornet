"""Star Citizen game state models for the Super Hornet AI agent.

State is composed from multiple unreliable sources (log parsing, vision,
external APIs), so fields carry implicit best-effort semantics.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class GameActivity(StrEnum):
    """Star Citizen activity states -- replaces FlightPhase."""

    IDLE = "IDLE"
    ON_FOOT = "ON_FOOT"
    SHIP_IDLE = "SHIP_IDLE"
    SHIP_FLIGHT = "SHIP_FLIGHT"
    QUANTUM_TRAVEL = "QUANTUM_TRAVEL"
    COMBAT = "COMBAT"
    MINING = "MINING"
    SALVAGE = "SALVAGE"
    TRADING = "TRADING"
    LANDING = "LANDING"
    EVA = "EVA"
    ENGINEERING = "ENGINEERING"


class ShipStatus(BaseModel):
    """Ship state inferred from vision + log data."""

    name: str = ""
    shields_up: bool = True
    shields_front: float = 100.0
    shields_rear: float = 100.0
    shields_left: float = 100.0
    shields_right: float = 100.0
    hull_percent: float = 100.0
    quantum_fuel_percent: float = 100.0
    hydrogen_fuel_percent: float = 100.0
    power_on: bool = False
    weapons_armed: bool = False
    landing_gear_down: bool = False
    quantum_drive_spooling: bool = False
    quantum_drive_active: bool = False
    missiles_remaining: int = 0
    speed_scm: float = 0.0
    speed_max: float = 0.0
    decoupled_mode: bool = False

    @property
    def shields_percent(self) -> float:
        """Average shield strength across all faces."""
        return (
            self.shields_front
            + self.shields_rear
            + self.shields_left
            + self.shields_right
        ) / 4.0


class PlayerStatus(BaseModel):
    """Player state from logs + vision."""

    location_system: str = ""  # "Stanton", "Pyro", "Nyx"
    location_body: str = ""  # "Hurston", "Crusader", etc.
    location_zone: str = ""  # "Lorville", "Port Olisar", etc.
    credits_auec: int = 0
    in_ship: bool = False
    in_vehicle: bool = False
    crime_stat: int = 0


class CombatState(BaseModel):
    """Combat-specific state from logs + vision."""

    under_attack: bool = False
    target_name: str = ""
    target_distance_km: float = 0.0
    hostile_count: int = 0
    friendly_count: int = 0
    last_kill: str = ""
    last_death: str = ""


class GameState(BaseModel):
    """Complete game state snapshot.

    GameState is composed from multiple unreliable sources (log parsing,
    vision inference, API calls). Every field should be treated as
    best-effort.
    """

    timestamp: str = ""
    activity: GameActivity = GameActivity.IDLE
    ship: ShipStatus = Field(default_factory=ShipStatus)
    player: PlayerStatus = Field(default_factory=PlayerStatus)
    combat: CombatState = Field(default_factory=CombatState)
    raw_log_events: list[dict[str, Any]] = Field(default_factory=list)
    vision_data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0

    def state_summary(self) -> str:
        """One-line summary for context injection -- replaces telemetry_summary()."""
        parts = [f"Activity: {self.activity.value}"]

        if self.player.location_system:
            loc = self.player.location_system
            if self.player.location_body:
                loc += f"/{self.player.location_body}"
            if self.player.location_zone:
                loc += f"/{self.player.location_zone}"
            parts.append(f"Location: {loc}")

        if self.player.in_ship and self.ship.name:
            parts.append(f"Ship: {self.ship.name}")
            parts.append(f"Shields: {self.ship.shields_percent:.0f}%")
            parts.append(f"Hull: {self.ship.hull_percent:.0f}%")
            parts.append(f"QFuel: {self.ship.quantum_fuel_percent:.0f}%")
            parts.append(f"HFuel: {self.ship.hydrogen_fuel_percent:.0f}%")
            if self.ship.quantum_drive_active:
                parts.append("QT:ACTIVE")
            if self.ship.weapons_armed:
                parts.append("WEAPONS:HOT")
            if self.ship.decoupled_mode:
                parts.append("DECOUPLED")

        if self.combat.under_attack:
            parts.append("UNDER ATTACK")
        if self.combat.hostile_count > 0:
            parts.append(f"Hostiles: {self.combat.hostile_count}")
        if self.player.crime_stat > 0:
            parts.append(f"CrimeStat: {self.player.crime_stat}")

        return " | ".join(parts)
