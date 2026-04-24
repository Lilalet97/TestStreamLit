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
    model: Optional[str] = None,
    credit_cost: int = 0,
    credit_tab_id: str = "",
) -> Any:
    """
    test_mode=True  → mock_fn() 호출 (키 풀 사용하지 않음)
    test_mode=False → acquire_lease → real_fn(key_payload) → release_lease

    credit_cost > 0 이면 선차감 방식 적용:
      1) reserve_credits (잔액 선차감)
      2) API 호출
      3) 성공 → confirm_credits / 실패 → rollback_credits

    Returns: mock_fn() 또는 real_fn()의 반환값
    Raises:
        NoKeyError: 키가 없거나 타임아웃
        기타: real_fn 내부 예외는 그대로 전파
    """
    # 선차감 (test_mode에서도 크레딧 차감 적용)
    if credit_cost > 0:
        from core.credits import reserve_credits, rollback_credits, confirm_credits
        ok, err_msg = reserve_credits(cfg, credit_cost)
        if not ok:
            raise RuntimeError(err_msg)

    if test_mode:
        try:
            result = mock_fn()
        except Exception:
            if credit_cost > 0:
                rollback_credits(cfg, credit_cost)
            raise
        if credit_cost > 0:
            confirm_credits(cfg, credit_cost, tab_id=credit_tab_id)
        return result

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
            model=model,
        )
    except TimeoutError as e:
        if credit_cost > 0:
            rollback_credits(cfg, credit_cost)
        msg = str(e)
        if "RPD" in msg or "일일" in msg:
            raise NoKeyError(
                f"[{provider}] 오늘의 일일 요청 한도에 도달했습니다. 내일 다시 시도해 주세요."
            ) from e
        raise NoKeyError(
            f"[{provider}] 사용 가능한 API 키가 없습니다. 잠시 후 다시 시도해 주세요."
        ) from e

    try:
        result = real_fn(lease.key_payload)
        release_lease(cfg, lease.lease_id, state="released")
        if credit_cost > 0:
            confirm_credits(cfg, credit_cost, tab_id=credit_tab_id)
        return result
    except Exception:
        release_lease(cfg, lease.lease_id, state="error")
        if credit_cost > 0:
            rollback_credits(cfg, credit_cost)
        raise
