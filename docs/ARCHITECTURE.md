# MERLIN v1.1 -- Architecture Documentation

Technical deep-dive into MERLIN's system design, component responsibilities, data flows, and implementation decisions.

---

## System Overview

```
+-------------------+         SimConnect (COM)            +------------------------+
|                   | <---------------------------------> |                        |
|   MSFS 2024       |   Position, Attitude, Speeds,       |   SimConnect Bridge    |
|   (Simulator)     |   Engines, Fuel, Environment,       |   (C# / .NET 8)       |
|                   |   Autopilot, Radios, Surfaces       |   Event-driven pump   |
+-------------------+                                     +----------+-------------+
                                                                     |
                                                          WebSocket (JSON)
                                                          ws://localhost:8080
                                                                     |
                    +------------------------------------------------+
                    |
                    v
+-----------------------------------------------------------------------------------+
|                                                                                   |
|                     FastAPI Web Server (web/server.py)                             |
|                     http://localhost:3838                                          |
|                                                                                   |
|   +----------------+    +------------------+    +--------------+                  |
|   |  SimConnect     |    |  Claude Client   |    |  Flight      |                  |
|   |  Client         |    |  (Anthropic API) |    |  Phase       |                  |
|   |  (websockets)   |    |  Tool dispatch   |    |  Detector    |                  |
|   |  Delta detect   |    |  Token budgeting |    |  Hysteresis  |                  |
|   +----------------+    +------------------+    +--------------+                  |
|                                                                                   |
|   +----------------+    +------------------+    +--------------+                  |
|   |  Audio          |    |  Whisper Client  |    |  Voice I/O   |                  |
|   |  Preprocessing  |    |  (HTTP, retry)   |    |  PTT / VAD   |                  |
|   |  Hi-pass/norm   |    |  Aviation vocab  |    |  Silero VAD  |                  |
|   |  Silero VAD     |    |  Conn pooling    |    |  Barge-in    |                  |
|   +----------------+    +------------------+    +--------------+                  |
|                                                                                   |
|   +----------------+                                                              |
|   |  TTS           |                                                              |
|   |  Preprocessor  |                                                              |
|   |  ICAO phrases  |                                                              |
|   +----------------+                                                              |
|                                                                                   |
|   +----------------+    +------------------+    +--------------+                  |
|   |  Context Store  |    |  Tool Functions  |    |  Screen      |                  |
|   |  (ChromaDB)     |    |  get_sim_state   |    |  Capture     |                  |
|   |  Query cache    |    |  lookup_airport  |    |  (optional)  |                  |
|   +----------------+    |  search_manual   |    +--------------+                  |
|                          |  get_checklist   |                                      |
|                          |  create_flight.. |                                      |
|                          +------------------+                                      |
+----------------------------+---------------------------+--------------------------+
                             |                           |
              +--------------+--+              +---------+--------+
              |                 |              |                  |
       +------+------+  +------+------+  +----+------+   +------+------+
       |  faster-   |  |  ElevenLabs |  |  ChromaDB |   |  Aviation   |
       |  whisper   |  |  TTS API    |  |  (Docker) |   |  API (FAA)  |
       |  (Docker)  |  |  WS stream  |  |  Port 8000|   |  (HTTP)     |
       |  Port 9090 |  |  v2 model   |  +-----------+   +-------------+
       +------------+  +-------------+
              ^                 |
              |                 v
+-----------------------------------------------------------------------------------+
|                                                                                   |
|                     Browser (web/static/)                                          |
|                     TARS-style cockpit display                                    |
|                                                                                   |
|   +-------------------+    +-------------------+    +-------------------+          |
|   |  Telemetry WS     |    |  Audio Capture    |    |  Chat Interface   |          |
|   |  Real-time gauges |    |  WebM -> server   |    |  Text + voice     |          |
|   +-------------------+    +-------------------+    +-------------------+          |
|                                                                                   |
+-----------------------------------------------------------------------------------+
```

---

## Data Flow: Simulator Telemetry

