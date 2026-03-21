# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-21

### Added
- Real-time SimConnect telemetry via event-driven bridge with auto-reconnect
- Web-based cockpit UI with live telemetry display (FastAPI + vanilla JS)
- Voice input via Whisper STT with aviation vocabulary prompting and audio preprocessing
- Voice output via ElevenLabs TTS with markdown sanitizer for clean speech
- AI copilot powered by Claude with flight-phase-aware response styles
- Dynamic token budgeting (short/normal/briefing response lengths)
- Push-to-talk (spacebar) and barge-in interruption support
- ChromaDB RAG store for aircraft manuals with query caching
- Flight phase detection state machine (preflight through landed)
- Health monitoring for all subsystems with graceful degradation
- Connection quality indicator and auto-reconnect with exponential backoff
- Delta detection to skip duplicate telemetry messages
- 287 unit tests covering all modules

### Architecture
- SimConnect bridge (C# .NET 8) with subscription-based data delivery
- Python orchestrator with async/await throughout
- FastAPI web server bridging browser to all backend services
- Docker Compose for Whisper and ChromaDB services
- WSL2-compatible networking with configurable bridge host
