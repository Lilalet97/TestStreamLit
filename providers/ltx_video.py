# providers/ltx_video.py
"""LTX Video API — text-to-video, image-to-video.

API: https://api.ltx.video/v1
인증: Bearer API Key
응답: MP4 바이너리 직접 반환 (폴링 없음)
"""
import base64
import logging
import requests

_log = logging.getLogger(__name__)

BASE_URL = "https://api.ltx.video/v1"


def text_to_video(
    api_key: str,
    prompt: str,
    *,
    model: str = "ltx-2-3-fast",
    duration: int = 5,
    resolution: str = "1920x1080",
    timeout: int = 300,
) -> str:
    """텍스트 → 비디오 생성. data:video/mp4;base64,... 반환."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "model": model,
        "duration": duration,
        "resolution": resolution,
    }

    _log.info("LTX text-to-video: model=%s, duration=%s, resolution=%s", model, duration, resolution)

    resp = requests.post(
        f"{BASE_URL}/text-to-video",
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if resp.status_code == 401:
        raise RuntimeError("LTX API 인증 실패: API 키를 확인하세요.")
    if resp.status_code == 429:
        raise RuntimeError("LTX API 요청 한도 초과. 잠시 후 다시 시도해주세요.")
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:300]
        raise RuntimeError(f"LTX text-to-video 오류 ({resp.status_code}): {detail}")

    # MP4 바이너리 → data URL
    video_bytes = resp.content
    b64 = base64.b64encode(video_bytes).decode("ascii")
    return f"data:video/mp4;base64,{b64}"


def image_to_video(
    api_key: str,
    prompt: str,
    image_url: str,
    *,
    model: str = "ltx-2-3-fast",
    duration: int = 5,
    resolution: str = "1920x1080",
    timeout: int = 300,
) -> str:
    """이미지 → 비디오 생성. data:video/mp4;base64,... 반환."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "image_uri": image_url,
        "prompt": prompt,
        "model": model,
        "duration": duration,
        "resolution": resolution,
    }

    _log.info("LTX image-to-video: model=%s, duration=%s, resolution=%s", model, duration, resolution)

    resp = requests.post(
        f"{BASE_URL}/image-to-video",
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if resp.status_code == 401:
        raise RuntimeError("LTX API 인증 실패: API 키를 확인하세요.")
    if resp.status_code == 429:
        raise RuntimeError("LTX API 요청 한도 초과. 잠시 후 다시 시도해주세요.")
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:300]
        raise RuntimeError(f"LTX image-to-video 오류 ({resp.status_code}): {detail}")

    video_bytes = resp.content
    b64 = base64.b64encode(video_bytes).decode("ascii")
    return f"data:video/mp4;base64,{b64}"
