# ui/stress_test_tab.py
"""부하 테스트 UI — Plan 기반 burst 모드.

- 실행 패널 (admin): provider × user_counts 플랜 설정 + 실행
- 결과 패널 (admin/viewer): 플랜별 비교 차트
"""
import json
import os
import threading
import uuid

import pandas as pd
import streamlit as st

from core.config import AppConfig
from core.stress_test import (
    StressPlanConfig,
    run_stress_plan,
    list_plan_ids,
    list_stress_test_runs,
    get_stress_test_run,
    get_stress_test_samples,
    delete_stress_plan,
)


# ── helpers ──────────────────────────────────────────────

def _available_providers() -> list[str]:
    """KEY_POOL_JSON에서 사용 가능한 provider 목록 추출."""
    raw = os.getenv("KEY_POOL_JSON") or st.secrets.get("KEY_POOL_JSON", "")
    if raw:
        try:
            kp = json.loads(raw)
            if isinstance(kp, dict):
                return sorted(kp.keys())
        except Exception:
            pass
    return ["openai"]


# ── live progress fragment ───────────────────────────────

@st.fragment(run_every="1s")
def _plan_live_progress():
    """실행 중인 플랜의 실시간 진행 상태."""
    progress = st.session_state.get("_stress_progress")
    if not progress:
        st.info("테스트가 실행 중이 아닙니다.")
        return

    status = progress.get("status", "")

    if status in ("completed", "cancelled"):
        # fragment가 아닌 전체 앱을 리렌더링 → 부모 함수가 completed 분기로 진입
        st.rerun(scope="app")

    if status != "running":
        st.info("테스트가 실행 중이 아닙니다.")
        return

    total = progress.get("total_rounds", 1)
    done = progress.get("completed_rounds", 0)
    current = progress.get("current_round", "")

    pct = done / total if total else 0
    st.progress(pct, text=f"라운드 {done}/{total}  —  현재: {current}")

    c1, c2, c3 = st.columns(3)
    c1.metric("전체 라운드", f"{done} / {total}")
    c2.metric("현재 Provider", progress.get("current_provider", "-"))
    c3.metric("현재 사용자 수", progress.get("current_users", "-"))

    # 이미 완료된 라운드 요약
    results = progress.get("round_results", [])
    if results:
        _show_round_summary_table(results)