```
MSFS 2024
  -> SimConnect COM API (native Windows)
  -> SimConnectManager (C#)
       Event-driven message pump: EventWaitHandle signals when data ready
       Dedicated pump thread calls ReceiveMessage() on signal
       High-frequency data (per sim frame, when changed): position, attitude, speeds
       Low-frequency data (1 Hz): autopilot, radios, fuel, surfaces, environment, engines
  -> Assembles SimState object (thread-safe, locked)
  -> Fires StateUpdated event
  -> TelemetryWebSocketServer serializes to JSON (snake_case)
  -> Broadcasts to all connected WebSocket clients
  -> SimConnectClient (Python) receives via websockets library
  -> Delta detection: only fires callbacks when values actually change
  -> Deserializes into Pydantic SimState model
  -> FlightPhaseDetector evaluates telemetry, updates phase (with hysteresis)
  -> FastAPI WebSocket relays telemetry to browser for real-time gauge display
```

---

## Data Flow: Voice Pipeline

```
Browser microphone
  -> MediaRecorder (WebM/Opus)
  -> WebSocket binary frame to FastAPI server
  -> Send WebM direct (skip ffmpeg conversion)
  -> Silero VAD (neural voice activity detection, 400ms silence timeout)
  -> Audio preprocessing pipeline:
       1. High-pass filter (remove low-frequency noise)
       2. Silence trimming (strip leading/trailing dead air)
       3. Amplitude normalization (consistent input levels)
  -> POST to faster-whisper HTTP API (localhost:9090/v1/audio/transcriptions)
       Model: medium (CTranslate2 backend, 3-4x faster than stock Whisper)
       initial_prompt: aviation vocabulary (ATIS, METAR, squawk, NATO phonetic, ...)
       Connection pooling via shared httpx.AsyncClient
       Note: the standalone WhisperClient retries with exponential backoff;
       the web server's transcription path does not retry
  -> TranscriptionResult (text, confidence, language, duration)
  -> ClaudeClient conversation loop:
       1. Build system prompt: persona + phase style + telemetry + RAG context
       2. Dynamic token budget: 1024 (routine) or 2048 (briefings/checklists)
       3. Stream response from Claude API
       4. Execute tool calls in agentic loop if needed
  -> Aviation TTS preprocessor (ICAO digit pronunciation, flight levels,
       headings, frequencies, runway designators, squawk codes)
  -> TTS text sanitizer strips markdown/special characters
  -> ElevenLabs WebSocket streaming (persistent connection per response)
       TLS pre-warm at startup; phrase cache for common responses
  -> Audio bytes streamed via WebSocket to browser
  -> Browser AudioContext plays audio

Barge-in: new user input cancels in-flight Claude stream + TTS immediately
```

---

## Component Descriptions

### SimConnect Bridge (`simconnect-bridge/`)

| File | Responsibility |
|---|---|
| `SimConnectManager.cs` | SimConnect lifecycle, event-driven message pump, data definition registration, auto-reconnect on MSFS crash/restart |
| `WebSocketServer.cs` | Fleck-based WebSocket server, JSON broadcast, client tracking with `ConcurrentDictionary`, heartbeat support |
| `Models/SimState.cs` | Telemetry data model: position, attitude, speeds, engines, fuel, autopilot, radios, environment, surfaces |
| `Models/SimDataStructs.cs` | C# struct definitions matching SimConnect data layout for marshalling |
| `Program.cs` | Entry point, wires up manager + server, handles graceful shutdown |

**Key design:** The message pump uses an `EventWaitHandle` instead of a timer. SimConnect signals the handle when data arrives; the pump thread wakes, calls `ReceiveMessage()`, and goes back to sleep. This replaced a timer-based approach that caused `0x80004005` COM errors from unsynchronized polling.

### Orchestrator (`orchestrator/orchestrator/`)

