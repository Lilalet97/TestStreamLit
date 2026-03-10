# ui/floating_notice.py
"""플로팅 알림/점검 배너 — 화면 최상단 고정, 실시간 카운트다운."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.maintenance import check_maintenance, get_notices_for_display

_COMPONENT_DIR = Path(__file__).resolve().parent / "components" / "floating_notice"
_notice_component_func = components.declare_component("floating_notice", path=str(_COMPONENT_DIR))


def _notice_component(notices: list, maintenance=None, key: str = "floating_notice"):
    return _notice_component_func(
        notices=notices,
        maintenance=maintenance,
        key=key,
        default=None,
    )


@st.fragment(run_every="5s")
def _notice_fragment(cfg: AppConfig, school_id: str):
    """5초마다 자동 폴링하는 알림/점검 프래그먼트."""
    maint_status = check_maintenance(cfg)

    # 점검 경고 정보 (JS 카운트다운용)
    maintenance_info = None
    if maint_status.is_warning_period or maint_status.is_maintenance_active:
        from core.db import get_upcoming_maintenance
        m = get_upcoming_maintenance(cfg)
        if m:
            maintenance_info = {
                "scheduled_at": m["scheduled_at"],
                "message": maint_status.message,
            }

    # 활성 알림 (dismissed는 JS parent DOM에서 관리)
    notices = get_notices_for_display(cfg, school_id, dismissed_ids=set())
    notice_list = [
        {"notice_id": n["notice_id"], "message": n["message"]}
        for n in notices
    ]

    _notice_component(notices=notice_list, maintenance=maintenance_info)


def render_floating_notice(cfg: AppConfig):
    """플로팅 알림 배너 렌더링. sidebar 내에서 호출 권장."""
    school_id = st.session_state.get("school_id", "default")
    _notice_fragment(cfg, school_id)
