# MERLIN -- API & Protocol Reference

Complete reference for the SimConnect bridge WebSocket API, orchestrator tool definitions, configuration variables, and the checklist YAML format.

---

## SimConnect Bridge WebSocket API

### Connection

```
ws://localhost:8080
```

The host and port are configurable in `simconnect-bridge/appsettings.json`:

```json
{
  "WebSocket": {
    "Port": 8080,
    "Host": "0.0.0.0"
  }
}
```

The server accepts any number of concurrent WebSocket clients. Each client receives broadcast state updates and can send request messages.

---

### Client-to-Server Messages

#### `get_state`

Request the current simulator state. The server acknowledges and delivers the full state on the next broadcast cycle.

```json
{
  "type": "get_state"
}
```

**Response:**

```json
{
  "type": "state_response",
  "message": "Full state will be delivered on next update cycle."
}
```

#### `subscribe`

Subscribe to a subset of state fields. After subscribing, broadcasts to this client will only include the requested top-level fields (plus `timestamp` and `connected`, which are always included).

```json
{
  "type": "subscribe",
  "fields": ["position", "speeds", "attitude"]
}
```

**Valid field names:** `position`, `attitude`, `speeds`, `engines`, `autopilot`, `radios`, `fuel`, `surfaces`, `environment`, `aircraft`

**Response:**

```json
{
  "type": "subscribe_ack",
  "fields": ["position", "speeds", "attitude"]
}
```

To receive all fields again, send a subscribe with `null` or omit `fields`:

```json
{
  "type": "subscribe",
  "fields": null
}
```

---

### Server-to-Client Messages

#### State Broadcast

Sent automatically on every telemetry update (up to 30 times per second for high-frequency data). The full schema:

```json
{
  "timestamp": "2026-03-20T15:30:00.000Z",
  "connected": true,
  "aircraft": "Cessna Skyhawk G1000 Asobo",

  "position": {
    "latitude": 40.639722,
    "longitude": -73.778889,
    "altitude_msl": 8500.0,
    "altitude_agl": 8067.0
  },

  "attitude": {
    "pitch": 2.5,
    "bank": -1.2,
    "heading_true": 275.3,
    "heading_magnetic": 262.1
  },

  "speeds": {
    "indicated_airspeed": 120.0,
    "true_airspeed": 132.0,
    "ground_speed": 135.0,
    "mach": 0.182,
    "vertical_speed": 50.0
  },

  "engines": {
    "engine_count": 1,
    "engines": [
      {
        "rpm": 2350.0,
        "manifold_pressure": 23.5,
        "fuel_flow_gph": 8.2,
        "egt": 1350.0,
        "oil_temp": 185.0,
        "oil_pressure": 60.0
      },
      { "rpm": 0, "manifold_pressure": 0, "fuel_flow_gph": 0, "egt": 0, "oil_temp": 0, "oil_pressure": 0 },
      { "rpm": 0, "manifold_pressure": 0, "fuel_flow_gph": 0, "egt": 0, "oil_temp": 0, "oil_pressure": 0 },
      { "rpm": 0, "manifold_pressure": 0, "fuel_flow_gph": 0, "egt": 0, "oil_temp": 0, "oil_pressure": 0 }
    ]
  },

  "autopilot": {
    "master": false,
    "heading": 270.0,
    "altitude": 8500.0,
    "vertical_speed": 0.0,
    "airspeed": 120.0
  },

  "radios": {
    "com1": 124.0,
    "com2": 121.5,
    "nav1": 111.5,
    "nav2": 0.0
  },

  "fuel": {
    "total_gallons": 38.5,
    "total_weight_lbs": 231.0
  },

  "surfaces": {
    "gear_handle": true,
    "flaps_percent": 0.0,
    "spoilers_percent": 0.0
  },

  "environment": {
    "wind_speed_kts": 10.0,
    "wind_direction": 180.0,
    "visibility_sm": 10.0,
    "temperature_c": 5.0,
    "barometer_inhg": 29.92
  }
}
```

#### Field Reference

**position**

