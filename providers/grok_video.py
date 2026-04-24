# providers/grok_video.py
"""xAI Grok Imagine Video API — submit → poll → video URL.

인증: Bearer token (xai- prefix API key)
비동기: POST /v1/videos/generations → GET /v1/videos/{request_id} 폴링
"""
import base64
import ipaddress
import socket
import time
from urllib.parse import urlparse

import requests

BASE_URL = "https://api.x.ai/v1"


def generate_video(
    api_key: str,
    prompt: str,
    settings: dict | None = None,
    start_image_url: str = "",
    max_poll_sec: int = 360,
    poll_interval: float = 10.0,
    model: str = "grok-imagine-video",
) -> list[str]:
    """Grok Imagine Video: submit → poll → video data URL 리스트 반환.

    Args:
        start_image_url: 스타트 프레임 이미지 공개 URL (image-to-video).
        model: 사용할 모델명 (KEY_POOL_JSON에서 설정 가능).
    """
    settings = settings or {}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # ── Submit ──
    payload: dict = {
        "model": model,
        "prompt": prompt,
    }

    duration = settings.get("duration")
    if duration:
        payload["duration"] = int(duration)

    aspect_ratio = settings.get("aspectRatio")
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio

    resolution = settings.get("resolution")
    if resolution:
        payload["resolution"] = resolution

    if start_image_url:
        payload["image"] = {"url": start_image_url}

    resp = requests.post(
        f"{BASE_URL}/videos/generations",
        headers=headers,
        json=payload,
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Grok submit 오류 ({resp.status_code}): {resp.text[:300]}"
        )

    try:
        request_id = resp.json().get("request_id")
    except (ValueError, requests.exceptions.JSONDecodeError):
        raise RuntimeError("Grok submit: 응답 JSON 파싱 실패")
    if not request_id:
        raise RuntimeError("Grok submit 응답에 request_id가 없습니다.")

    _MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200MB 제한

    # ── Poll ──
    deadline = time.time() + max_poll_sec
    consecutive_errors = 0
    while time.time() < deadline:
        time.sleep(poll_interval)

        poll_resp = requests.get(
            f"{BASE_URL}/videos/{request_id}",
            headers=headers,
            timeout=30,
        )
        if poll_resp.status_code != 200:
            consecutive_errors += 1
            if consecutive_errors >= 5 and poll_resp.status_code in (401, 403):
                raise RuntimeError(f"Grok 폴링 인증 오류 ({poll_resp.status_code})")
            if poll_resp.status_code == 429:
                time.sleep(poll_interval)
            elif poll_resp.status_code in (400, 404):
                raise RuntimeError(f"Grok 폴링 오류 ({poll_resp.status_code}): {poll_resp.text[:200]}")
            continue
        consecutive_errors = 0

        try:
            poll_data = poll_resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            continue
        status = poll_data.get("status", "")

        if status == "done":
            video = poll_data.get("video", {})
            video_url = video.get("url", "")
            if video_url:
                # SSRF 방어: 스킴·호스트 검증
                _pu = urlparse(video_url)
                if _pu.scheme not in ("http", "https"):
                    raise RuntimeError(f"Grok 비디오 URL 스킴 거부: {_pu.scheme}")
                try:
                    _ip = socket.getaddrinfo(_pu.hostname, None)[0][4][0]
                    _addr = ipaddress.ip_address(_ip)
                    if _addr.is_private or _addr.is_loopback or _addr.is_link_local:
                        raise RuntimeError(f"Grok 비디오 URL 내부 IP 거부: {_pu.hostname}")
                except (socket.gaierror, ValueError):
                    pass  # DNS 실패 시 requests가 처리
                dl = requests.get(video_url, timeout=120, stream=True)
                if dl.status_code == 200:
                    try:
                        content_len = int(dl.headers.get("Content-Length", 0))
                    except (ValueError, TypeError):
                        content_len = 0
                    if content_len > _MAX_VIDEO_BYTES:
                        dl.close()
                        raise RuntimeError(f"비디오 크기 초과: {content_len // (1024*1024)}MB")
                    chunks = []
                    downloaded = 0
                    for chunk in dl.iter_content(chunk_size=1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > _MAX_VIDEO_BYTES:
                            dl.close()
                            raise RuntimeError(f"비디오 크기 초과: {downloaded // (1024*1024)}MB")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    ct = dl.headers.get("Content-Type", "video/mp4")
                    b64 = base64.b64encode(content).decode()
                    return [f"data:{ct};base64,{b64}"]
            raise RuntimeError("Grok 완료되었으나 비디오 URL이 비어있습니다.")

        if status == "expired":
            raise RuntimeError("Grok 작업이 만료되었습니다.")

    raise RuntimeError(f"Grok 작업 시간 초과 ({max_poll_sec}초)")