| File | Responsibility |
|---|---|
| `config.py` | `pydantic-settings` `BaseSettings` class; all config from `.env` |
| `sim_client.py` | WebSocket client for bridge; Pydantic models (`SimState`, `Position`, `Attitude`, `Speeds`, `EngineData`, `Engines`, `AutopilotState`, `RadioState`, `FuelState`, `Environment`, `SurfaceState`); `ConnectionState` enum; `HealthMonitor` for subsystem status; delta detection |
| `claude_client.py` | Anthropic API wrapper; MERLIN persona (from `data/prompts/merlin_system.md`); flight-phase-aware response style directives; response pacing rules; streaming with tool dispatch |
| `flight_phase.py` | `FlightPhaseDetector` state machine with configurable `PhaseThresholds`; hysteresis (3 consecutive detections before transition) |
| `context_store.py` | ChromaDB RAG store; document ingestion with chunking; phase-aware topic mapping; `_QueryCache` with 60s TTL to avoid redundant ChromaDB round-trips |
| `audio_processing.py` | Audio preprocessing: high-pass filter, silence trimming, normalization; Silero VAD (neural voice activity detection, 400ms silence timeout); `SC_VOCABULARY_PROMPT` vocabulary for Whisper biasing; WebM-to-WAV conversion |
| `voice.py` | `VoiceInput` (PTT and VAD modes); `VoiceOutput` (streaming TTS with sentence buffering); barge-in cancellation support |
| `tts_preprocessor.py` | ICAO-compliant aviation text preprocessing for TTS: digit-by-digit pronunciation for flight levels, headings, frequencies, runway designators, squawk codes |
| `whisper_client.py` | HTTP client for faster-whisper ASR service (OpenAI-compatible `/v1/audio/transcriptions` endpoint); `TranscriptionResult` dataclass; retry with exponential backoff; confidence scoring |
| `tools.py` | Claude tool implementations: `get_sim_state`, `lookup_airport`, `search_manual`, `get_checklist`, `create_flight_plan` |
| `screen_capture.py` | Optional screen capture for vision-based analysis |
| `main.py` | CLI entry point for headless/console operation |

### Web Server (`web/`)

| File | Responsibility |
|---|---|
| `server.py` | FastAPI application; WebSocket endpoints for telemetry streaming and chat; HTTP endpoints for audio upload (STT) and TTS proxy; barge-in cancellation; serves static files |
| `run.py` | Uvicorn dev server launcher |
| `static/index.html` | TARS-style cockpit display with telemetry gauges |
| `static/app.js` | Browser WebSocket client, audio capture (MediaRecorder), UI state management |
| `static/style.css` | Cockpit UI styling |

### Data Files (`data/`)

| Path | Purpose |
|---|---|
| `data/prompts/merlin_system.md` | Full MERLIN persona definition loaded at startup |
| `data/prompts/merlin_emergency.md` | Emergency procedure prompt overlay |
| `data/checklists/generic_single_engine.yaml` | Generic single-engine piston checklist |
| `data/checklists/generic_jet.yaml` | Generic jet aircraft checklist |

---

## Key Design Decisions and Rationale

### 1. Event-Driven SimConnect Pump (not Timer-Based)

**Decision:** Replace the original timer-based `ReceiveMessage()` polling with an `EventWaitHandle`-driven pump thread.

**Rationale:** Timer-based polling at 100 Hz raced with SimConnect's internal event model, causing intermittent `HRESULT 0x80004005` COM errors. The event-driven approach lets SimConnect signal exactly when data is ready, eliminating the race condition and reducing CPU usage during idle periods.

### 2. Audio Preprocessing Before Whisper

**Decision:** Apply a high-pass filter, silence trimming, and amplitude normalization to all audio before sending to Whisper.

**Rationale:** Cockpit environments (even simulated ones with headsets) introduce low-frequency hum, inconsistent mic levels, and leading/trailing silence. Preprocessing improves Whisper's transcription accuracy, particularly for short aviation commands that might otherwise be lost in noise.

### 3. Aviation Vocabulary Prompting

**Decision:** Pass an `initial_prompt` containing aviation terminology (ICAO phonetic alphabet, instrument abbreviations, procedure callouts) to every Whisper transcription request.

**Rationale:** Whisper's general-purpose language model frequently misrecognizes aviation-specific terms ("squawk" as "squall", "ATIS" as "at this"). The initial prompt biases the model toward the correct vocabulary without restricting its output to a fixed dictionary.

### 4. Dynamic Token Budgeting

**Decision:** Use 1024 max tokens for routine cockpit communication and 2048 for briefings, checklists, and flight plans.

