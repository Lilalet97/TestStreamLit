# providers/kling.py
import time
import uuid
import jwt

from core.http import http_post_json, http_get_json
from core.redact import json_dumps_safe

KLING_BASE = "https://api.klingai.com/v1"


def get_kling_token(access_key: str, secret_key: str) -> str:
    headers = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"iss": access_key, "exp": now + 1800, "nbf": now - 5}
    token = jwt.encode(payload, secret_key, headers=headers)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def submit_image(access_key: str, secret_key: str, endpoint: str, payload: dict):
    token = get_kling_token(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return http_post_json(endpoint, headers, payload, timeout=60)


def submit_video(access_key: str, secret_key: str, endpoint: str, payload: dict):
    token = get_kling_token(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return http_post_json(endpoint, headers, payload, timeout=60)


def get_task(access_key: str, secret_key: str, task_id: str, task_type: str = "video"):
    """Kling 작업 상태 조회. task_type: 'video' 또는 'image'."""
    token = get_kling_token(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}"}
    if task_type == "image":
        url = f"{KLING_BASE}/images/generations/{task_id}"
    else:
        url = f"{KLING_BASE}/videos/text2video/{task_id}"
    return http_get_json(url, headers, timeout=30)


# ----------------------------
# MOCK (Kling)
# ----------------------------
def mock_submit(is_video: bool, scenario: str):
    if scenario == "FAILED_402":
        j = {"code": 402, "message": "insufficient quota"}
        return 402, json_dumps_safe(j), j
    if scenario == "FAILED_401":
        j = {"code": 401, "message": "invalid token"}
        return 401, json_dumps_safe(j), j
    if scenario == "FAILED_429":
        j = {"code": 429, "message": "rate limited"}
        return 429, json_dumps_safe(j), j
    if scenario == "SERVER_500":
        j = {"code": 500, "message": "internal error"}
        return 500, json_dumps_safe(j), j
    if scenario == "TIMEOUT":
        # Kling은 원래 task_id 받고 폴링하는 구조가 많지만,
        # 현재 UI는 "제출 응답만" 처리하므로 TIMEOUT은 http timeout처럼 시뮬레이션
        return -1, "timeout simulated", None

    task_id = "mock_task_" + str(uuid.uuid4())[:8]
    if is_video:
        data = {"task_id": task_id, "video_url": f"https://dummyimage.com/1280x720/111/fff.png&text=KLING+VIDEO+{task_id}"}
    else:
        data = {"task_id": task_id, "image_url": f"https://dummyimage.com/1024x1024/111/fff.png&text=KLING+IMG+{task_id}"}

    j = {"code": 200, "data": data}
    return 200, json_dumps_safe(j), j
