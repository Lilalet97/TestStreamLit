# providers/legnext.py
import time
import uuid
import streamlit as st

from core.http import http_post_json, http_get_json
from core.redact import json_dumps_safe

LEGNEXT_BASE = "https://api.legnext.ai/api/v1"


def submit(text: str, api_key: str, callback: str | None = None):
    url = f"{LEGNEXT_BASE}/diffusion"
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    payload = {"text": text}
    if callback:
        payload["callback"] = callback
    return http_post_json(url, headers, payload, timeout=30)


def get_job(job_id: str, api_key: str):
    url = f"{LEGNEXT_BASE}/job/{job_id}"
    headers = {"x-api-key": api_key}
    return http_get_json(url, headers, timeout=30)


def is_error_obj(j: dict | None) -> bool:
    return isinstance(j, dict) and ("code" in j and "message" in j) and ("job_id" not in j)


# ----------------------------
# MOCK
# ----------------------------
def mock_submit(full_text: str, scenario: str):
    if scenario == "FAILED_402":
        j = {"code": 402, "message": "insufficient quota", "raw_message": "", "detail": None}
        return 402, json_dumps_safe(j), j
    if scenario == "FAILED_401":
        j = {"code": 401, "message": "Failed to verify api key", "raw_message": "", "detail": None}
        return 401, json_dumps_safe(j), j
    if scenario == "FAILED_429":
        j = {"code": 429, "message": "Too Many Requests", "raw_message": "", "detail": None}
        return 429, json_dumps_safe(j), j
    if scenario == "SERVER_500":
        j = {"code": 500, "message": "Internal Server Error", "raw_message": "", "detail": None}
        return 500, json_dumps_safe(j), j

    job_id = "mock_" + str(uuid.uuid4())[:8]
    st.session_state.setdefault("_mock_legnext_jobs", {})
    st.session_state["_mock_legnext_jobs"][job_id] = {"created": time.time(), "scenario": scenario}
    j = {"job_id": job_id, "status": "pending"}
    return 200, json_dumps_safe(j), j


def mock_get_job(job_id: str):
    st.session_state.setdefault("_mock_legnext_jobs", {})
    job = st.session_state["_mock_legnext_jobs"].get(job_id)
    if not job:
        j = {"code": 404, "message": "job not found"}
        return 404, json_dumps_safe(j), j

    scenario = job["scenario"]
    elapsed = time.time() - job["created"]

    if scenario == "TIMEOUT":
        status = "processing" if elapsed > 1 else "pending"
        j = {"job_id": job_id, "status": status, "output": None, "error": None}
        return 200, json_dumps_safe(j), j

    if elapsed < 1.5:
        status = "pending"
        out = None
    elif elapsed < 3.0:
        status = "processing"
        out = None
    else:
        status = "completed"
        out = {"image_urls": [f"https://dummyimage.com/1024x1024/000/fff.png&text=LEGNEXT+{job_id}"]}

    j = {"job_id": job_id, "status": status, "output": out, "error": None}
    return 200, json_dumps_safe(j), j