| Field | Type | Unit | Description |
|---|---|---|---|
| `latitude` | float | degrees | WGS84 latitude |
| `longitude` | float | degrees | WGS84 longitude |
| `altitude_msl` | float | feet | Altitude above mean sea level |
| `altitude_agl` | float | feet | Altitude above ground level |

**attitude**

| Field | Type | Unit | Description |
|---|---|---|---|
| `pitch` | float | degrees | Nose up positive |
| `bank` | float | degrees | Right wing down positive |
| `heading_true` | float | degrees | True heading (0-360) |
| `heading_magnetic` | float | degrees | Magnetic heading (0-360) |

**speeds**

| Field | Type | Unit | Description |
|---|---|---|---|
| `indicated_airspeed` | float | knots | Indicated airspeed (IAS) |
| `true_airspeed` | float | knots | True airspeed (TAS) |
| `ground_speed` | float | knots | Ground speed |
| `mach` | float | Mach | Mach number |
| `vertical_speed` | float | ft/min | Vertical speed (positive = climb) |

**engines**

| Field | Type | Unit | Description |
|---|---|---|---|
| `engine_count` | int | -- | Number of active engines (inferred from RPM > 0) |
| `engines` | array[4] | -- | Per-engine parameters (always 4 slots) |
| `engines[].rpm` | float | RPM | Engine RPM |
| `engines[].manifold_pressure` | float | inHg | Manifold pressure |
| `engines[].fuel_flow_gph` | float | gal/hr | Fuel flow rate |
| `engines[].egt` | float | Rankine | Exhaust gas temperature |
| `engines[].oil_temp` | float | Rankine | Oil temperature |
| `engines[].oil_pressure` | float | psf | Oil pressure |

**autopilot**

| Field | Type | Unit | Description |
|---|---|---|---|
| `master` | bool | -- | Autopilot master switch engaged |
| `heading` | float | degrees | Selected heading |
| `altitude` | float | feet | Selected altitude |
| `vertical_speed` | float | ft/min | Selected vertical speed |
| `airspeed` | float | knots | Selected airspeed |

**radios**

| Field | Type | Unit | Description |
|---|---|---|---|
| `com1` | float | MHz | COM1 active frequency |
| `com2` | float | MHz | COM2 active frequency |
| `nav1` | float | MHz | NAV1 active frequency |
| `nav2` | float | MHz | NAV2 active frequency |

**fuel**

| Field | Type | Unit | Description |
|---|---|---|---|
| `total_gallons` | float | gallons | Total fuel quantity |
| `total_weight_lbs` | float | pounds | Total fuel weight |

**surfaces**

| Field | Type | Unit | Description |
|---|---|---|---|
| `gear_handle` | bool | -- | Landing gear handle position (true = down) |
| `flaps_percent` | float | percent | Trailing edge flap deflection (0-100) |
| `spoilers_percent` | float | percent | Spoiler handle position (0-100) |

**environment**

| Field | Type | Unit | Description |
|---|---|---|---|
| `wind_speed_kts` | float | knots | Ambient wind speed |
| `wind_direction` | float | degrees | Ambient wind direction (from) |
| `visibility_sm` | float | statute miles | Visibility |
| `temperature_c` | float | celsius | Outside air temperature |
| `barometer_inhg` | float | inHg | Barometric pressure |

#### Error Response

Sent when the server cannot parse a client message or the request type is unknown:

```json
{
  "type": "error",
  "message": "Unknown request type: foo"
}
```

---

## Orchestrator Tool Definitions

These are the tools registered with the Claude API. Claude can call them mid-response; the orchestrator executes them and feeds results back.

### `get_sim_state`

Retrieve the current simulator state.

**Parameters:** None

**Returns:**

