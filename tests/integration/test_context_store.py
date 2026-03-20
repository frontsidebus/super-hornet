"""Integration tests for the ChromaDB-backed ContextStore.

Tests that require the ChromaDB Docker container use the ``docker`` marker.
Tests that use a local PersistentClient (in-process) only need ``integration``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.context_store import ContextStore
from orchestrator.sim_client import FlightPhase, SimState

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def context_store(tmp_path: Path) -> ContextStore:
    """Create a fresh ContextStore backed by a temporary directory.

    ChromaDB's PersistentClient works locally, so no Docker needed for basic
    ingest/query tests.  The ``docker_chromadb`` fixture is available for
    tests that specifically exercise the HTTP client mode.
    """
    return ContextStore(persist_path=str(tmp_path / "chromadb"))


@pytest.fixture()
def populated_store(
    context_store: ContextStore,
    sample_document: Path,
    sample_document_metadata: dict[str, Any],
) -> ContextStore:
    """A ContextStore that already has the sample Cessna 172 document ingested."""
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        context_store.ingest_document(
            sample_document, metadata=sample_document_metadata
        )
    )
    return context_store


# ---------------------------------------------------------------------------
# Ingestion tests
# ---------------------------------------------------------------------------


class TestDocumentIngestion:
    async def test_ingest_creates_chunks(
        self, context_store: ContextStore, sample_document: Path, sample_document_metadata: dict
    ) -> None:
        """Ingesting a document should create multiple chunks."""
        count = await context_store.ingest_document(
            sample_document, metadata=sample_document_metadata
        )
        assert count > 0
        assert context_store.document_count == count

    async def test_ingest_is_idempotent(
        self, context_store: ContextStore, sample_document: Path, sample_document_metadata: dict
    ) -> None:
        """Ingesting the same document twice should upsert, not duplicate."""
        first = await context_store.ingest_document(
            sample_document, metadata=sample_document_metadata
        )
        second = await context_store.ingest_document(
            sample_document, metadata=sample_document_metadata
        )
        assert first == second
        assert context_store.document_count == first

    async def test_ingest_empty_file(
        self, context_store: ContextStore, tmp_path: Path
    ) -> None:
        """An empty file should produce zero chunks."""
        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")
        count = await context_store.ingest_document(empty)
        assert count == 0

    async def test_ingest_custom_chunk_size(
        self, context_store: ContextStore, sample_document: Path
    ) -> None:
        """Smaller chunk size should produce more chunks."""
        count_large = await context_store.ingest_document(
            sample_document, chunk_size=2000, chunk_overlap=100
        )
        # Re-create store for a fresh collection
        store2 = ContextStore(persist_path=str(Path(sample_document).parent / "chromadb2"))
        count_small = await store2.ingest_document(
            sample_document, chunk_size=200, chunk_overlap=50
        )
        assert count_small > count_large


# ---------------------------------------------------------------------------
# Query tests
# ---------------------------------------------------------------------------


class TestQuery:
    async def test_query_returns_relevant_results(
        self, populated_store: ContextStore
    ) -> None:
        """Querying for V-speeds should return content mentioning them."""
        results = await populated_store.query("V-speeds rotation Vr Vy")
        assert len(results) > 0
        # At least one result should mention V-speeds or rotation
        texts = " ".join(r["content"] for r in results)
        assert "Vr" in texts or "rotation" in texts.lower() or "V-SPEEDS" in texts

    async def test_query_returns_metadata(
        self, populated_store: ContextStore
    ) -> None:
        """Results should include document metadata."""
        results = await populated_store.query("takeoff checklist")
        assert len(results) > 0
        first = results[0]
        assert "metadata" in first
        assert "content" in first
        assert first["metadata"].get("aircraft_type") == "Cessna 172S Skyhawk"

    async def test_query_with_n_results(
        self, populated_store: ContextStore
    ) -> None:
        """Limiting n_results should cap the returned count."""
        results = await populated_store.query("checklist", n_results=1)
        assert len(results) <= 1

    async def test_query_empty_store(self, context_store: ContextStore) -> None:
        """Querying an empty store should return an empty list, not error."""
        results = await context_store.query("anything at all")
        assert results == []


# ---------------------------------------------------------------------------
# Aircraft-type filtering
# ---------------------------------------------------------------------------


class TestAircraftFiltering:
    async def test_filter_by_aircraft_type(
        self,
        context_store: ContextStore,
        sample_document: Path,
    ) -> None:
        """Filtering by aircraft_type should narrow results to that aircraft."""
        await context_store.ingest_document(
            sample_document, metadata={"aircraft_type": "Cessna 172S Skyhawk"}
        )
        results = await context_store.query(
            "takeoff procedure",
            filters={"aircraft_type": "Cessna 172S Skyhawk"},
        )
        assert len(results) > 0
        for r in results:
            assert r["metadata"]["aircraft_type"] == "Cessna 172S Skyhawk"

    async def test_filter_nonexistent_aircraft_returns_empty(
        self, populated_store: ContextStore
    ) -> None:
        """Filtering for an aircraft not in the store should return nothing."""
        results = await populated_store.query(
            "takeoff procedure",
            filters={"aircraft_type": "Boeing 747-400"},
        )
        assert results == []


# ---------------------------------------------------------------------------
# get_relevant_context with sim state
# ---------------------------------------------------------------------------


class TestGetRelevantContext:
    async def test_relevant_context_with_aircraft(
        self, populated_store: ContextStore
    ) -> None:
        """With matching aircraft_title, should return aircraft-specific docs."""
        state = SimState(
            aircraft_title="Cessna 172S Skyhawk",
            flight_phase=FlightPhase.TAKEOFF,
        )
        results = await populated_store.get_relevant_context(state)
        assert len(results) > 0

    async def test_relevant_context_unknown_aircraft_falls_back(
        self, populated_store: ContextStore
    ) -> None:
        """An unknown aircraft should fall back to unfiltered results."""
        state = SimState(
            aircraft_title="Unknown Experimental X-99",
            flight_phase=FlightPhase.CRUISE,
        )
        results = await populated_store.get_relevant_context(state)
        # Should still find generic results from the ingested doc
        assert len(results) > 0

    async def test_relevant_context_empty_aircraft(
        self, populated_store: ContextStore
    ) -> None:
        """Empty aircraft_title should query without aircraft filter."""
        state = SimState(
            aircraft_title="",
            flight_phase=FlightPhase.PREFLIGHT,
        )
        results = await populated_store.get_relevant_context(state)
        assert len(results) > 0
