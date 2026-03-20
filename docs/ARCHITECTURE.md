# MERLIN -- Architecture Documentation

Technical deep-dive into MERLIN's system design, component responsibilities, data flows, and implementation decisions.

---

## System Architecture

```
+-------------------+          SimConnect (COM)           +------------------------+
|                   | <---------------------------------> |                        |
|   MSFS 2024       |   Position, Attitude, Speeds,       |   SimConnect Bridge    |
|   (Simulator)     |   Engines, Fuel, Environment,       |   (C# / .NET 8)       |
|                   |   Autopilot, Radios, Surfaces       |                        |
+-------------------+                                     +----------+-------------+
                                                                     |
                                                          WebSocket (JSON)
                                                          ws://localhost:8080
                                                                     |
                              +--------------------------------------+-----------------------------+
                              |                                                                    |
                              |                    Orchestrator (Python 3.11+)                      |
                              |                                                                    |
                              |   +----------------+    +------------------+    +--------------+    |
                              |   |  SimConnect     |    |  Claude Client   |    |  Flight      |    |
                              |   |  Client         |    |  (Anthropic API) |    |  Phase       |    |
                              |   |  (websockets)   |    |  Tool dispatch   |    |  Detector    |    |
                              |   +----------------+    +------------------+    +--------------+    |
                              |                                                                    |
                              |   +----------------+    +------------------+    +--------------+    |
                              |   |  Voice Input    |    |  Voice Output    |    |  Screen      |    |
                              |   |  (Whisper STT)  |    |  (ElevenLabs)    |    |  Capture     |    |
                              |   |  PTT / VAD      |    |  Streaming TTS   |    |  (mss)       |    |
                              |   +----------------+    +------------------+    +--------------+    |
                              |                                                                    |
                              |   +----------------+    +------------------+                        |
                              |   |  Context Store  |    |  Tool Functions  |                        |
                              |   |  (ChromaDB)     |    |  get_sim_state   |                        |
                              |   |  RAG pipeline   |    |  lookup_airport  |                        |
                              |   +----------------+    |  search_manual   |                        |
                              |                          |  get_checklist   |                        |
                              |                          |  create_flight.. |                        |
                              |                          +------------------+                        |
                              +--------------------------------------------------------------------+
                                        |                          |
                           +------------+                 +--------+--------+
                           |                              |                 |
                    +------+------+                +------+------+   +------+------+
                    |  Whisper    |                |  ChromaDB   |   |  Aviation   |
                    |  ASR Server |                |  (Docker)   |   |  API (FAA)  |
                    |  (Docker)   |                |  Port 8000  |   |  (HTTP)     |
                    |  Port 9000  |                +-------------+   +-------------+
                    +-------------+
```

### Data Flow: Voice Interaction

```
Microphone -> sounddevice (PCM capture)
           -> VoiceInput (PTT or VAD recording)
           -> WAV bytes
           -> Whisper HTTP API (localhost:9000/asr)
           -> Transcribed text
           -> Orchestrator conversation loop
           -> Claude API (with system prompt + telemetry + RAG context + tools)
           -> Streamed response text
           -> Console output (print)
           -> ElevenLabs TTS API (streaming synthesis)
           -> PCM audio playback via sounddevice
           -> Speakers / headset
```

### Data Flow: Simulator Telemetry

```
MSFS 2024 SimConnect
  -> SimConnectManager (C#) polls at 30Hz (position/attitude/speeds)
     and 1Hz (autopilot/radios/fuel/surfaces/environment/engines)
  -> Assembles SimState object
  -> Serializes to JSON
  -> TelemetryWebSocketServer broadcasts to all connected clients
  -> SimConnectClient (Python) receives via websockets
  -> Deserializes into Pydantic SimState model
  -> FlightPhaseDetector evaluates telemetry and updates phase
  -> SimState injected into Claude system prompt on each interaction
```

---

## SimConnect Bridge