```json
{
  "aircraft": "Cessna Skyhawk G1000 Asobo",
  "flight_phase": "CRUISE",
  "position": {
    "lat": 40.639722,
    "lon": -73.778889,
    "altitude_msl": 8500,
    "altitude_agl": 8067
  },
  "attitude": {
    "pitch": 2.5,
    "bank": -1.2,
    "heading": 262
  },
  "speeds": {
    "indicated": 120,
    "true_airspeed": 132,
    "ground_speed": 135,
    "mach": 0.182,
    "vertical_speed": 50
  },
  "engine": {
    "rpm": [2350],
    "fuel_flow": [8.2],
    "oil_temp": [185],
    "oil_pressure": [60]
  },
  "autopilot": {
    "engaged": false,
    "heading": 270,
    "altitude": 8500
  },
  "fuel": {
    "total_gallons": 38.5,
    "total_weight_lbs": 231.0
  },
  "environment": {
    "wind": "180° at 10kt",
    "visibility_sm": 10.0,
    "temperature_c": 5,
    "altimeter_inhg": 29.92
  },
  "surfaces": {
    "gear_down": true,
    "flaps": 0,
    "spoilers": false
  },
  "on_ground": false
}
```

### `lookup_airport`

Look up airport information by ICAO or FAA identifier.

**Parameters:**

| Name | Type | Required | Description |
|---|---|---|---|
| `identifier` | string | Yes | Airport ICAO or FAA code (e.g., `KJFK`, `LAX`) |

Three-letter identifiers without a `K` prefix are automatically prefixed (e.g., `LAX` becomes `KLAX`).

**Returns:**

```json
{
  "identifier": "KJFK",
  "name": "JOHN F KENNEDY INTL",
  "city": "NEW YORK",
  "state": "NEW YORK",
  "elevation": "13",
  "latitude": "40-38-23.0000N",
  "longitude": "073-46-44.0000W",
  "status": "O"
}
```

On error:

```json
{
  "error": "Airport KXYZ not found"
}
```

### `search_manual`

Search the aircraft operating manual and aviation knowledge base via vector similarity.

**Parameters:**

| Name | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Natural language search query |

The search is automatically filtered to the current aircraft type when available.

**Returns:** Array of up to 5 matching document chunks:

```json
[
  {
    "content": "The maximum structural cruising speed (Vno) is 129 KIAS...",
    "source": "data/manuals/c172s_poh.txt"
  },
  {
    "content": "Never exceed speed (Vne) is 163 KIAS. Do not exceed...",
    "source": "data/manuals/c172s_poh.txt"
  }
]
```

### `get_checklist`

Get the checklist for a given flight phase.

**Parameters:**

| Name | Type | Required | Description |
|---|---|---|---|
| `phase` | string | Yes | Flight phase name |

**Valid phase values:** `PREFLIGHT`, `TAXI`, `TAKEOFF`, `CLIMB`, `CRUISE`, `DESCENT`, `APPROACH`, `LANDING`, `LANDED`

Returns aircraft-specific checklists from the context store when available, falling back to generic defaults.

**Returns (aircraft-specific):**

```json
{
  "phase": "TAKEOFF",
  "aircraft": "Cessna 172 Skyhawk",
  "source": "aircraft_manual",
  "checklist": "Throttle — FULL open smoothly\nEngine instruments — Monitor..."
}
```

**Returns (generic fallback):**

```json
{
  "phase": "TAKEOFF",
  "aircraft": "generic",
  "source": "default",
  "items": [
    "Flaps - SET FOR TAKEOFF",
    "Trim - SET",
    "Mixture - RICH (or as required)",
    "Fuel pump - ON",
    "Lights - ON",
    "Doors - SECURE",
    "Controls - FREE AND CORRECT",
    "Takeoff clearance - OBTAINED"
  ]
}
```

### `create_flight_plan`

Create a basic flight plan between two airports.

**Parameters:**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `departure` | string | Yes | -- | Departure airport identifier |
| `destination` | string | Yes | -- | Destination airport identifier |
| `altitude` | integer | No | 5000 | Planned cruise altitude in feet MSL |
| `route` | string | No | `""` | Optional waypoints separated by spaces |

**Returns:**

```json
{
  "departure": {
    "identifier": "KLAX",
    "name": "LOS ANGELES INTL",
    "elevation": "128"
  },
  "destination": {
    "identifier": "KSFO",
    "name": "SAN FRANCISCO INTL",
    "elevation": "13"
  },
  "cruise_altitude": 8000,
  "route": "KLAX KSJC KSFO",
  "waypoints": ["KLAX", "KSJC", "KSFO"],
  "status": "draft",
  "notes": "This is a draft plan. Verify airways, altitudes, and NOTAMs before use."
}
```

