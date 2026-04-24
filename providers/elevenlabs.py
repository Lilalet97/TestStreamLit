# providers/elevenlabs.py
"""ElevenLabs API 호출 — TTS, Voice-to-Voice, Sound Effects, Voice Clone."""
from __future__ import annotations

import base64
import json
import re

import requests

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

_MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50MB


def _validate_voice_id(voice_id: str | None) -> None:
    """voice_id가 안전한 URL 경로 세그먼트인지 검증."""
    if not re.match(r'^[a-zA-Z0-9_-]+$', voice_id or ''):
        raise ValueError(f"Invalid voice_id: {voice_id!r}")


def _stream_audio(resp: requests.Response, label: str = "ElevenLabs") -> bytes:
    """스트리밍으로 오디오를 읽되 크기 제한을 적용."""
    resp.raise_for_status()
    chunks = []
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        downloaded += len(chunk)
        if downloaded > _MAX_AUDIO_BYTES:
            resp.close()
            raise RuntimeError(f"{label}: 응답이 50MB를 초과합니다.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise RuntimeError(f"{label} API: 빈 응답")
    return content


def _audio_data_url(content: bytes, mime: str = "audio/mpeg") -> str:
    """바이너리 오디오 → data URL."""
    b64 = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{b64}"


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
    _validate_voice_id(voice_id)
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

    resp = requests.post(url, headers=headers, json=payload, timeout=60, stream=True)
    content = _stream_audio(resp, "ElevenLabs TTS")
    return _audio_data_url(content)


def voice_to_voice(
    api_key: str,
    voice_id: str,
    audio_bytes: bytes,
    model_id: str = "eleven_english_sts_v2",
    stability: float = 0.5,
    similarity_boost: float = 0.75,
    style: float = 0.0,
    use_speaker_boost: bool = True,
) -> str:
    """Speech-to-Speech 호출 → audio data URL 반환."""
    _validate_voice_id(voice_id)
    url = f"{ELEVENLABS_BASE}/speech-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
    }
    files = {
        "audio": ("input.mp3", audio_bytes, "audio/mpeg"),
    }
    voice_settings = json.dumps({
        "stability": stability,
        "similarity_boost": similarity_boost,
        "style": style,
        "use_speaker_boost": use_speaker_boost,
    })
    data = {
        "model_id": model_id,
        "voice_settings": voice_settings,
    }

    resp = requests.post(url, headers=headers, files=files, data=data, timeout=120, stream=True)
    content = _stream_audio(resp, "ElevenLabs VTV")
    return _audio_data_url(content)


def sound_generation(
    api_key: str,
    text: str,
    duration_seconds: float | None = None,
    prompt_influence: float = 0.3,
) -> str:
    """Sound Effects 생성 → audio data URL 반환."""
    url = f"{ELEVENLABS_BASE}/sound-generation"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload: dict = {
        "text": text,
        "prompt_influence": prompt_influence,
    }
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds

    resp = requests.post(url, headers=headers, json=payload, timeout=120, stream=True)
    content = _stream_audio(resp, "ElevenLabs SFX")
    return _audio_data_url(content)


def voice_clone(
    api_key: str,
    name: str,
    audio_bytes: bytes,
    description: str = "",
) -> dict:
    """Instant Voice Clone → {"voice_id": ..., "name": ...} 반환."""
    url = f"{ELEVENLABS_BASE}/voices/add"
    headers = {
        "xi-api-key": api_key,
    }
    files = {
        "files": ("sample.mp3", audio_bytes, "audio/mpeg"),
    }
    data = {
        "name": name,
        "description": description,
    }

    resp = requests.post(url, headers=headers, files=files, data=data, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs Clone API {resp.status_code}: {resp.text[:300]}"
        )
    try:
        body = resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        raise RuntimeError("ElevenLabs Clone API: 응답 JSON 파싱 실패")
    return {"voice_id": body.get("voice_id", ""), "name": name}


def list_voices(api_key: str) -> list[dict]:
    """사용자 보이스 목록 → [{"voice_id", "name", "category"}, ...]."""
    url = f"{ELEVENLABS_BASE}/voices"
    headers = {"xi-api-key": api_key}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs Voices API {resp.status_code}: {resp.text[:300]}"
        )
    try:
        voices = resp.json().get("voices", [])
    except (ValueError, requests.exceptions.JSONDecodeError):
        raise RuntimeError("ElevenLabs Voices API: 응답 JSON 파싱 실패")
    return [
        {
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "category": v.get("category", ""),
        }
        for v in voices
    ]


def delete_voice(api_key: str, voice_id: str) -> bool:
    """보이스 삭제."""
    _validate_voice_id(voice_id)
    url = f"{ELEVENLABS_BASE}/voices/{voice_id}"
    headers = {"xi-api-key": api_key}
    resp = requests.delete(url, headers=headers, timeout=30)
    return resp.status_code == 200
