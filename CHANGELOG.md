# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-03-21

### Added
- faster-whisper STT backend (CTranslate2 `medium` model) replacing stock Whisper `small` model
- Silero VAD neural voice activity detection with 400ms silence timeout (was 1.5s RMS threshold)
- Aviation TTS preprocessor with ICAO digit pronunciation for flight levels, headings, frequencies, runway designators, and squawk codes
- TTS phrase caching: common responses pre-generated at startup for zero-latency playback
- ElevenLabs WebSocket streaming for TTS (persistent connection per response)
- HTTP connection pooling for Whisper client via shared httpx.AsyncClient
- TLS pre-warm for ElevenLabs at startup to eliminate first-request handshake delay

### Changed
- Whisper Docker image switched from onerahmet/openai-whisper-asr-webservice to fedirz/faster-whisper-server
- Whisper API endpoint changed to OpenAI-compatible `/v1/audio/transcriptions`
- Clause chunking threshold reduced from 120 to 50 characters for faster TTS streaming
- Skip ffmpeg conversion: send WebM audio direct to Whisper instead of converting to WAV
- ElevenLabs TTS uses WebSocket streaming instead of per-sentence REST calls

### Performance
- Estimated end-to-end voice latency reduced from ~3.3s to ~1.2-1.5s
- faster-whisper CTranslate2 backend is 3-4x faster than stock Whisper with identical accuracy
- Silero VAD reduces silence detection timeout from 1.5s to 400ms
- Phrase cache eliminates TTS round-trip for common responses (Roger, Copy that, etc.)

### Tests
- Test count increased from 287 to 361

## [1.0.0] - 2026-03-21

### Added
- Real-time game state telemetry via log parsing and screen capture
- Web-based cockpit UI with live telemetry display (FastAPI + vanilla JS)
- Voice input via Whisper STT with aviation vocabulary prompting and audio preprocessing
- Voice output via ElevenLabs TTS with markdown sanitizer for clean speech
- AI copilot powered by Claude with flight-phase-aware response styles
- Dynamic token budgeting (short/normal/briefing response lengths)
- Push-to-talk (spacebar) and barge-in interruption support
- ChromaDB RAG store for aircraft manuals with query caching
- Game activity detection state machine with hysteresis
- Health monitoring for all subsystems with graceful degradation
- Connection quality indicator and auto-reconnect with exponential backoff
- Delta detection to skip duplicate telemetry messages
- 287 unit tests covering all modules

### Architecture
- Game state aggregation from perception modules (log parser, vision)
- Python orchestrator with async/await throughout
- FastAPI web server bridging browser to all backend services
- Docker Compose for Whisper and ChromaDB services
- WSL2-compatible networking with configurable bridge host
