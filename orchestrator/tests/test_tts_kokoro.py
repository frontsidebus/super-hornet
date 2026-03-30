"""Tests for the Kokoro TTS backend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from orchestrator.tts.base import TTSProvider


class TestKokoroTTSConstruction:
    """Constructor and property tests."""

    def test_is_tts_provider_subclass(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro"):
            from orchestrator.tts.kokoro import KokoroTTS

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")
            assert isinstance(tts, TTSProvider)

    def test_sample_rate_returns_24000(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro"):
            from orchestrator.tts.kokoro import KokoroTTS

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")
            assert tts.sample_rate == 24000

    def test_constructor_raises_if_model_missing(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro"):
            from orchestrator.tts.kokoro import KokoroTTS

            with (
                patch("pathlib.Path.is_file", return_value=False),
                pytest.raises(FileNotFoundError, match="model file not found"),
            ):
                KokoroTTS(
                    model_path="/nonexistent/model.onnx",
                    voices_path="/fake/voices.bin",
                )

    def test_constructor_raises_if_voices_missing(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro"):
            from orchestrator.tts.kokoro import KokoroTTS

            # model exists, voices does not
            call_count = 0

            def _is_file_side_effect() -> bool:
                nonlocal call_count
                call_count += 1
                return call_count == 1  # First call (model) returns True

            with (
                patch("pathlib.Path.is_file", side_effect=_is_file_side_effect),
                pytest.raises(FileNotFoundError, match="voices file not found"),
            ):
                KokoroTTS(
                    model_path="/fake/model.onnx",
                    voices_path="/nonexistent/voices.bin",
                )

    def test_constructor_raises_if_kokoro_not_installed(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro", None):
            from orchestrator.tts.kokoro import KokoroTTS

            with (
                patch("pathlib.Path.is_file", return_value=True),
                pytest.raises(ImportError, match="kokoro-onnx"),
            ):
                KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")


class TestKokoroSynthesize:
    """synthesize() tests."""

    async def test_synthesize_returns_pcm_int16_bytes(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro") as mock_kokoro_cls:
            from orchestrator.tts.kokoro import KokoroTTS

            mock_kokoro = MagicMock()
            mock_kokoro_cls.return_value = mock_kokoro

            # create() returns (float32 samples, sample_rate)
            float_samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
            mock_kokoro.create = MagicMock(return_value=(float_samples, 24000))

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")

            result = await tts.synthesize("Hello world")

        assert isinstance(result, bytes)
        # Verify float32 -> int16 conversion
        expected = (np.clip(float_samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        assert result == expected

    async def test_synthesize_clips_values(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro") as mock_kokoro_cls:
            from orchestrator.tts.kokoro import KokoroTTS

            mock_kokoro = MagicMock()
            mock_kokoro_cls.return_value = mock_kokoro

            # Values outside [-1, 1] should be clipped
            float_samples = np.array([2.0, -2.0], dtype=np.float32)
            mock_kokoro.create = MagicMock(return_value=(float_samples, 24000))

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")

            result = await tts.synthesize("Clip test")

        result_array = np.frombuffer(result, dtype=np.int16)
        assert result_array[0] == 32767  # clipped from 2.0
        assert result_array[1] == -32767  # clipped from -2.0


class TestKokoroSynthesizeStream:
    """synthesize_stream() tests."""

    async def test_stream_yields_pcm_chunks(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro") as mock_kokoro_cls:
            from orchestrator.tts.kokoro import KokoroTTS

            mock_kokoro = MagicMock()
            mock_kokoro_cls.return_value = mock_kokoro

            # create_stream returns async generator of (samples, sr) tuples
            async def _fake_stream(*args, **kwargs):
                yield (np.zeros(1000, dtype=np.float32), 24000)
                yield (np.ones(500, dtype=np.float32), 24000)

            mock_kokoro.create_stream = _fake_stream

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")

            chunks = []
            async for chunk in tts.synthesize_stream("Hello streaming"):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert all(isinstance(c, bytes) for c in chunks)
        # First chunk: 1000 int16 samples = 2000 bytes
        assert len(chunks[0]) == 2000
        # Second chunk: 500 int16 samples = 1000 bytes
        assert len(chunks[1]) == 1000

    async def test_stream_stops_on_cancel(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro") as mock_kokoro_cls:
            from orchestrator.tts.kokoro import KokoroTTS

            mock_kokoro = MagicMock()
            mock_kokoro_cls.return_value = mock_kokoro

            async def _fake_stream(*args, **kwargs):
                yield (np.zeros(100, dtype=np.float32), 24000)
                yield (np.zeros(100, dtype=np.float32), 24000)
                yield (np.zeros(100, dtype=np.float32), 24000)

            mock_kokoro.create_stream = _fake_stream

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")

            chunks = []
            async for chunk in tts.synthesize_stream("Cancel test"):
                chunks.append(chunk)
                # Cancel after first chunk
                await tts.cancel()

        # Should get 1 chunk (the one before cancellation was checked)
        assert len(chunks) == 1


class TestKokoroCancel:
    """cancel() and aclose() tests."""

    async def test_cancel_sets_flag(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro"):
            from orchestrator.tts.kokoro import KokoroTTS

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")

            assert not tts._cancelled
            await tts.cancel()
            assert tts._cancelled

    async def test_aclose_is_noop(self) -> None:
        with patch("orchestrator.tts.kokoro.Kokoro"):
            from orchestrator.tts.kokoro import KokoroTTS

            with patch("pathlib.Path.is_file", return_value=True):
                tts = KokoroTTS(model_path="/fake/model.onnx", voices_path="/fake/voices.bin")

            # Should not raise
            await tts.aclose()