**Rationale:** During high-workload phases (takeoff, approach), lengthy responses are a distraction. The lower budget forces Claude to be tactical. During low-workload phases (preflight briefing, cruise), the pilot benefits from thorough, structured information.

### 5. Flight-Phase-Aware Response Styles

**Decision:** Inject phase-specific style directives into the system prompt (e.g., PREFLIGHT allows banter; TAKEOFF demands brevity and safety focus).

**Rationale:** A co-pilot who rambles during a go-around or who is terse during a relaxed preflight briefing feels unnatural. Phase-aware styling makes MERLIN contextually appropriate.

### 6. Barge-In / Interruption Support

**Decision:** If the user sends new input while MERLIN is mid-response, immediately cancel the Claude stream and TTS pipeline.

**Rationale:** In a cockpit, the pilot's new input is always higher priority than the co-pilot's current utterance. Forcing the user to wait for MERLIN to finish speaking before accepting new input would break the interaction model.

### 7. Query Cache for ChromaDB

**Decision:** Cache RAG query results with a 60-second TTL, keyed by query text, result count, and filter hash. Invalidate on flight phase change.

**Rationale:** Within a single flight phase, the relevant reference documents rarely change. A 60-second cache avoids repeated network round-trips to ChromaDB for identical queries during rapid-fire conversation turns.

### 8. Delta Detection for Telemetry

**Decision:** The `SimConnectClient` tracks previous state and only fires update callbacks when telemetry values actually change.

**Rationale:** At sim-frame rate, most telemetry frames are identical to the previous frame (especially for slowly-changing parameters). Delta detection reduces unnecessary downstream processing, logging, and WebSocket traffic to browser clients.

---

## Docker Services

### Service Topology

```
+---------------------------------------------------+
|  Docker Network: merlin (bridge)                  |
|                                                    |
|   +-----------+   +-----------+   +------------+  |
|   | faster-  |   | chromadb  |   |orchestrator|  |
|   | whisper  |   |  :8000    |   |   :3838    |  |
|   |  :9090   |   |           |   |            |  |
|   +-----------+   +-----------+   +------------+  |
|                                          |         |
+------------------------------------------|--------+
                                           |
                              host.docker.internal
                                           |
                              +------------+--------+
                              | SimConnect Bridge    |
                              | (Windows host)       |
                              | ws://0.0.0.0:8080    |
                              +---------------------+
                                           |
                              +------------+--------+
                              | MSFS 2024            |
                              | (Windows host)       |
                              +---------------------+
```

### Networking

- All Docker services share the `merlin` bridge network and communicate by service name (`whisper`, `chromadb`).
- The orchestrator container reaches the SimConnect bridge on the Windows host via `host.docker.internal`, which Docker Desktop maps to the host machine's IP.
- The `extra_hosts` directive ensures `host.docker.internal` resolves correctly even on non-Docker-Desktop setups.
- Ports 9090 (Whisper), 8000 (ChromaDB), and 3838 (orchestrator/web) are published to the host for debugging and native access.
- **WSL2 note:** When running outside Docker, use `$(hostname).local` or the Windows host IP to reach the SimConnect bridge.

### Volume Mounts and Data Persistence

| Volume | Type | Mount Point | Purpose |
|---|---|---|---|
| `whisper_cache` | Named volume | `/root/.cache/huggingface` | Caches downloaded faster-whisper models across container restarts |
| `./data/chroma_db` | Bind mount | `/chroma/chroma` | ChromaDB persistent storage; survives `docker compose down` |

The orchestrator container does not mount persistent data by default. In dev mode, the source code is bind-mounted read-only for hot-reload:

```yaml
volumes:
  - ./orchestrator/orchestrator:/app/orchestrator:ro
```

### GPU Passthrough for Whisper

To enable NVIDIA GPU acceleration for Whisper, uncomment the `deploy` block in `docker-compose.yml`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

Prerequisites:
- NVIDIA GPU with CUDA support
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed
- Docker Desktop configured to use the NVIDIA runtime

This dramatically reduces Whisper transcription latency, especially with the `medium` model.
