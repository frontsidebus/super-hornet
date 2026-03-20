"""Integration tests for the Whisper speech-to-text pipeline.

These tests require the Whisper Docker container to be running.
Run with: pytest tests/integration/test_whisper_pipeline.py -m docker
"""

from __future__ import annotations

import pytest

from orchestrator.whisper_client import WhisperClient, WhisperClientError

pytestmark = [pytest.mark.integration, pytest.mark.docker]


@pytest.fixture()
def whisper_client(docker_whisper: str) -> WhisperClient:
    client = WhisperClient(base_url=docker_whisper, timeout=30.0, language="en")
    yield client
    client.close()


class TestWhisperAvailability:
    """Verify the Whisper service is reachable."""

    def test_service_is_available(self, whisper_client: WhisperClient) -> None:
        assert whisper_client.is_available()


class TestWhisperTranscription:
    """End-to-end transcription tests."""

    def test_transcribe_wav_returns_text(
        self, whisper_client: WhisperClient, sample_wav_bytes: bytes
    ) -> None:
        """A valid WAV file should produce some text output (even if just noise)."""
        result = whisper_client.transcribe(sample_wav_bytes)
        # The sine wave may produce empty or short text, but should not raise
        assert isinstance(result, str)

    def test_transcribe_silent_audio(
        self, whisper_client: WhisperClient, silent_wav_bytes: bytes
    ) -> None:
        """Silent audio should return without error (may return empty or whitespace)."""
        result = whisper_client.transcribe(silent_wav_bytes)
        assert isinstance(result, str)

    def test_transcribe_longer_audio(
        self, whisper_client: WhisperClient, long_wav_bytes: bytes
    ) -> None:
        """A 5-second audio clip should transcribe successfully."""
        result = whisper_client.transcribe(long_wav_bytes)
        assert isinstance(result, str)

    def test_transcribe_with_json_output(
        self, whisper_client: WhisperClient, sample_wav_bytes: bytes
    ) -> None:
        """Request JSON output format from the ASR endpoint."""
        result = whisper_client.transcribe(sample_wav_bytes, output_format="json")
        assert isinstance(result, str)
        # JSON output should be parseable (it's returned as a string from the client)
        import json
        parsed = json.loads(result)
        assert "text" in parsed

    def test_transcribe_empty_bytes_raises(
        self, whisper_client: WhisperClient
    ) -> None:
        """Sending empty bytes should result in an error from the service."""
        with pytest.raises((WhisperClientError, Exception)):
            whisper_client.transcribe(b"")

    def test_transcribe_invalid_audio_raises(
        self, whisper_client: WhisperClient
    ) -> None:
        """Sending garbage bytes (not a valid audio format) should raise."""
        with pytest.raises((WhisperClientError, Exception)):
            whisper_client.transcribe(b"this is not audio data at all")


class TestWhisperLanguageOverride:
    """Test language parameter handling."""

    def test_explicit_english_language(
        self, whisper_client: WhisperClient, sample_wav_bytes: bytes
    ) -> None:
        result = whisper_client.transcribe(sample_wav_bytes, language="en")
        assert isinstance(result, str)

    def test_explicit_language_override(
        self, whisper_client: WhisperClient, sample_wav_bytes: bytes
    ) -> None:
        """Overriding language on a per-call basis should not crash."""
        result = whisper_client.transcribe(sample_wav_bytes, language="fr")
        assert isinstance(result, str)
