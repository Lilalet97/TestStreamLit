# providers/elevenlabs.py
"""ElevenLabs Text-to-Speech API 호출."""
import base64

import requests

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


def text_to_speech(
    api_key: str,
    voice_id: str,
    text: str,
    model_id: str = "eleven_multilingual_v2",
    stability: float = 0.5,
    similarity_boost: float = 0.75,
    style: float = 0.0,
    use_speaker_boost: bool = True,
) -> str:
    """TTS 호출 → audio data URL (base64 mp3) 반환."""
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "use_speaker_boost": use_speaker_boost,
        },
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs API {resp.status_code}: {resp.text[:300]}"
        )

    audio_b64 = base64.b64encode(resp.content).decode("ascii")
    return f"data:audio/mpeg;base64,{audio_b64}"
