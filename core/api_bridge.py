# core/api_bridge.py
"""
키 풀 lease + mock 분기를 한 곳에서 처리하는 헬퍼.

사용 패턴:
    result = call_with_lease(
        cfg, sidebar, provider="openai",
        mock_fn=lambda: _mock_response(scenario),
        real_fn=lambda kp: _call_real_api(kp["api_key"], ...),
    )
"""

import uuid
from typing import Any, Callable, Dict, Optional

import streamlit as st

from core.config import AppConfig
from core.key_pool import acquire_lease, release_lease


class NoKeyError(Exception):
    """키 풀에 사용 가능한 키가 없을 때."""
    pass


def call_with_lease(
    cfg: AppConfig,
    test_mode: bool,
    provider: str,
    mock_fn: Callable[[], Any],
    real_fn: Callable[[Dict[str, Any]], Any],
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    school_id: Optional[str] = None,
    max_wait_sec: int = 60,
    lease_ttl_sec: Optional[int] = 120,
) -> Any:
    """
    test_mode=True  → mock_fn() 호출 (키 풀 사용하지 않음)
    test_mode=False → acquire_lease → real_fn(key_payload) → release_lease

    Returns: mock_fn() 또는 real_fn()의 반환값
    Raises:
        NoKeyError: 키가 없거나 타임아웃
        기타: real_fn 내부 예외는 그대로 전파
    """
    if test_mode:
        return mock_fn()

    # session state에서 기본값 가져오기
    uid = user_id or st.session_state.get("user_id", "guest")
    sid = session_id or st.session_state.get("session_id", "")
    sch = school_id or st.session_state.get("school_id", "default")
    run_id = str(uuid.uuid4())

    try:
        lease = acquire_lease(
            cfg,
            provider=provider,
            run_id=run_id,
            user_id=uid,
            session_id=sid,
            school_id=sch,
            wait=True,
            max_wait_sec=max_wait_sec,
            lease_ttl_sec=lease_ttl_sec,
        )
    except TimeoutError as e:
        raise NoKeyError(
            f"[{provider}] 사용 가능한 API 키가 없습니다. 잠시 후 다시 시도해 주세요."
        ) from e

    try:
        result = real_fn(lease.key_payload)
        release_lease(cfg, lease.lease_id, state="released")
        return result
    except Exception:
        release_lease(cfg, lease.lease_id, state="error")
        raise
