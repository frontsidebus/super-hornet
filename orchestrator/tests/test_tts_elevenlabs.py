"""Tests for the ElevenLabs TTS backend."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.tts.base import TTSProvider

# Fake PCM bytes for mocked ffmpeg output
FAKE_PCM = b"\x00\x01" * 100


class TestElevenLabsTTSConstruction:
    """Constructor and property tests."""

    def test_is_tts_provider_subclass(self) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="key", voice_id="vid")
        assert isinstance(tts, TTSProvider)

    def test_sample_rate_returns_24000(self) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="key", voice_id="vid")
        assert tts.sample_rate == 24000

    def test_constructor_stores_params(self) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(
            api_key="test-key", voice_id="test-voice", model_id="eleven_test_v1"
        )
        assert tts._api_key == "test-key"
        assert tts._voice_id == "test-voice"
        assert tts._model_id == "eleven_test_v1"


class TestElevenLabsSynthesize:
    """synthesize() REST API tests."""

    @pytest.fixture
    def _mock_ffmpeg(self):
        """Mock asyncio.create_subprocess_exec for ffmpeg calls."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(FAKE_PCM, b""))
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            yield mock_exec, mock_proc

    async def test_synthesize_calls_api_and_returns_pcm(self, _mock_ffmpeg: tuple) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        mock_exec, mock_proc = _mock_ffmpeg

        tts = ElevenLabsTTS(api_key="test-key", voice_id="test-voice")

        # Mock the httpx client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake-mp3-data"
        mock_response.raise_for_status = MagicMock()

        with patch.object(tts._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await tts.synthesize("Hello world")

        assert result == FAKE_PCM
        assert isinstance(result, bytes)

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "text-to-speech/test-voice" in call_args[0][0]
        assert call_args[1]["headers"]["xi-api-key"] == "test-key"
        assert call_args[1]["json"]["text"] == "Hello world"

    async def test_synthesize_returns_empty_bytes_on_error(self, _mock_ffmpeg: tuple) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key", voice_id="test-voice")

        import httpx

        with patch.object(
            tts._client,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPError("API error"),
        ):
            result = await tts.synthesize("Hello world")

        assert result == b""


class TestElevenLabsSynthesizeStream:
    """synthesize_stream() WebSocket tests."""

    async def test_synthesize_stream_yields_pcm_chunks(self) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key", voice_id="test-voice")

        # Build an async iterator for the websocket mock
        async def _ws_aiter():
            yield b"fake-mp3-chunk-1"
            yield b"fake-mp3-chunk-2"

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: _ws_aiter()
        mock_ws.send = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        mock_ffmpeg_proc = AsyncMock()
        mock_ffmpeg_proc.communicate = AsyncMock(return_value=(FAKE_PCM, b""))
        mock_ffmpeg_proc.returncode = 0

        with (
            patch("websockets.connect", return_value=mock_ws),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_ffmpeg_proc,
            ),
        ):
            chunks = []
            async for chunk in tts.synthesize_stream("Hello streaming"):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert all(isinstance(c, bytes) for c in chunks)


class TestElevenLabsCancel:
    """cancel() and aclose() tests."""

    async def test_cancel_sets_flag(self) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="key", voice_id="vid")
        assert not tts._cancelled
        await tts.cancel()
        assert tts._cancelled

    async def test_aclose_closes_client(self) -> None:
        from orchestrator.tts.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="key", voice_id="vid")
        with patch.object(tts._client, "aclose", new_callable=AsyncMock) as mock_close:
            await tts.aclose()
            mock_close.assert_called_once()
