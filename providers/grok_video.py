# providers/grok_video.py
"""xAI Grok Imagine Video API — submit → poll → video URL.

인증: Bearer token (xai- prefix API key)
비동기: POST /v1/videos/generations → GET /v1/videos/{request_id} 폴링
"""
import base64
import time

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

    request_id = resp.json().get("request_id")
    if not request_id:
        raise RuntimeError("Grok submit 응답에 request_id가 없습니다.")

    # ── Poll ──
    deadline = time.time() + max_poll_sec
    while time.time() < deadline:
        time.sleep(poll_interval)

        poll_resp = requests.get(
            f"{BASE_URL}/videos/{request_id}",
            headers=headers,
            timeout=30,
        )
        if poll_resp.status_code != 200:
            continue

        poll_data = poll_resp.json()
        status = poll_data.get("status", "")

        if status == "done":
            video = poll_data.get("video", {})
            video_url = video.get("url", "")
            if video_url:
                dl = requests.get(video_url, timeout=120)
                if dl.status_code == 200:
                    ct = dl.headers.get("Content-Type", "video/mp4")
                    b64 = base64.b64encode(dl.content).decode()
                    return [f"data:{ct};base64,{b64}"]
            raise RuntimeError("Grok 완료되었으나 비디오 URL이 비어있습니다.")

        if status == "expired":
            raise RuntimeError("Grok 작업이 만료되었습니다.")

    raise RuntimeError(f"Grok 작업 시간 초과 ({max_poll_sec}초)")
