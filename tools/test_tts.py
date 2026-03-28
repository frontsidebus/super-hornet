#!/usr/bin/env python3
"""Test ElevenLabs TTS — verifies Super Hornet can talk.

Usage:
    # List available voices:
    python tools/test_tts.py --list-voices

    # Test TTS with a specific voice:
    python tools/test_tts.py --voice-id <ID> --text "Good afternoon, Captain."

    # Test with Super Hornet's default intro:
    python tools/test_tts.py --voice-id <ID>

Requires: ELEVENLABS_API_KEY in .env or environment.
"""

import argparse
import asyncio
import os
import sys

import httpx

# Add orchestrator to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator"))


HORNET_INTRO = (
    "Good afternoon, Captain. This is Hornet, your AI wingman. "
    "Systems are nominal, scanners are clear, and I've already "
    "reviewed the departure procedures. Whenever you're ready to fly, "
    "I'm ready to keep you out of trouble. Mostly."
)


async def list_voices(api_key: str) -> None:
    """List all available ElevenLabs voices."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
        )
        resp.raise_for_status()
        voices = resp.json()["voices"]

        print(f"\n{'Name':<30} {'Voice ID':<25} {'Labels'}")
        print("-" * 85)
        for v in voices:
            labels = v.get("labels", {})
            label_str = ", ".join(f"{k}: {val}" for k, val in labels.items()) if labels else ""
            print(f"{v['name']:<30} {v['voice_id']:<25} {label_str}")
        print(f"\nTotal: {len(voices)} voices")


async def test_tts(api_key: str, voice_id: str, text: str, save_path: str | None = None) -> None:
    """Synthesize text and play it."""
    print(f"Voice ID: {voice_id}")
    print(f"Text: {text}")
    print("Synthesizing...")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        mp3_data = resp.content

    print(f"Received {len(mp3_data)} bytes of audio")

    if save_path:
        with open(save_path, "wb") as f:
            f.write(mp3_data)
        print(f"Saved to {save_path}")

    # Decode MP3 → PCM via ffmpeg and play
    print("Playing...")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", "pipe:0",
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", "24000", "-ac", "1",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=mp3_data)

    if proc.returncode != 0:
        print(f"ffmpeg error: {stderr.decode()[:300]}")
        return

    import numpy as np
    samples = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
    print(f"Audio: {len(samples)} samples, {len(samples)/24000:.1f}s duration")

    try:
        import sounddevice as sd
        sd.play(samples, samplerate=24000)
        sd.wait()
        print("Playback complete!")
    except Exception as e:
        print(f"Playback failed ({e}), but synthesis worked. Check audio device.")
        if save_path:
            print(f"You can play the file manually: ffplay {save_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test ElevenLabs TTS for Super Hornet")
    parser.add_argument("--list-voices", action="store_true", help="List available voices")
    parser.add_argument("--voice-id", type=str, help="ElevenLabs voice ID")
    parser.add_argument("--text", type=str, default=HORNET_INTRO, help="Text to speak")
    parser.add_argument("--save", type=str, default=None, help="Save MP3 to file path")
    parser.add_argument("--api-key", type=str, default=None, help="ElevenLabs API key (or set ELEVENLABS_API_KEY)")
    args = parser.parse_args()

    # Load .env if present
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    api_key = args.api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("Error: Set ELEVENLABS_API_KEY in .env or pass --api-key")
        sys.exit(1)

    if args.list_voices:
        asyncio.run(list_voices(api_key))
    elif args.voice_id:
        asyncio.run(test_tts(api_key, args.voice_id, args.text, args.save))
    else:
        print("Specify --list-voices or --voice-id <ID>")
        print("Example: python tools/test_tts.py --list-voices")
        sys.exit(1)


if __name__ == "__main__":
    main()