---

## Context Store Query Interface

The `ContextStore` class provides the RAG query interface. While not an HTTP API, it is the programmatic interface for document retrieval.

### `ingest_document(path, metadata, chunk_size, chunk_overlap)`

Ingest a text file into the vector store.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` or `Path` | -- | Path to the text file |
| `metadata` | `dict` | `None` | Additional metadata (e.g., `{"aircraft_type": "Cessna 172"}`) |
| `chunk_size` | `int` | 1000 | Characters per chunk |
| `chunk_overlap` | `int` | 200 | Overlap between consecutive chunks |

Returns the number of chunks ingested.

### `query(text, n_results, filters)`

Query the store by semantic similarity.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `text` | `str` | -- | Natural language query |
| `n_results` | `int` | 5 | Maximum results to return |
| `filters` | `dict` | `None` | Metadata filters (e.g., `{"aircraft_type": "Cessna 172"}`) |

Returns a list of dicts with `content`, `metadata`, and `distance` keys.

### `get_relevant_context(sim_state, n_results)`

Retrieve documents relevant to the current aircraft and flight phase. Automatically builds a query from the aircraft title and phase-specific topic keywords.

---

## Configuration Reference

All configuration is managed through environment variables loaded by `pydantic-settings`. Set them in your `.env` file.

### API Keys

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | -- | Anthropic API key for Claude |
| `ELEVENLABS_API_KEY` | No | `""` | ElevenLabs API key for TTS |

### Claude Settings

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLAUDE_MODEL` | No | `claude-sonnet-4-20250514` | Claude model identifier |
| `CLAUDE_MAX_TOKENS` | No | `4096` | Maximum tokens per response |
| `CLAUDE_TEMPERATURE` | No | `0.7` | Response temperature (0.0-1.0) |

### SimConnect Bridge

| Variable | Required | Default | Description |
|---|---|---|---|
| `SIMCONNECT_WS_HOST` | No | `localhost` | Bridge WebSocket host |
| `SIMCONNECT_WS_PORT` | No | `8765` | Bridge WebSocket port |
| `SIMCONNECT_BRIDGE_URL` | No | `ws://localhost:8080` | Full bridge WebSocket URL (used by orchestrator) |
| `SIMCONNECT_POLL_INTERVAL_MS` | No | `100` | Telemetry polling interval |

### Voice Pipeline -- STT

| Variable | Required | Default | Description |
|---|---|---|---|
| `WHISPER_MODEL` | No | `base.en` | Whisper model size (`tiny.en`, `base.en`, `small.en`, `medium.en`, `large-v3`) |
| `WHISPER_URL` | No | `http://localhost:9000` | Whisper ASR service URL |
| `AUDIO_INPUT_DEVICE` | No | `""` | Audio input device index (blank = system default) |
| `VAD_SENSITIVITY` | No | `0.6` | VAD sensitivity threshold (0.0-1.0) |
| `WAKE_WORD` | No | `merlin` | Wake word to activate MERLIN (blank = always on) |

### Voice Pipeline -- TTS

| Variable | Required | Default | Description |
|---|---|---|---|
| `ELEVENLABS_VOICE_ID` | No | `""` | Voice ID for TTS output |
| `ELEVENLABS_MODEL_ID` | No | `eleven_turbo_v2_5` | ElevenLabs model |
| `ELEVENLABS_STABILITY` | No | `0.5` | Voice stability (0.0-1.0) |
| `ELEVENLABS_SIMILARITY_BOOST` | No | `0.75` | Voice similarity (0.0-1.0) |
| `ELEVENLABS_STYLE` | No | `0.3` | Voice expressiveness (0.0-1.0) |

### Context Store

| Variable | Required | Default | Description |
|---|---|---|---|
| `CHROMA_PERSIST_DIR` | No | `./chroma_data` | ChromaDB storage directory |
| `EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | Embedding model for document chunks |
| `RAG_TOP_K` | No | `5` | Number of chunks per RAG query |
| `RAG_CHUNK_SIZE` | No | `1000` | Chunk size in characters |
| `RAG_CHUNK_OVERLAP` | No | `200` | Chunk overlap in characters |

### FAA Data

| Variable | Required | Default | Description |
|---|---|---|---|
| `AVIATION_API_BASE_URL` | No | `https://api.aviationapi.com/v1` | Aviation API base URL |
| `AIRPORT_CACHE_TTL` | No | `3600` | Airport data cache TTL in seconds |

