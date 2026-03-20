"""Tests for orchestrator.whisper_client — Whisper HTTP client with retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from orchestrator.whisper_client import (
    WhisperClient,
    WhisperClientError,
    _DEFAULT_WHISPER_URL,
    _MAX_RETRIES,
    _RETRY_BACKOFF,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_audio() -> bytes:
    """Minimal fake WAV bytes for testing."""
    return b"RIFF" + b"\x00" * 100


@pytest.fixture
def whisper_client() -> WhisperClient:
    return WhisperClient(base_url="http://localhost:9000", timeout=5.0, language="en")


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestWhisperClientInit:
    def test_default_base_url(self) -> None:
        c = WhisperClient()
        assert c.base_url == _DEFAULT_WHISPER_URL

    def test_strips_trailing_slash(self) -> None:
        c = WhisperClient(base_url="http://localhost:9000/")
        assert c.base_url == "http://localhost:9000"

    def test_default_language(self) -> None:
        c = WhisperClient()
        assert c.language == "en"

    def test_custom_language(self) -> None:
        c = WhisperClient(language="de")
        assert c.language == "de"

    def test_no_language(self) -> None:
        c = WhisperClient(language=None)
        assert c.language is None


# ---------------------------------------------------------------------------
# Successful transcription
# ---------------------------------------------------------------------------


class TestTranscribeSuccess:
    def test_returns_stripped_text(self, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = "  Hello, Captain.  \n"
        mock_response.raise_for_status = MagicMock()

        whisper_client._client = MagicMock()
        whisper_client._client.post.return_value = mock_response

        result = whisper_client.transcribe(sample_audio)
        assert result == "Hello, Captain."

    def test_sends_correct_params(self, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = "text"
        mock_response.raise_for_status = MagicMock()

        whisper_client._client = MagicMock()
        whisper_client._client.post.return_value = mock_response

        whisper_client.transcribe(sample_audio, output_format="json", language="fr")

        call_args = whisper_client._client.post.call_args
        assert call_args[0][0] == "http://localhost:9000/asr"
        assert call_args[1]["params"]["output"] == "json"
        assert call_args[1]["params"]["language"] == "fr"

    def test_uses_default_language_when_not_overridden(self, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = "text"
        mock_response.raise_for_status = MagicMock()

        whisper_client._client = MagicMock()
        whisper_client._client.post.return_value = mock_response

        whisper_client.transcribe(sample_audio)
        call_args = whisper_client._client.post.call_args
        assert call_args[1]["params"]["language"] == "en"

    def test_no_language_param_when_none(self, sample_audio: bytes) -> None:
        client = WhisperClient(language=None)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = "text"
        mock_response.raise_for_status = MagicMock()

        client._client = MagicMock()
        client._client.post.return_value = mock_response

        client.transcribe(sample_audio)
        call_args = client._client.post.call_args
        assert "language" not in call_args[1]["params"]


# ---------------------------------------------------------------------------
# Retry logic — connection errors
# ---------------------------------------------------------------------------


class TestRetryOnConnectionError:
    @patch("orchestrator.whisper_client.time.sleep")
    def test_retries_on_connect_error(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = "recovered"
        mock_response.raise_for_status = MagicMock()

        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = [
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            mock_response,
        ]

        result = whisper_client.transcribe(sample_audio)
        assert result == "recovered"
        assert whisper_client._client.post.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("orchestrator.whisper_client.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = httpx.ConnectError("refused")

        with pytest.raises(WhisperClientError, match="failed after"):
            whisper_client.transcribe(sample_audio)

        assert whisper_client._client.post.call_count == _MAX_RETRIES

    @patch("orchestrator.whisper_client.time.sleep")
    def test_backoff_timing(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = httpx.ConnectError("refused")

        with pytest.raises(WhisperClientError):
            whisper_client.transcribe(sample_audio)

        # Verify backoff: 1.5*1, 1.5*2, 1.5*3
        sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [
            pytest.approx(_RETRY_BACKOFF * 1),
            pytest.approx(_RETRY_BACKOFF * 2),
            pytest.approx(_RETRY_BACKOFF * 3),
        ]


# ---------------------------------------------------------------------------
# No retry on 4xx errors
# ---------------------------------------------------------------------------


class TestNoRetryOn4xx:
    @patch("orchestrator.whisper_client.time.sleep")
    def test_does_not_retry_on_400(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = "Bad request"

        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=mock_response,
        )

        with pytest.raises(WhisperClientError):
            whisper_client.transcribe(sample_audio)

        # Should only try once for 4xx
        assert whisper_client._client.post.call_count == 1
        mock_sleep.assert_not_called()

    @patch("orchestrator.whisper_client.time.sleep")
    def test_does_not_retry_on_422(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 422
        mock_response.text = "Unprocessable"

        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = httpx.HTTPStatusError(
            "422", request=MagicMock(), response=mock_response,
        )

        with pytest.raises(WhisperClientError):
            whisper_client.transcribe(sample_audio)

        assert whisper_client._client.post.call_count == 1


# ---------------------------------------------------------------------------
# Retry on 5xx errors
# ---------------------------------------------------------------------------


class TestRetryOn5xx:
    @patch("orchestrator.whisper_client.time.sleep")
    def test_retries_on_500(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_err_response = MagicMock(spec=httpx.Response)
        mock_err_response.status_code = 500
        mock_err_response.text = "Internal error"

        mock_ok_response = MagicMock(spec=httpx.Response)
        mock_ok_response.text = "recovered"
        mock_ok_response.raise_for_status = MagicMock()

        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = [
            httpx.HTTPStatusError("500", request=MagicMock(), response=mock_err_response),
            mock_ok_response,
        ]

        result = whisper_client.transcribe(sample_audio)
        assert result == "recovered"
        assert whisper_client._client.post.call_count == 2


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    @patch("orchestrator.whisper_client.time.sleep")
    def test_retries_on_timeout(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = "ok"
        mock_response.raise_for_status = MagicMock()

        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = [
            httpx.ReadTimeout("timeout"),
            mock_response,
        ]

        result = whisper_client.transcribe(sample_audio)
        assert result == "ok"
        assert whisper_client._client.post.call_count == 2

    @patch("orchestrator.whisper_client.time.sleep")
    def test_raises_after_max_timeout_retries(self, mock_sleep: MagicMock, whisper_client: WhisperClient, sample_audio: bytes) -> None:
        whisper_client._client = MagicMock()
        whisper_client._client.post.side_effect = httpx.ReadTimeout("timeout")

        with pytest.raises(WhisperClientError, match="failed after"):
            whisper_client.transcribe(sample_audio)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_returns_true(self, whisper_client: WhisperClient) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        whisper_client._client = MagicMock()
        whisper_client._client.get.return_value = mock_response

        assert whisper_client.is_available() is True
        whisper_client._client.get.assert_called_once_with(
            "http://localhost:9000/docs", timeout=5.0,
        )

    def test_unavailable_returns_false(self, whisper_client: WhisperClient) -> None:
        whisper_client._client = MagicMock()
        whisper_client._client.get.side_effect = httpx.ConnectError("refused")

        assert whisper_client.is_available() is False

    def test_non_200_returns_false(self, whisper_client: WhisperClient) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 503

        whisper_client._client = MagicMock()
        whisper_client._client.get.return_value = mock_response

        assert whisper_client.is_available() is False


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_enter_returns_self(self) -> None:
        client = WhisperClient()
        assert client.__enter__() is client

    def test_exit_closes_client(self) -> None:
        client = WhisperClient()
        client._client = MagicMock()
        client.__exit__(None, None, None)
        client._client.close.assert_called_once()

    def test_with_statement(self) -> None:
        with WhisperClient() as client:
            assert isinstance(client, WhisperClient)
