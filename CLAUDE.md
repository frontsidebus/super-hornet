# CLAUDE.md -- Project Conventions for Airdale (MERLIN)

## Project Overview

**Airdale** (codename) is an AI co-pilot called **MERLIN** for Microsoft Flight Simulator 2024. It connects to the sim via SimConnect, processes real-time telemetry, and provides voice-interactive flight guidance powered by Claude. The persona is a Navy Test Pilot with encyclopedic aviation knowledge and dry humor.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestrator | Python 3.11+ (async, hatch build system) |
| Web Server | FastAPI with WebSocket support (browser UI) |
| SimConnect Bridge | C# / .NET 8 (out-of-process exe, event-driven message pump) |
| AI Inference | Anthropic Claude API with tool use |
| Vector Store / RAG | ChromaDB with sentence-transformers embeddings |
| Speech-to-Text | Whisper `small` model (local via Docker, onerahmet/openai-whisper-asr-webservice) |
| Text-to-Speech | ElevenLabs streaming API (`eleven_multilingual_v2` model) |
| IPC | WebSocket (JSON) between bridge and orchestrator |
| Config | pydantic-settings with .env files |

## Directory Structure

```
airdale/
├── orchestrator/                # Python package -- the brain
│   ├── orchestrator/            # Source package
│   │   ├── __init__.py
│   │   ├── audio_processing.py  # Audio preprocessing (high-pass, trim, normalize)
│   │   ├── claude_client.py     # Anthropic API wrapper with MERLIN persona + tools
│   │   ├── config.py            # Pydantic settings from .env
│   │   ├── context_store.py     # ChromaDB RAG store with query cache
│   │   ├── flight_phase.py      # State-machine flight phase detector
│   │   ├── main.py              # CLI entry point
│   │   ├── screen_capture.py    # Optional screen capture for vision analysis
│   │   ├── sim_client.py        # WebSocket client, telemetry models, health monitor
│   │   ├── tools.py             # Claude tool implementations
│   │   ├── voice.py             # Voice I/O (PTT, VAD, barge-in, streaming TTS)
│   │   └── whisper_client.py    # Whisper ASR HTTP client with retry logic
│   ├── tests/                   # Unit tests (pytest + pytest-asyncio)
│   ├── Dockerfile
│   └── pyproject.toml           # Build config, dependencies, ruff settings
├── simconnect-bridge/           # C# .NET project -- the sensor layer
│   ├── Models/
│   │   ├── SimDataStructs.cs    # SimConnect data structure definitions
│   │   └── SimState.cs          # Telemetry data model
│   ├── SimConnectBridge.Tests/  # xUnit tests for bridge components
│   ├── SimConnectManager.cs     # Event-driven SimConnect message pump
│   ├── WebSocketServer.cs       # Fleck WebSocket server with client tracking
│   ├── Program.cs               # Entry point
│   ├── SimConnectBridge.csproj
│   └── appsettings.json
├── web/                         # FastAPI web UI server
│   ├── server.py                # Backend: telemetry WS, chat, STT/TTS proxy
│   ├── run.py                   # Dev server launcher
│   ├── requirements.txt
│   └── static/                  # Browser frontend
│       ├── index.html           # TARS-style cockpit display
│       ├── app.js               # WebSocket client, audio capture, UI logic
│       └── style.css
├── data/
│   ├── checklists/              # YAML checklist files (generic_single_engine, etc.)
│   └── prompts/                 # System prompt templates
│       ├── merlin_system.md     # MERLIN persona definition
│       └── merlin_emergency.md  # Emergency procedure prompt overlay
├── tests/                       # Integration tests (root level)
│   └── integration/             # End-to-end, WebSocket, tool chain, Whisper pipeline
├── tools/                       # Developer utilities
│   ├── download_faa_data.py     # FAA data fetcher for RAG ingestion
│   ├── ingest.py                # Document ingestion into ChromaDB
│   └── test_tts.py              # ElevenLabs TTS smoke test
├── docs/                        # Project documentation
│   ├── ARCHITECTURE.md          # System design and data flows
│   ├── API.md                   # WebSocket protocol reference
│   ├── GETTING_STARTED.md
│   └── INSTALL.md
├── docker-compose.yml           # Production service stack
├── docker-compose.dev.yml       # Dev overrides (hot-reload, bind mounts)
├── .env.example                 # Environment variable template
├── .env                         # Local config (git-ignored)
└── CLAUDE.md                    # This file
```

## Development Commands

### Python Orchestrator

```bash
cd orchestrator

# Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Run the orchestrator (CLI mode)
merlin

# Lint
ruff check .

# Format
ruff format .

# Run tests
pytest
```

### Web UI Server

```bash
cd web

# Install dependencies (or use the orchestrator venv)
pip install -r requirements.txt

# Run the FastAPI dev server (defaults to http://localhost:3838)
python run.py
```

### C# SimConnect Bridge

```bash
cd simconnect-bridge

# Restore and build
dotnet restore
dotnet build

# Run (MSFS must be running)
dotnet run

# Run tests
dotnet test
```

### Docker Services

```bash
# Start all services (Whisper, ChromaDB, orchestrator)
docker compose up -d

# Dev mode with hot-reload and bind mounts
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# View logs
docker compose logs -f orchestrator

# Rebuild after dependency changes
docker compose build --no-cache orchestrator
```

