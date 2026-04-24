# core/maintenance.py
"""서버 점검 + 알림 통합 로직.

흐름:
1. admin이 점검 시각(scheduled_at)을 설정
2. 매 페이지 로드 시 check_maintenance() 호출
3. 점검 1시간 전 ~ 점검 시각: 자동 알림 배너 표시
4. 점검 시각 도달: 모든 활성 lease 해제(error), 비admin 사용자 비활성화
5. admin 화면에 "점검 완료" 표시
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List

_KST = timezone(timedelta(hours=9))

from core.config import AppConfig
from core.db import (
    get_upcoming_maintenance,
    update_maintenance_status,
    deactivate_all_non_admin_users,
    get_active_notices_for_user,
)


@dataclass(frozen=True)
class MaintenanceStatus:
    is_maintenance_active: bool = False       # 점검 시각 도달 → 서비스 차단
    is_warning_period: bool = False           # 점검 1시간 전 → 경고 배너
    minutes_remaining: int = 0                # 점검까지 남은 분
    message: str = ""                         # 점검 메시지
    maintenance_id: Optional[int] = None


def check_maintenance(cfg: AppConfig) -> MaintenanceStatus:
    """현재 점검 상태를 확인. 매 페이지 로드마다 호출."""
    m = get_upcoming_maintenance(cfg)
    if m is None:
        return MaintenanceStatus()

    mid = m["id"]
    status = m["status"]
    message = m.get("message", "서버 점검이 예정되어 있습니다.")
    # scheduled_at은 KST 기준 naive datetime으로 저장됨
    raw = m["scheduled_at"].replace("Z", "").replace("+00:00", "")
    scheduled = datetime.fromisoformat(raw)
    now = datetime.now(_KST).replace(tzinfo=None)
    diff = scheduled - now
    minutes_remaining = max(0, int(diff.total_seconds() / 60))

    # 이미 active 상태면 점검 중
    if status == "active":
        return MaintenanceStatus(
            is_maintenance_active=True,
            message=message,
            maintenance_id=mid,
        )

    # 점검 시각 도달 → 점검 실행
    if now >= scheduled:
        _execute_maintenance(cfg, mid)
        return MaintenanceStatus(
            is_maintenance_active=True,
            message=message,
            maintenance_id=mid,
        )

    # 1시간 이내 → 경고 기간
    warning_start = scheduled - timedelta(hours=1)
    if now >= warning_start:
        return MaintenanceStatus(
            is_warning_period=True,
            minutes_remaining=minutes_remaining,
            message=message,
            maintenance_id=mid,
        )

    # 아직 1시간 이상 남음
    return MaintenanceStatus(
        minutes_remaining=minutes_remaining,
        message=message,
        maintenance_id=mid,
    )


def _execute_maintenance(cfg: AppConfig, mid: int):
    """점검 실행: lease 정리 + 사용자 비활성화."""
    # 1) 상태를 active로 (atomic: scheduled → active만 허용)
    from core.db import get_db as _get_db
    _conn = _get_db(cfg)
    try:
        _cur = _conn.execute(
            "UPDATE maintenance_schedule SET status='active' WHERE id=? AND status='scheduled'",
            (mid,),
        )
        _conn.commit()
        if _cur.rowcount == 0:
            return  # 이미 다른 프로세스가 실행함
    finally:
        _conn.close()

    # 2) 모든 활성 lease를 error로 해제
    from core.db import get_db
    conn = get_db(cfg)
    try:
        from core.db import now_iso
        conn.execute("""
            UPDATE api_key_leases
            SET state='error', released_at=?
            WHERE state='active'
        """, (now_iso(),))
        conn.commit()
    finally:
        conn.close()

    # 3) 비admin 사용자 전부 비활성화
    deactivate_all_non_admin_users(cfg)


def complete_maintenance(cfg: AppConfig, mid: int):
    """admin이 점검 완료 후 호출 → 상태 completed, 사용자 재활성화."""
    update_maintenance_status(cfg, mid, "completed")
    from core.db import reactivate_all_users
    reactivate_all_users(cfg)


def get_notices_for_display(cfg: AppConfig, school_id: str,
                            dismissed_ids: set) -> List[dict]:
    """사용자에게 표시할 알림 목록 (이미 닫은 것 제외)."""
    notices = get_active_notices_for_user(cfg, school_id)
    return [n for n in notices if n["notice_id"] not in dismissed_ids]