def _show_round_summary_table(results: list[dict]):
    """완료된 라운드들의 요약 테이블."""
    rows = []
    for r in results:
        rows.append({
            "라운드": r.get("round_label", ""),
            "Provider": r.get("provider", ""),
            "사용자수": r.get("num_users", 0),
            "총요청": r.get("total_requests", 0),
            "성공률(%)": r.get("success_rate", 0),
            "평균지연(ms)": r.get("avg_latency_ms", 0),
            "P95(ms)": r.get("p95_ms", 0),
            "P99(ms)": r.get("p99_ms", 0),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# ── execution UI (admin only) ────────────────────────────

def render_stress_test_execution(cfg: AppConfig):
    """플랜 기반 부하 테스트 설정 및 실행 패널."""
    providers = _available_providers()

    progress = st.session_state.get("_stress_progress", {})
    status = progress.get("status", "")

    # 완료/취소된 플랜 → 결과만 보여주고 상태 초기화
    if status in ("completed", "cancelled"):
        st.success(f"플랜 {status}")
        results = progress.get("round_results", [])
        if results:
            _show_round_summary_table(results)
        if st.button("새 테스트 준비", use_container_width=True):
            st.session_state["_stress_progress"] = {}
            st.rerun()
        st.divider()

    # 실행 중 → 중지 버튼만 표시
    elif status == "running":
        st.warning("플랜이 실행 중입니다. 완료 또는 중지 후 새 테스트를 시작할 수 있습니다.")
        _plan_live_progress()

        if st.button("플랜 중지", type="primary", use_container_width=True):
            stop_ev = st.session_state.get("_stress_stop_event")
            if stop_ev:
                stop_ev.set()
            st.rerun()
        return

    st.subheader("플랜 설정")

    with st.form("stress_plan_form"):
        # Provider 선택 (다중)
        selected_providers = st.multiselect(
            "Provider (실행 순서대로)",
            providers,
            default=providers[:1],
            help="선택한 순서대로 테스트가 진행됩니다.",
        )

        # 동시 사용자 수 단계
        user_counts_str = st.text_input(
            "동시 사용자 수 (쉼표 구분)",
            value="5, 10, 15",
            help="각 provider에 대해 이 순서대로 burst 테스트가 진행됩니다.",
        )

        col1, col2 = st.columns(2)
        with col1:
            mock_mode = st.toggle("Mock 모드 (API 미호출)", value=True)
            lease_wait = st.slider("Lease 대기 시간 (초)", 5, 120, 30)
        with col2:
            lease_ttl = st.slider("Lease TTL (초)", 10, 120, 60)
            if mock_mode:
                mock_min = st.slider("Mock 최소 지연 (ms)", 50, 2000, 100)
                mock_max = st.slider("Mock 최대 지연 (ms)", 50, 5000, 500)
            else:
                mock_min, mock_max = 100, 500

        if not mock_mode:
            st.warning("Real 모드: 실제 API 키를 소비합니다. 동시 요청 제한이 일시적으로 해제됩니다.")

        # 총 라운드 미리보기
        try:
            counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            counts = [5]
        total_rounds = len(selected_providers) * len(counts)
        st.caption(f"총 {total_rounds}개 라운드 예정 ({len(selected_providers)} providers × {len(counts)} steps)")

        submitted = st.form_submit_button(
            "플랜 실행",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not selected_providers:
            st.error("최소 1개 provider를 선택해주세요.")
            return

        try:
            user_counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            st.error("사용자 수를 올바르게 입력해주세요. (예: 5, 10, 15)")
            return

        if not user_counts:
            st.error("최소 1개 사용자 수를 입력해주세요.")
            return

        plan_id = str(uuid.uuid4())
        plan_config = StressPlanConfig(
            providers=selected_providers,
            user_counts=user_counts,
            mock_mode=mock_mode,
            mock_latency_min_ms=mock_min,
            mock_latency_max_ms=mock_max,
            lease_wait_sec=lease_wait,
            lease_ttl_sec=lease_ttl,
        )

        progress: dict = {}
        stop_event = threading.Event()

        st.session_state["_stress_progress"] = progress
        st.session_state["_stress_stop_event"] = stop_event
        st.session_state["_stress_current_plan_id"] = plan_id

        admin_user_id = st.session_state.get("user_id", "admin")

        t = threading.Thread(
            target=run_stress_plan,
            args=(cfg, plan_id, plan_config, admin_user_id, progress, stop_event),
            daemon=True,
        )
        t.start()
        st.rerun()


# ── results UI (admin + viewer) ──────────────────────────

def render_stress_test_results(cfg: AppConfig):
    """플랜 기반 부하 테스트 결과 조회 + 비교 차트."""
    # 실행 중이면 라이브 프로그레스 표시
    if st.session_state.get("_stress_progress", {}).get("status") == "running":
        _plan_live_progress()
        st.divider()

    plans = list_plan_ids(cfg, limit=20)
    if not plans:
        st.info("부하 테스트 결과가 없습니다.")
        return

    st.subheader("플랜 목록")

    plan_options = {}
    for p in plans:
        label = (
            f"{p['started_at'][:19]}  |  "
            f"{p['round_count']}라운드  |  "
            f"{p.get('rounds', '')}"
        )
        plan_options[p["plan_id"]] = label

    selected_plan_id = st.selectbox(
        "플랜 선택",
        options=list(plan_options.keys()),
        format_func=lambda pid: plan_options[pid],
        key="stress_plan_select",
    )

    if not selected_plan_id:
        return

    # 해당 plan의 모든 라운드 로드
    rounds = list_stress_test_runs(cfg, limit=100, plan_id=selected_plan_id)
    if not rounds:
        st.warning("라운드 데이터를 찾을 수 없습니다.")
        return

    # 라운드별 summary 수집
    round_data = []
    for r in rounds:
        summary = {}
        try:
            summary = json.loads(r.get("summary_json", "{}") or "{}")
        except Exception:
            pass
        config = {}
        try:
            config = json.loads(r.get("config_json", "{}") or "{}")
        except Exception:
            pass

        round_data.append({
            "test_id": r["test_id"],
            "round_label": r.get("round_label", ""),
            "provider": config.get("provider", r.get("round_label", "").rsplit("_", 1)[0]),
            "num_users": config.get("num_users", 0),
            "status": r["status"],
            "mock_mode": config.get("mock_mode", True),
            **summary,
        })

    df = pd.DataFrame(round_data)

    # ── 플랜 요약 테이블 ──
    st.subheader("라운드별 요약")
    summary_cols = [
        "round_label", "provider", "num_users", "status",
        "total_requests", "success_rate", "avg_latency_ms",
        "p95_ms", "p99_ms", "successes", "timeouts", "errors",
    ]
    existing = [c for c in summary_cols if c in df.columns]
    st.dataframe(df[existing], hide_index=True, width="stretch")

    # ── 비교 차트: Provider별 평균 지연시간 vs 사용자 수 ──
    if "num_users" in df.columns and "avg_latency_ms" in df.columns:
        st.subheader("평균 지연시간 비교 (사용자 수별)")
        try:
            pivot_latency = df.pivot_table(
                index="num_users",
                columns="provider",
                values="avg_latency_ms",
                aggfunc="first",
            )
            st.line_chart(pivot_latency)
        except Exception:
            st.caption("지연시간 비교 차트를 생성할 수 없습니다.")

    # ── 비교 차트: Provider별 성공률 vs 사용자 수 ──
    if "num_users" in df.columns and "success_rate" in df.columns:
        st.subheader("성공률 비교 (사용자 수별)")
        try:
            pivot_success = df.pivot_table(
                index="num_users",
                columns="provider",
                values="success_rate",
                aggfunc="first",
            )
            st.line_chart(pivot_success)
        except Exception:
            st.caption("성공률 비교 차트를 생성할 수 없습니다.")

    # ── 비교 차트: Provider별 P95 vs 사용자 수 ──
    if "num_users" in df.columns and "p95_ms" in df.columns:
        st.subheader("P95 지연시간 비교 (사용자 수별)")
        try:
            pivot_p95 = df.pivot_table(
                index="num_users",
                columns="provider",
                values="p95_ms",
                aggfunc="first",
            )
            st.line_chart(pivot_p95)
        except Exception:
            st.caption("P95 비교 차트를 생성할 수 없습니다.")

    # ── 개별 라운드 상세 ──
    st.divider()
    st.subheader("개별 라운드 상세")

    round_labels = {r["test_id"]: r["round_label"] for r in round_data}
    selected_round = st.selectbox(
        "라운드 선택",
        options=list(round_labels.keys()),
        format_func=lambda tid: round_labels[tid],
        key="stress_round_select",
    )

    if selected_round:
        _render_round_detail(cfg, selected_round, round_data)

    # 삭제 버튼
    st.divider()
    if st.button("이 플랜 결과 전체 삭제", key=f"del_plan_{selected_plan_id}"):
        delete_stress_plan(cfg, selected_plan_id)
        st.rerun()


def _render_round_detail(cfg: AppConfig, test_id: str, round_data: list[dict]):
    """개별 라운드의 상세 정보 + 워커별 결과."""
    rd = next((r for r in round_data if r["test_id"] == test_id), None)
    if not rd:
        return

    st.markdown(
        f"**{rd.get('round_label', '')}**  |  "
        f"**상태:** `{rd.get('status', '')}`  |  "
        f"**Mock:** {'ON' if rd.get('mock_mode', True) else 'OFF'}"
    )

    if rd.get("total_requests"):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("총 요청", rd.get("total_requests", 0))
        c2.metric("성공률", f"{rd.get('success_rate', 0)}%")
        c3.metric("평균 지연", f"{rd.get('avg_latency_ms', 0)}ms")
        c4.metric("P95", f"{rd.get('p95_ms', 0)}ms")
        c5.metric("P99", f"{rd.get('p99_ms', 0)}ms")

    samples = get_stress_test_samples(cfg, test_id)
    if not samples:
        st.info("샘플 데이터가 없습니다.")
        return

    sdf = pd.DataFrame(samples)

    # 워커별 지연시간
    if "worker_id" in sdf.columns and "duration_ms" in sdf.columns:
        st.markdown("**워커별 지연시간 (ms)**")
        worker_chart = sdf[["worker_id", "duration_ms"]].copy()
        worker_chart["worker_id"] = worker_chart["worker_id"].astype(str)
        st.bar_chart(worker_chart, x="worker_id", y="duration_ms")

    # 상태 분포
    if "status" in sdf.columns:
        st.markdown("**요청 상태 분포**")
        st.bar_chart(sdf["status"].value_counts())

    # 키 분배
    key_dist = rd.get("key_distribution")
    if key_dist:
        st.markdown("**API 키별 요청 분배**")
        key_df = pd.DataFrame(
            list(key_dist.items()),
            columns=["key_name", "count"],
        )
        st.bar_chart(key_df, x="key_name", y="count")

    # 전체 샘플
    with st.expander("전체 샘플 데이터"):
        display_cols = [
            "worker_id", "request_seq", "duration_ms", "status",
            "error_text", "key_name", "started_at",
        ]
        existing = [c for c in display_cols if c in sdf.columns]
        st.dataframe(sdf[existing], hide_index=True, width="stretch")
