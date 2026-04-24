# providers/google_veo.py
"""Google Veo 비디오 생성 API 호출 — AI Studio 인증.

Google AI Studio API 키 방식 사용.
- 생성: Veo 3.1 (predictLongRunning)
- 폴링: GET {operation_name} (AI Studio 방식)
인증: API 키 (URL 쿼리 파라미터)
비동기: submit → poll → 비디오 base64 → data URL

# [VERTEX AI 복원 시]
# from providers.vertex_auth import get_auth_headers, get_vertex_url
# sa_json, project_id, location 파라미터 복원
# Submit: url = get_vertex_url(project_id, location, model, "predictLongRunning")
# Poll: POST get_vertex_url(model, "fetchPredictOperation") + {"operationName": name}
# headers = get_auth_headers(sa_json)
"""
import re
import time

import requests

from providers.vertex_auth import get_aistudio_url, get_aistudio_headers

DEFAULT_MODEL = "veo-3.1-generate-preview"


def generate_video(
    api_key: str,
    prompt: str,
    settings: dict | None = None,
    model: str = DEFAULT_MODEL,
    max_poll_sec: int = 360,
    poll_interval: float = 10.0,
    start_image_data_url: str = "",
    end_image_data_url: str = "",
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list[str]:
    """Veo API (AI Studio): submit → poll → video data URL 리스트 반환.

    Args:
        start_image_data_url: 스타트 프레임 이미지 (data:image/...;base64,...).
            제공 시 image-to-video 모드로 동작.
        end_image_data_url: 엔드 프레임 이미지 (data:image/...;base64,...).
            start + end 동시 제공 시 interpolation 모드 (8초 고정).
    """
    settings = settings or {}
    headers = get_aistudio_headers()
    # [VERTEX AI] headers = get_auth_headers(sa_json)

    # ── Submit ──
    url = get_aistudio_url(model, "predictLongRunning", api_key)
    # [VERTEX AI] url = get_vertex_url(project_id, location, model, "predictLongRunning")

    params = {
        "aspectRatio": settings.get("aspectRatio", "16:9"),
        # [VERTEX AI] "personGeneration": "allow_all",
    }

    resolution = settings.get("resolution")
    if resolution:
        params["resolution"] = resolution

    duration = settings.get("durationSeconds")
    if duration:
        params["durationSeconds"] = int(duration)

    instance: dict = {"prompt": prompt}

    # 스타트 이미지 → image-to-video
    if start_image_data_url:
        m = re.match(r"data:([^;]+);base64,(.+)", start_image_data_url, re.DOTALL)
        if m:
            instance["image"] = {
                "bytesBase64Encoded": m.group(2),
                "mimeType": m.group(1),
            }

    # 엔드 이미지 → interpolation (start + end)
    if end_image_data_url:
        m = re.match(r"data:([^;]+);base64,(.+)", end_image_data_url, re.DOTALL)
        if m:
            instance["lastFrame"] = {
                "bytesBase64Encoded": m.group(2),
                "mimeType": m.group(1),
            }

    # interpolation 모드: duration 8초 고정 (API 제약)
    if "image" in instance and "lastFrame" in instance:
        params["durationSeconds"] = 8

    payload = {
        "instances": [instance],
        "parameters": params,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        _safe = resp.text[:300].replace(api_key, "***")
        raise RuntimeError(
            f"Veo submit 오류 ({resp.status_code}): {_safe}"
        )

    try:
        data = resp.json()
    except (ValueError, Exception):
        raise RuntimeError(f"Veo submit: 응답 JSON 파싱 실패 (status={resp.status_code})")
    operation_name = data.get("name")
    if not operation_name:
        raise RuntimeError("Veo submit 응답에 operation name이 없습니다.")

    # ── Poll (AI Studio: GET {operation_name}) ──
    # [VERTEX AI] poll_url = get_vertex_url(project_id, location, model, "fetchPredictOperation")
    # [VERTEX AI] poll은 POST + {"operationName": operation_name} 방식
    poll_base = "https://generativelanguage.googleapis.com/v1beta"
    poll_url = f"{poll_base}/{operation_name}"

    _MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200MB 제한

    deadline = time.time() + max_poll_sec
    consecutive_errors = 0
    while time.time() < deadline:
        time.sleep(poll_interval)

        poll_resp = requests.get(poll_url, params={"key": api_key}, timeout=30)
        # [VERTEX AI] poll_headers = get_auth_headers(sa_json)
        # [VERTEX AI] poll_resp = requests.post(poll_url, headers=poll_headers,
        # [VERTEX AI]     json={"operationName": operation_name}, timeout=30)
        if poll_resp.status_code != 200:
            consecutive_errors += 1
            if consecutive_errors >= 5 and poll_resp.status_code in (401, 403):
                raise RuntimeError(f"Veo 폴링 인증 오류 ({poll_resp.status_code})")
            if poll_resp.status_code == 429:
                time.sleep(poll_interval)  # 레이트리밋 시 추가 대기
            elif poll_resp.status_code >= 500:
                pass  # 서버 에러 → 재시도
            elif poll_resp.status_code in (400, 404):
                raise RuntimeError(f"Veo 폴링 오류 ({poll_resp.status_code}): {poll_resp.text[:200]}")
            continue
        consecutive_errors = 0

        try:
            poll_data = poll_resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            continue

        # 에러 체크
        error = poll_data.get("error")
        if error:
            msg = error.get("message", "알 수 없는 오류")
            raise RuntimeError(f"Veo 작업 실패: {msg}")

        if not poll_data.get("done"):
            continue

        # 완료 → 비디오 추출
        response = poll_data.get("response", {})
        urls = []

        # 시도 1: AI Studio generateVideoResponse 형식
        gen_resp = response.get("generateVideoResponse", {})
        samples = gen_resp.get("generatedSamples", [])
        for sample in samples:
            uri = sample.get("video", {}).get("uri", "")
            if uri:
                # AI Studio URI는 API 키 인증 필요
                dl = requests.get(
                    uri, params={"key": api_key}, timeout=120, stream=True,
                )
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
                    import base64
                    ct = dl.headers.get("Content-Type", "video/mp4")
                    b64 = base64.b64encode(content).decode()
                    urls.append(f"data:{ct};base64,{b64}")

        # 시도 2: Vertex AI videos 형식 (fallback)
        if not urls:
            videos = response.get("videos", [])
            for video in videos:
                b64 = video.get("bytesBase64Encoded", "")
                mime = video.get("mimeType", "video/mp4")
                if b64:
                    urls.append(f"data:{mime};base64,{b64}")

        # 시도 3: predictions 형식 (fallback)
        if not urls:
            predictions = response.get("predictions", [])
            for pred in predictions:
                b64 = pred.get("bytesBase64Encoded", "")
                mime = pred.get("mimeType", "video/mp4")
                if b64:
                    urls.append(f"data:{mime};base64,{b64}")

        if urls:
            return urls
        raise RuntimeError(
            f"Veo 완료되었으나 비디오 데이터가 비어있습니다. "
            f"response keys: {list(response.keys())}"
        )

    raise RuntimeError(f"Veo 작업 시간 초과 ({max_poll_sec}초)")
