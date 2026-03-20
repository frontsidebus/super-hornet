"""Shared fixtures for MERLIN integration tests."""

from __future__ import annotations

import asyncio
import io
import json
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import httpx
import numpy as np
import pytest
import pytest_asyncio
import websockets
from websockets.server import serve as ws_serve

# ---------------------------------------------------------------------------
# Docker compose helpers
# ---------------------------------------------------------------------------

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _compose_cmd(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


@pytest.fixture(scope="session")
def docker_whisper(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    """Start the Whisper container and yield its base URL.

    Tears down after the test session completes.
    """
    service = "whisper"
    url = "http://localhost:9000"

    subprocess.run(_compose_cmd("up", "-d", service), check=True, capture_output=True)
    _wait_for_http(url + "/docs", timeout=120)

    yield url

    subprocess.run(_compose_cmd("down", service), capture_output=True)


@pytest.fixture(scope="session")
def docker_chromadb(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    """Start the ChromaDB container and yield its base URL."""
    service = "chromadb"
    url = "http://localhost:8000"

    subprocess.run(_compose_cmd("up", "-d", service), check=True, capture_output=True)
    _wait_for_http(url + "/api/v1/heartbeat", timeout=60)

    yield url

    subprocess.run(_compose_cmd("down", service), capture_output=True)


def _wait_for_http(url: str, timeout: float = 60, interval: float = 2.0) -> None:
    """Poll an HTTP endpoint until it returns 200 or timeout is reached."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_err = exc
        time.sleep(interval)
    raise TimeoutError(
        f"Service at {url} did not become ready within {timeout}s. Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Sample audio generation
# ---------------------------------------------------------------------------


def _make_wav_bytes(
    duration: float = 1.0,
    sample_rate: int = 16000,
    frequency: float = 440.0,
) -> bytes:
    """Generate a WAV file (mono, 16-bit PCM) with a sine wave tone."""
    num_samples = int(sample_rate * duration)
    t = np.linspace(0.0, duration, num_samples, endpoint=False)
    samples = (np.sin(2.0 * np.pi * frequency * t) * 32767 * 0.5).astype(np.int16)

    buf = io.BytesIO()
    # WAV header (44 bytes)
    data_size = num_samples * 2  # 16-bit = 2 bytes per sample
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(samples.tobytes())
    return buf.getvalue()


@pytest.fixture()
def sample_wav_bytes() -> bytes:
    """A short 1-second 440Hz WAV for STT testing."""
    return _make_wav_bytes(duration=1.0, frequency=440.0)


@pytest.fixture()
def silent_wav_bytes() -> bytes:
    """A short silent WAV (amplitude = 0)."""
    return _make_wav_bytes(duration=0.5, frequency=0.0)


@pytest.fixture()
def long_wav_bytes() -> bytes:
    """A 5-second WAV for longer transcription tests."""
    return _make_wav_bytes(duration=5.0, frequency=440.0)


# ---------------------------------------------------------------------------
# Sample documents for context store
# ---------------------------------------------------------------------------

SAMPLE_DOCUMENT_CONTENT = """\
Cessna 172S Skyhawk Information Manual

SECTION 4 - NORMAL PROCEDURES

BEFORE TAKEOFF CHECK
1. Parking Brake - SET
2. Cabin Doors - CLOSED and LOCKED
3. Flight Controls - FREE and CORRECT
4. Flight Instruments - CHECK and SET
5. Fuel Quantity - CHECK
6. Mixture - RICH (below 3000 feet)
7. Fuel Selector Valve - BOTH
8. Elevator Trim - SET for takeoff
9. Throttle - 1700 RPM
10. Magnetos - CHECK (RPM drop not to exceed 150 RPM on each; 50 RPM differential)
11. Engine Gauges - CHECK
12. Suction Gauge - CHECK
13. Throttle - IDLE
14. Radios / Avionics - SET
15. Autopilot - OFF
16. Flaps - SET for takeoff (0-10 degrees)
17. Parking Brake - RELEASE

V-SPEEDS:
Vr  (Rotation):       55 KIAS
Vx  (Best Angle):     62 KIAS
Vy  (Best Rate):      74 KIAS
Va  (Maneuvering):    105 KIAS at max gross weight
Vfe (Max Flap Ext):   110 KIAS (10 degrees), 85 KIAS (full)
Vno (Max Structural):  129 KIAS
Vne (Never Exceed):   163 KIAS

CRUISE PERFORMANCE:
Altitude: 8000 ft  |  Power: 75%  |  TAS: 122 kt  |  Fuel Flow: 8.6 GPH
"""


@pytest.fixture()
def sample_document(tmp_path: Path) -> Path:
    """Write a sample aircraft manual to a temp file and return its path."""
    doc_path = tmp_path / "cessna172s_poh.txt"
    doc_path.write_text(SAMPLE_DOCUMENT_CONTENT, encoding="utf-8")
    return doc_path


@pytest.fixture()
def sample_document_metadata() -> dict[str, Any]:
    return {
        "aircraft_type": "Cessna 172S Skyhawk",
        "document_type": "POH",
        "section": "normal_procedures",
    }


# ---------------------------------------------------------------------------
# Mock WebSocket server (mimics the C# SimConnect bridge)
# ---------------------------------------------------------------------------

_DEFAULT_SIM_STATE: dict[str, Any] = {
    "timestamp": 1000.0,
    "aircraft_title": "Cessna 172S Skyhawk",
    "position": {
        "latitude": 28.4294,
        "longitude": -81.309,
        "altitude": 1000.0,
        "altitude_agl": 920.0,
    },
    "attitude": {"pitch": 2.0, "bank": 0.0, "heading": 270.0},
    "speeds": {
        "indicated": 110.0,
        "true_airspeed": 115.0,
        "ground_speed": 118.0,
        "mach": 0.17,
        "vertical_speed": 0.0,
    },
    "engine": {
        "rpm": [2300.0],
        "manifold_pressure": [24.0],
        "fuel_flow": [8.6],
        "egt": [1350.0],
        "oil_temp": [180.0],
        "oil_pressure": [60.0],
        "n1": [],
        "n2": [],
    },
    "autopilot": {
        "master": False,
        "heading_hold": False,
        "altitude_hold": False,
        "nav_hold": False,
        "approach_hold": False,
        "vertical_speed_hold": False,
        "set_heading": 270.0,
        "set_altitude": 3000.0,
        "set_speed": 0.0,
        "set_vertical_speed": 0.0,
    },
    "radios": {
        "com1_active": 124.8,
        "com1_standby": 121.5,
        "com2_active": 0.0,
        "com2_standby": 0.0,
        "nav1_active": 110.0,
        "nav1_standby": 0.0,
        "nav2_active": 0.0,
        "nav2_standby": 0.0,
        "transponder": 1200,
        "adf": 0.0,
    },
    "fuel": {"quantities": [21.0, 21.0], "total": 42.0, "total_weight": 252.0},
    "environment": {
        "wind_speed": 8.0,
        "wind_direction": 240.0,
        "visibility": 10.0,
        "temperature": 20.0,
        "pressure": 29.92,
        "precipitation": "none",
    },
    "surfaces": {
        "gear_down": True,
        "gear_retractable": False,
        "flaps_position": 0,
        "flaps_num_positions": 4,
        "spoilers_deployed": False,
        "parking_brake": False,
    },
    "flight_phase": "CRUISE",
    "on_ground": False,
    "sim_paused": False,
}


class MockSimConnectServer:
    """A lightweight WebSocket server that mimics the C# SimConnect bridge.

    Supports:
    - ``get_state`` requests (replies with current sim state)
    - ``subscribe`` requests (acknowledges with subscribe_ack)
    - Periodic ``state_update`` broadcasts
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port  # 0 = pick a free port
        self.sim_state: dict[str, Any] = dict(_DEFAULT_SIM_STATE)
        self._server: Any = None
        self._clients: set[Any] = set()
        self._running = False

    async def start(self) -> int:
        """Start the mock server and return the bound port."""
        self._server = await ws_serve(
            self._handler, self.host, self.port
        )
        # Retrieve the actual port (important when port=0)
        bound_port = self._server.sockets[0].getsockname()[1]
        self.port = bound_port
        self._running = True
        return bound_port

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    async def broadcast_state_update(self) -> None:
        """Send a state_update message to all connected clients."""
        msg = json.dumps({"type": "state_update", "data": self.sim_state})
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                self._clients.discard(ws)

    async def _handler(self, ws: Any) -> None:
        self._clients.add(ws)
        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue

                msg_type = data.get("type", "")

                if msg_type == "get_state":
                    await ws.send(json.dumps(self.sim_state))

                elif msg_type == "subscribe":
                    fields = data.get("fields", [])
                    await ws.send(json.dumps({
                        "type": "subscribe_ack",
                        "fields": fields or ["all"],
                    }))

                else:
                    await ws.send(json.dumps({
                        "type": "error",
                        "message": f"Unknown request type: {msg_type}",
                    }))
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)


@pytest_asyncio.fixture()
async def mock_simconnect_server() -> AsyncGenerator[MockSimConnectServer, None]:
    """Start a mock SimConnect WebSocket server for the duration of a test."""
    server = MockSimConnectServer()
    await server.start()
    yield server
    await server.stop()