**Language:** C# / .NET 8
**Entry point:** `simconnect-bridge/Program.cs`
**Key classes:** `SimConnectManager`, `TelemetryWebSocketServer`

### Why Out-of-Process

The SimConnect bridge runs as a separate native Windows executable, not a WASM module or in-process plugin. This is Microsoft's recommended approach for third-party tools and provides:

- **Crash isolation.** If the bridge crashes, MSFS continues running. If MSFS crashes, the bridge detects the disconnect and waits for reconnection.
- **Language flexibility.** The bridge speaks WebSocket/JSON, so any language can consume telemetry.
- **Independent deployment.** The bridge can be restarted without touching the sim.

SimConnect requires native Windows COM interop, which is why the bridge cannot run inside Docker or WSL.

### Data Definition Groups and Poll Rates

The bridge registers three data definition groups with SimConnect, each polled at a different rate:

| Group | Poll Rate | Variables | Rationale |
|---|---|---|---|
| `HighFrequency` | 30 Hz | Latitude, longitude, altitude (MSL/AGL), pitch, bank, heading (true/magnetic), IAS, TAS, ground speed, Mach, vertical speed | Flight-critical parameters that change rapidly |
| `LowFrequency` | 1 Hz | Autopilot state, radio frequencies, fuel quantity/weight, gear/flaps/spoilers, wind, visibility, temperature, barometer | Slowly-changing or configuration data |
| `EngineData` | 1 Hz | RPM, manifold pressure, fuel flow, EGT, oil temp, oil pressure (x4 engines) | Engine parameters for up to 4 engines |
| `AircraftTitle` | 1 Hz | Aircraft title string (256 chars) | Identifies the loaded aircraft |

A separate message pump timer runs at 100 Hz (10ms interval) to call `SimConnect.ReceiveMessage()`, which is required for out-of-process clients to receive callbacks.

### WebSocket Protocol