### WSL2 Note

When running the orchestrator or web server inside WSL2, the SimConnect bridge runs on the Windows host. Set the bridge URL to reach the host:

```bash
# In .env
SIMCONNECT_WS_HOST=host.docker.internal   # Docker
SIMCONNECT_WS_HOST=$(hostname).local       # WSL2 native
```

## Code Style

### Python

- **Linter/Formatter:** ruff (config in `pyproject.toml`)
- **Line length:** 100 characters
- **Type hints:** Required on all function signatures
- **Async:** Use `async`/`await` throughout the orchestrator -- the event loop is the heartbeat
- **Imports:** Sorted by ruff (isort-compatible)
- **Naming:** `snake_case` for functions and variables, `PascalCase` for classes
- **Models:** Use Pydantic `BaseModel` for all data structures crossing boundaries
- **Config:** Use `pydantic-settings` `BaseSettings` -- never hardcode keys or magic numbers
- **ruff rules enabled:** E (pycodestyle), F (pyflakes), I (isort), N (pep8-naming), UP (pyupgrade), B (bugbear), SIM (simplify)

### C#

- Standard .NET conventions
- `PascalCase` for public members, `_camelCase` for private fields
- Nullable reference types enabled
- Models in the `Models/` directory
- XML doc comments on public APIs

## Important Architectural Decisions

1. **SimConnect bridge MUST be out-of-process** -- It runs as a separate .exe, not a WASM module. This is Microsoft's recommendation for stability. If the bridge crashes, MSFS keeps running.

2. **Event-driven SimConnect message pump** -- The bridge uses an `EventWaitHandle`-based message pump instead of timer-based polling. SimConnect signals the event when data is ready; the pump thread calls `ReceiveMessage()` in response. This eliminates the 0x80004005 COM errors caused by unsynchronized timer polling.

3. **Subscription-based data delivery** -- The `TelemetryWebSocketServer` broadcasts state updates to all connected WebSocket clients. Clients can subscribe to field subsets to reduce bandwidth.

4. **WebSocket for IPC** -- The bridge and orchestrator communicate over WebSocket with JSON payloads. This keeps the components language-agnostic and independently deployable.

5. **Claude tool use for actions** -- The orchestrator defines tools (`get_sim_state`, `lookup_airport`, `search_manual`, `get_checklist`, `create_flight_plan`) that Claude calls mid-response. Do not pre-fetch everything into the context window.

6. **Dynamic token budgeting** -- Three tiers: 256 tokens for short acknowledgments (roger, thanks, simple questions); `claude_max_tokens` (1024) for routine cockpit comms; `claude_max_tokens_briefing` (2048) for briefings, checklists, and flight plans. This keeps responses tactical during high-workload phases.

7. **Flight-phase-aware response styles** -- Each flight phase injects a style directive into the system prompt (e.g., PREFLIGHT allows banter; TAKEOFF demands brevity). The `FlightPhaseDetector` uses a state machine with hysteresis (3 consecutive detections before transition) to prevent oscillation.

8. **Flight phase is derived from telemetry** -- The orchestrator infers the current phase (preflight, taxi, takeoff, climb, cruise, descent, approach, landing, landed) from sim state. This drives checklist selection and proactive callouts.

9. **Voice is streaming** -- TTS begins playing as Claude's response streams in. Do not wait for the full response before starting audio playback.

10. **TTS text sanitizer** -- Claude responses are sanitized before TTS synthesis to strip markdown formatting, special characters, and other tokens that produce garbled speech output.

11. **Audio preprocessing pipeline** -- Incoming microphone audio passes through a high-pass filter, silence trimming, and normalization before being sent to Whisper. This improves transcription accuracy in noisy cockpit environments.

12. **Aviation vocabulary prompting for Whisper** -- An `initial_prompt` containing aviation terms (ATIS, METAR, squawk, NATO phonetic alphabet, etc.) biases Whisper toward recognizing aviation terminology without restricting its output.

13. **Barge-in / interruption support** -- If the user sends new audio or text while MERLIN is responding, the current Claude stream and TTS pipeline are cancelled immediately. The web server manages cancellation tokens per-client.

14. **Delta detection for telemetry deduplication** -- The `SimConnectClient` tracks previous state and only fires update callbacks when telemetry values actually change, reducing unnecessary processing.

15. **Query cache for ChromaDB** -- The `ContextStore` uses a TTL-based cache (60s default) keyed by query text, result count, and filter hash. Within a single flight phase, relevant documents rarely change, so this avoids redundant round-trips to ChromaDB.

## Testing Approach

- **287 tests passing** across Python and C# test suites.
- **Python:** pytest + pytest-asyncio for async tests. Mock the WebSocket connection and Claude API in unit tests.
- **C#:** xUnit. Mock SimConnect for unit tests. Integration tests require MSFS running.
- **No sim required for most tests** -- Record telemetry snapshots as JSON fixtures and replay them through the orchestrator.
- **Test categories include:** unit tests (config, flight phase, tools, Claude client, Whisper client, context store, screen capture), integration tests (WebSocket reconnection, health monitor, delta detection, query classification, orchestrator end-to-end, tool chain, Whisper pipeline).

## Environment Variables

All config flows through `.env` files loaded by `pydantic-settings`. See `.env.example` for the complete list with documentation. Never commit `.env` to version control.
