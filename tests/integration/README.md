# MERLIN Integration Tests

Integration tests for the MERLIN AI co-pilot orchestrator. These tests exercise
real service interactions (Docker containers, network APIs, WebSocket protocols)
beyond what unit tests cover.

## Prerequisites

- Python 3.11+
- Docker and Docker Compose (for container-dependent tests)
- Internet access (for network-dependent tests)
- Install test dependencies: `pip install -e "orchestrator[test]"`

## Running Tests

By default, `pytest` only runs unit tests (integration tests are excluded via
the `addopts` config in `pyproject.toml`).

### All integration tests

```bash
pytest tests/integration/ -m integration
```

### Docker-dependent tests only

Requires Docker running with the MERLIN containers available:

```bash
docker compose up -d whisper chromadb
pytest tests/integration/ -m docker
```

### Network-dependent tests only

Requires internet access (makes real HTTP calls to external APIs):

```bash
pytest tests/integration/ -m network
```

### Specific test files

```bash
pytest tests/integration/test_simconnect_websocket.py -m integration
pytest tests/integration/test_context_store.py -m integration
pytest tests/integration/test_tool_chain.py -m integration
pytest tests/integration/test_orchestrator_e2e.py -m integration
```

### Combined markers

```bash
# Docker AND network tests
pytest tests/integration/ -m "docker or network"

# Everything except slow tests
pytest tests/integration/ -m "integration and not slow"
```

## Test Categories

| File | Markers | External Dependencies |
|---|---|---|
| `test_whisper_pipeline.py` | `integration`, `docker` | Whisper Docker container |
| `test_context_store.py` | `integration` | None (uses local ChromaDB) |
| `test_simconnect_websocket.py` | `integration` | None (uses mock WS server) |
| `test_tool_chain.py` | `integration`, `network` (some) | aviationapi.com (network tests only) |
| `test_orchestrator_e2e.py` | `integration` | None (all external services mocked) |

## Architecture

Tests use a mock WebSocket server (`MockSimConnectServer` in `conftest.py`) that
mimics the C# SimConnect bridge protocol. This allows testing the full Python
client without MSFS or the .NET bridge running.

Docker fixtures use `docker compose` to manage container lifecycle within the
test session. They poll health endpoints before yielding.