The bridge uses [Fleck](https://github.com/statianzo/Fleck) for the WebSocket server. JSON serialization uses `System.Text.Json` with `snake_case` naming policy.

**Connection:** Clients connect to `ws://<host>:8080` (configurable in `appsettings.json`).

**Broadcast:** On every state update, the server broadcasts the full `SimState` JSON to all connected clients. Clients with field subscriptions receive a filtered subset.

**Client requests and server responses are documented in [API.md](API.md).**

### Thread Safety

- `SimConnectManager` uses a `lock` around all state mutations in `OnRecvSimobjectData`.
- Timer callbacks (`_highFreqTimer`, `_lowFreqTimer`, `_messageTimer`) run on thread pool threads; the lock serializes access to `CurrentState`.
- `TelemetryWebSocketServer` uses `ConcurrentDictionary` for client tracking. Broadcast iterates a snapshot of connected clients.
- The `StateUpdated` event fires inside the lock; subscribers should not block.

---

## Orchestrator

**Language:** Python 3.11+
**Entry point:** `orchestrator/orchestrator/main.py`
**Key class:** `Orchestrator`

### Async Architecture

The orchestrator is fully async, built on `asyncio`. The main event loop drives:

1. **SimConnectClient** -- maintains a WebSocket connection with a background `_listen_loop` task that receives state updates.
2. **Conversation loop** -- alternates between gathering user input (text or voice) and streaming Claude responses.
3. **CaptureManager** -- runs an optional background task capturing screenshots at the configured FPS.
4. **VoiceOutput** -- TTS playback is dispatched as fire-and-forget tasks via `asyncio.create_task`.

Blocking operations (audio recording, Whisper inference, audio playback) are offloaded to the default thread pool executor via `run_in_executor`.

### Conversation Management

The `ClaudeClient` maintains a rolling conversation history as a list of message dicts compatible with the Anthropic API format. History is trimmed to the most recent 50 message pairs (`_max_history = 50`) to stay within context limits.

Each conversation turn:

1. Fetches the current `SimState` from the bridge (or uses a default empty state if disconnected).
2. Queries the `ContextStore` for documents relevant to the current aircraft and flight phase.
3. Builds the system prompt: MERLIN persona + telemetry summary + relevant RAG excerpts.
4. Appends the user message (with optional vision image) to conversation history.
5. Streams the response from Claude, yielding text chunks for real-time display.
6. If Claude requests tool use, executes tools and feeds results back in an agentic loop.

### Context Window Assembly

The system prompt is assembled dynamically on each turn:

```
[MERLIN Persona]
--- CURRENT FLIGHT STATE ---
Phase: CRUISE | Alt: 8500ft | IAS: 120kt | HDG: 270° | VS: +0fpm | GS: 135kt
Aircraft: Cessna 172 Skyhawk
On ground: False
Autopilot: HDG 270 | ALT 8500 | VS +0
Weather: Wind 180°/10kt | Vis 10sm | Temp 5°C | QNH 29.92"Hg
--- RELEVANT REFERENCE MATERIAL ---
[data/manuals/c172s_poh.txt]
<first 500 chars of most relevant chunk>
...
```

This keeps Claude grounded in the current situation without consuming excessive tokens.

### Tool Use Flow

Claude can call tools mid-response. The flow:

1. Claude's response includes `tool_use` content blocks alongside text.
2. The orchestrator collects all tool use blocks from the streamed response.
3. If the stop reason is `tool_use`, the orchestrator executes each tool.
4. Tool results are appended to the conversation as `tool_result` messages.
5. The conversation loops back to Claude, which incorporates the tool results into its continued response.
6. This loop repeats until Claude's stop reason is `end_turn` (no more tools needed).

**Implemented tools:**

| Tool | Function | Data Source |
|---|---|---|
| `get_sim_state` | Full telemetry snapshot | SimConnect bridge via WebSocket |
| `lookup_airport` | Airport info by ICAO code | Aviation API (HTTP) |
| `search_manual` | RAG query on ingested documents | ChromaDB |
| `get_checklist` | Phase-appropriate checklist | Context store with generic fallback |
| `create_flight_plan` | Draft route between airports | Airport lookups + route structure |

### Flight Phase Detection

The `FlightPhaseDetector` uses a state-machine approach with hysteresis. It evaluates telemetry on each state update and requires 3 consecutive detections of a new phase before transitioning (prevents oscillation).

**Decision logic (simplified):**

```
On ground?
  +-- Ground speed < 5 kt -> PREFLIGHT or LANDED (depending on history)
  +-- Ground speed 5-40 kt -> TAXI
  +-- Ground speed >= 40 kt -> TAKEOFF

Airborne?
  +-- AGL < 200 ft, gear down, descending -> LANDING
  +-- AGL < 3000 ft, gear down -> APPROACH or DESCENT
  +-- Vertical speed > +300 fpm -> CLIMB
  +-- Vertical speed < -300 fpm -> DESCENT
  +-- Vertical speed within +/- 200 fpm -> CRUISE
```

**Thresholds are configurable** via the `PhaseThresholds` dataclass.

---

## RAG Pipeline

### Document Ingestion Flow

```
Text file on disk
  -> ContextStore.ingest_document()
  -> Read file as UTF-8 text
  -> Split into overlapping chunks (default: 1000 chars, 200 char overlap)
  -> Generate deterministic IDs (SHA-256 of path + chunk index)
  -> Upsert into ChromaDB collection "merlin_docs" with metadata
```

### Chunking Strategy

Character-based sliding window with overlap:

- **Chunk size:** 1000 characters (configurable via `RAG_CHUNK_SIZE`)
- **Overlap:** 200 characters (configurable via `RAG_CHUNK_OVERLAP`)
- Empty chunks are discarded

This is a simple approach that works well for structured technical documents (POHs, checklists). The overlap ensures that sentences split across chunk boundaries are still retrievable.

### Embedding Model

ChromaDB's default embedding function is used (Sentence Transformers `all-MiniLM-L6-v2` when configured via `EMBEDDING_MODEL`). The collection uses cosine distance (`hnsw:space: cosine`).

### Query-Time Context Selection

When the orchestrator prepares a conversation turn, `ContextStore.get_relevant_context()`:

1. Maps the current flight phase to a list of topic keywords (e.g., APPROACH maps to `["approach", "ILS", "VOR", "RNAV", "minimums", "go-around"]`).
2. Builds a query string: `"{aircraft_title} {topic keywords}"`.
3. First attempts a filtered query matching `aircraft_type` metadata.
4. Falls back to an unfiltered query if no aircraft-specific results are found.
5. Returns up to `n_results` (default 5) document chunks with content, metadata, and distance scores.

---

## Voice Pipeline

### Speech-to-Text Path

```
Microphone
  -> sounddevice InputStream (16kHz, mono, float32)
  -> PTT: records until stop_recording() called
     VAD: records until silence detected (1.5s of sub-threshold RMS)
  -> numpy array of audio samples
  -> Convert to WAV bytes (int16 PCM)
  -> POST to Whisper HTTP API (localhost:9000/asr)
  -> Returns transcribed text
```

The Whisper HTTP service (`onerahmet/openai-whisper-asr-webservice`) provides an OpenAI-compatible `/asr` endpoint. The `WhisperClient` includes retry logic with exponential backoff (up to 3 attempts).

As a fallback, the `VoiceInput` class can load Whisper locally via the `whisper` Python package if the HTTP service is unavailable.

### Text-to-Speech Path

```
Claude response text
  -> POST to ElevenLabs API (/v1/text-to-speech/{voice_id})
  -> Returns audio bytes (MP3 or PCM)
  -> Decode to float32 numpy array
  -> Play via sounddevice (24kHz)
```

The `VoiceOutput` class also supports a `speak_streamed` method that buffers Claude's streaming text output and sends complete sentences to TTS as they arrive, reducing perceived latency.

### Latency Budget

Approximate latencies for a typical voice interaction (base Whisper model, CPU):

| Stage | Typical Latency |
|---|---|
| Audio recording | Variable (user speech duration) |
| Whisper transcription | 1-3 seconds (base model, CPU) |
| Claude API (time to first token) | 0.5-2 seconds |
| Claude API (full response stream) | 2-8 seconds |
| ElevenLabs TTS synthesis | 0.5-1.5 seconds |
| Audio playback | Streams as audio arrives |
| **Total (recording end to first audio)** | **~3-7 seconds** |

GPU acceleration for Whisper can reduce transcription to under 500ms. Using the `tiny` model further cuts transcription time at the cost of accuracy.

### Push-to-Talk vs VAD

| Mode | How It Works | Best For |
|---|---|---|
| **Push-to-talk (PTT)** | Records while active; stops when `stop_recording()` is called (Enter key in the current implementation) | Noisy environments, precise control |
| **Voice Activity Detection (VAD)** | Starts on speech detection (RMS above threshold), ends after 1.5s of silence | Hands-free operation |

VAD sensitivity is controlled by `VAD_SENSITIVITY` in `.env` (maps to `vad_threshold` in `VoiceInput`).

---

## Docker Services

### Service Topology

```
+---------------------------------------------------+
|  Docker Network: merlin (bridge)                  |
|                                                    |
|   +-----------+   +-----------+   +------------+  |
|   |  whisper  |   | chromadb  |   |orchestrator|  |
|   |  :9000    |   |  :8000    |   |   :8081    |  |
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
- Ports 9000 (Whisper), 8000 (ChromaDB), and 8081 (orchestrator) are published to the host for debugging and native orchestrator access.

### Volume Mounts and Data Persistence

| Volume | Type | Mount Point | Purpose |
|---|---|---|---|
| `whisper_cache` | Named volume | `/root/.cache/whisper` | Caches downloaded Whisper models across container restarts |
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

This dramatically reduces Whisper transcription latency, especially with larger models.
