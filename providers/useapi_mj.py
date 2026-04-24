# providers/useapi_mj.py
"""useapi.net Midjourney API v3 wrapper.

Flow:
1. POST /v3/midjourney/jobs/imagine  → jobid 반환
2. GET  /v3/midjourney/jobs/{jobid}  → 폴링 (15초 간격)
3. status == "completed" → response.attachments[].url 또는 response.imageUx[].url
"""
import logging
import time

import requests

_log = logging.getLogger(__name__)

BASE_URL = "https://api.useapi.net/v3/midjourney"


def imagine(
    api_token: str,
    prompt: str,
    *,
    channel: str = "",
    timeout: int = 300,
    poll_interval: int = 15,
) -> list[str]:
    """Midjourney /imagine 요청 후 완료까지 폴링, 이미지 URL 리스트 반환.

    Args:
        api_token: useapi.net Bearer 토큰
        prompt: Midjourney 프롬프트
        channel: Discord 채널 ID (없으면 자동 선택)
        timeout: 최대 대기 시간 (초)
        poll_interval: 폴링 간격 (초)

    Returns:
        이미지 URL 리스트
    """
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    # ── 1) Submit imagine job ──
    payload = {"prompt": prompt, "stream": False}
    if channel:
        payload["channel"] = channel

    resp = requests.post(
        f"{BASE_URL}/jobs/imagine",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code == 422:
        raise RuntimeError("Midjourney 프롬프트가 거부되었습니다 (모더레이션)")
    if resp.status_code == 429:
        raise RuntimeError("Midjourney 대기열이 가득 찼습니다. 잠시 후 다시 시도해주세요.")
    if resp.status_code not in (200, 201):
        try:
            detail = resp.json()
        except (ValueError, Exception):
            detail = resp.text[:300]
        raise RuntimeError(f"Midjourney imagine 오류 ({resp.status_code}): {detail}")

    try:
        data = resp.json()
    except (ValueError, Exception):
        raise RuntimeError("Midjourney imagine: 응답 JSON 파싱 실패")

    jobid = data.get("jobid")
    if not jobid:
        raise RuntimeError(f"Midjourney imagine: jobid가 없습니다 — {data}")

    _log.info("Midjourney job submitted: %s", jobid)

    # ── 2) Poll until completed ──
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(poll_interval)

        poll_resp = requests.get(
            f"{BASE_URL}/jobs/{jobid}",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=30,
        )

        if poll_resp.status_code != 200:
            _log.warning("Midjourney poll error %s: %s", poll_resp.status_code, poll_resp.text[:200])
            continue

        try:
            job = poll_resp.json()
        except (ValueError, Exception):
            continue

        status = job.get("status", "")
        _log.info("Midjourney job %s status: %s", jobid, status)

        if status == "completed":
            urls = []
            response = job.get("response", {})
            # 개별 이미지 (imageUx: upscaled 개별 URL)
            for img in response.get("imageUx", []):
                url = img.get("url")
                if url:
                    urls.append(url)
            # fallback: 그리드 이미지 (attachments)
            if not urls:
                for att in response.get("attachments", []):
                    url = att.get("proxy_url") or att.get("url")
                    if url:
                        urls.append(url)
            if not urls:
                raise RuntimeError("Midjourney 완료되었으나 이미지 URL이 없습니다")
            return urls

        if status in ("failed", "cancelled", "moderated"):
            err = job.get("error", "") or job.get("errorDetails", "") or status
            raise RuntimeError(f"Midjourney 작업 실패: {err}")

    raise RuntimeError(f"Midjourney 작업 시간 초과 ({timeout}초)")


def describe(
    api_token: str,
    image_url: str,
    *,
    channel: str = "",
    timeout: int = 120,
    poll_interval: int = 5,
) -> list[str]:
    """Midjourney /describe 요청 → 이미지 분석 → 프롬프트 4개 반환.

    Args:
        api_token: useapi.net Bearer 토큰
        image_url: 분석할 이미지 URL (GCS 등 공개 URL)
        channel: Discord 채널 ID
        timeout: 최대 대기 시간 (초)
        poll_interval: 폴링 간격 (초)

    Returns:
        프롬프트 문자열 리스트 (최대 4개)
    """
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    payload = {"imageUrl": image_url, "stream": False}
    if channel:
        payload["channel"] = channel

    resp = requests.post(
        f"{BASE_URL}/jobs/describe",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if resp.status_code == 422:
        raise RuntimeError("Midjourney describe: 이미지가 거부되었습니다")
    if resp.status_code == 429:
        raise RuntimeError("Midjourney 대기열이 가득 찼습니다. 잠시 후 다시 시도해주세요.")
    if resp.status_code not in (200, 201):
        try:
            detail = resp.json()
        except (ValueError, Exception):
            detail = resp.text[:300]
        raise RuntimeError(f"Midjourney describe 오류 ({resp.status_code}): {detail}")

    try:
        data = resp.json()
    except (ValueError, Exception):
        raise RuntimeError("Midjourney describe: 응답 JSON 파싱 실패")

    jobid = data.get("jobid")
    if not jobid:
        raise RuntimeError(f"Midjourney describe: jobid가 없습니다 — {data}")

    _log.info("Midjourney describe job submitted: %s", jobid)

    # Poll until completed
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(poll_interval)

        poll_resp = requests.get(
            f"{BASE_URL}/jobs/{jobid}",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=30,
        )

        if poll_resp.status_code != 200:
            continue

        try:
            job = poll_resp.json()
        except (ValueError, Exception):
            continue

        status = job.get("status", "")
        _log.info("Midjourney describe %s status: %s", jobid, status)

        if status == "completed":
            # 프롬프트 추출: response.embeds[0].description
            response = job.get("response", {})
            embeds = response.get("embeds", [])
            if embeds:
                desc = embeds[0].get("description", "")
                # 1️⃣ ... 2️⃣ ... 3️⃣ ... 4️⃣ ... 형태로 파싱
                import re
                prompts = re.split(r'[1-4]️⃣\s*', desc)
                prompts = [p.strip() for p in prompts if p.strip()]
                if prompts:
                    return prompts
            # fallback: content에서 추출 시도
            content = response.get("content", "")
            if content:
                import re
                prompts = re.split(r'[1-4]️⃣\s*', content)
                prompts = [p.strip() for p in prompts if p.strip()]
                if prompts:
                    return prompts
            raise RuntimeError("Midjourney describe: 프롬프트를 추출할 수 없습니다")

        if status in ("failed", "cancelled", "moderated"):
            err = job.get("error", "") or job.get("errorDetails", "") or status
            raise RuntimeError(f"Midjourney describe 실패: {err}")

    raise RuntimeError(f"Midjourney describe 시간 초과 ({timeout}초)")
