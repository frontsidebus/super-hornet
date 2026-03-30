"""Tests for StatePersistence save/load with atomic writes and throttling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telemetry.persistence import StatePersistence
from telemetry.schema import TelemetryEnvelope


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@pytest.fixture
def persistence(state_path: Path) -> StatePersistence:
    return StatePersistence(path=state_path, write_interval=5.0)


@pytest.fixture
def envelope() -> TelemetryEnvelope:
    return TelemetryEnvelope(
        adapter_id="sc-adapter-01",
        sim_name="star_citizen",
        vehicle_type="spacecraft",
        timestamp="2025-01-01T00:00:00Z",
        connected=True,
        vehicle_name="F7C-M Super Hornet",
    )


class TestSave:
    async def test_save_writes_json_file(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        await persistence.save(envelope)
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["adapter_id"] == "sc-adapter-01"
        assert data["sim_name"] == "star_citizen"

    async def test_save_includes_persisted_at(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        await persistence.save(envelope)
        data = json.loads(state_path.read_text())
        assert "_persisted_at" in data
        assert isinstance(data["_persisted_at"], float)

    async def test_save_is_throttled(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        # First save writes
        await persistence.save(envelope)
        first_content = state_path.read_text()

        # Modify envelope
        envelope.vehicle_name = "Gladius"

        # Second save within interval should be no-op
        await persistence.save(envelope)
        assert state_path.read_text() == first_content

    async def test_save_after_interval_elapses(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        await persistence.save(envelope)
        first_content = state_path.read_text()

        # Simulate interval elapsed by resetting _last_write
        persistence._last_write = 0.0
        envelope.vehicle_name = "Gladius"
        await persistence.save(envelope)

        second_content = state_path.read_text()
        assert second_content != first_content
        data = json.loads(second_content)
        assert data["vehicle_name"] == "Gladius"

    async def test_atomic_write_no_tmp_remains(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        await persistence.save(envelope)
        tmp_path = state_path.with_suffix(".tmp")
        assert not tmp_path.exists()
        assert state_path.exists()


class TestLoad:
    async def test_load_returns_envelope_from_valid_file(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        # Write directly so we don't depend on save throttle
        data = envelope.model_dump(mode="json")
        data["_persisted_at"] = 1234567890.0
        state_path.write_text(json.dumps(data))

        result = persistence.load()
        assert result is not None
        assert isinstance(result, TelemetryEnvelope)
        assert result.adapter_id == "sc-adapter-01"
        assert result.vehicle_name == "F7C-M Super Hornet"

    async def test_load_returns_none_for_missing_file(
        self, persistence: StatePersistence, state_path: Path
    ) -> None:
        assert not state_path.exists()
        result = persistence.load()
        assert result is None

    async def test_load_returns_none_for_corrupt_file(
        self, persistence: StatePersistence, state_path: Path
    ) -> None:
        state_path.write_text("not json at all {{{")
        result = persistence.load()
        assert result is None

    async def test_load_strips_persisted_at(
        self, persistence: StatePersistence, state_path: Path, envelope: TelemetryEnvelope
    ) -> None:
        data = envelope.model_dump(mode="json")
        data["_persisted_at"] = 1234567890.0
        state_path.write_text(json.dumps(data))

        result = persistence.load()
        assert result is not None
        # _persisted_at should not be present as a field on the model
        result_data = result.model_dump(mode="json")
        assert "_persisted_at" not in result_data