### Screen Capture

| Variable | Required | Default | Description |
|---|---|---|---|
| `SCREEN_CAPTURE_ENABLED` | No | `false` | Enable screen capture |
| `SCREEN_CAPTURE_INTERVAL` | No | `2.0` | Seconds between captures |
| `SCREEN_CAPTURE_WIDTH` | No | `1280` | Capture target width (pixels) |
| `SCREEN_CAPTURE_WINDOW_TITLE` | No | `Microsoft Flight Simulator` | Window title for targeted capture |

### Logging

| Variable | Required | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` | No | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_DIR` | No | `./logs` | Log file directory |
| `LOG_TRANSCRIPTS` | No | `true` | Log conversation transcripts |

---

## Checklist YAML Format

Checklists are stored in `data/checklists/` as YAML files. The orchestrator and context store can ingest these for aircraft-specific checklist retrieval.

### Schema

```yaml
# Top-level metadata
aircraft_class: single_engine_piston    # Aircraft category identifier
version: "1.0"                          # Checklist version
author: MERLIN                          # Author name

# Phases (one or more)
phases:

  # Phase key (used for lookup)
  preflight:
    name: "Preflight Inspection"        # Human-readable phase name
    items:
      - item: "Weather briefing"        # Checklist action
        setting: "Obtained"             # Expected value/position
        remark: "Optional commentary."  # MERLIN's remark (~ for none)

      - item: "Fuel quantity"
        setting: "Sufficient for flight + reserves"
        remark: ~
```

### Phase Names

The following phase keys are used in the checklist files. They are more granular than the flight phase detector's output to support aircraft-specific procedures:

| Phase Key | Description | Maps to Flight Phase |
|---|---|---|
| `preflight` | Walkaround and document checks | PREFLIGHT |
| `before_start` | Cockpit setup before engine start | PREFLIGHT |
| `engine_start` | Engine start sequence | PREFLIGHT |
| `before_taxi` | Post-start, pre-taxi checks | TAXI |
| `before_takeoff` | Run-up and final checks | TAKEOFF |
| `takeoff` | Takeoff roll and initial climb | TAKEOFF |
| `climb` | Climb-out procedures | CLIMB |
| `cruise` | Level flight checks | CRUISE |
| `descent` | Descent preparation | DESCENT |
| `approach` | Approach configuration | APPROACH |
| `before_landing` | Final approach checks | LANDING |
| `after_landing` | Post-landing procedures | LANDED |
| `shutdown` | Engine shutdown and securing | LANDED |

### Item Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `item` | string | Yes | The action or system to check |
| `setting` | string | Yes | The expected state, position, or value |
| `remark` | string or null | No | Optional MERLIN commentary. Use `~` (YAML null) for no remark. |

### Adding a Custom Checklist

1. Create a new YAML file in `data/checklists/`:

   ```bash
   touch data/checklists/my_aircraft.yaml
   ```

2. Follow the schema above. Use the existing `generic_single_engine.yaml` or `generic_jet.yaml` as a template.

3. Set `aircraft_class` to a descriptive identifier (e.g., `baron_58`, `citation_cj4`).

4. Ingest the file into the context store so MERLIN can retrieve it:

   ```python
   await store.ingest_document(
       "data/checklists/my_aircraft.yaml",
       metadata={"aircraft_type": "Beechcraft Baron 58"}
   )
   ```

5. When flying that aircraft, MERLIN will prefer the aircraft-specific checklist over the generic defaults.

### Included Checklists

| File | Aircraft Class | Applicable To |
|---|---|---|
| `generic_single_engine.yaml` | `single_engine_piston` | Cessna 172, Cessna 152, DA40, PA-28, SR22, and similar |
| `generic_jet.yaml` | `jet_turbofan` | Boeing 747-8/787, Airbus A320neo, Citation CJ4, and similar |
