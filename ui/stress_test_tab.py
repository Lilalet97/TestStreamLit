# ui/stress_test_tab.py
"""부하 테스트 UI — 알고리즘 검증 (Mock) + 실제 부하테스트 (Real).

- 알고리즘 검증: Mock 모드로 키 배치 알고리즘 정상 작동 확인
- 실제 부하테스트: Real API 호출, 라운드 간 60초 대기
- 결과 패널: 플랜별 비교 차트
"""
import json
import os
import threading
import uuid

import pandas as pd
import streamlit as st

from core.config import AppConfig
from core.stress_test import (
    PROVIDER_ORDER,
    StressPlanConfig,
    run_stress_plan,
    list_plan_ids,
    list_stress_test_runs,
    get_stress_test_run,
    get_stress_test_samples,
    delete_stress_plan,
)


# ── helpers ──────────────────────────────────────────────

_PROVIDER_ORDER = PROVIDER_ORDER

def _available_providers() -> list[str]:
    """KEY_POOL_JSON에서 사용 가능한 provider 목록 추출 (탭 순서 기준)."""
    try:
        raw = str(st.secrets.get("KEY_POOL_JSON", "") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        raw = os.getenv("KEY_POOL_JSON", "")
    if raw:
        try:
            kp = json.loads(raw)
            if isinstance(kp, dict):
                keys = set(kp.keys())
                ordered = [p for p in _PROVIDER_ORDER if p in keys]
                ordered += sorted(keys - set(ordered))
                return ordered
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


# ── 공통 실행 로직 ───────────────────────────────────────

def _is_running(mock_mode: bool | None = None, test_mode: str | None = None) -> bool:
    """테스트 실행 중 여부. test_mode 또는 mock_mode 지정 시 해당 모드만 확인."""
    progress = st.session_state.get("_stress_progress", {})
    if progress.get("status") != "running":
        return False
    if test_mode is not None:
        return st.session_state.get("_stress_test_mode") == test_mode
    if mock_mode is not None:
        return st.session_state.get("_stress_mock_mode") == mock_mode
    return True


def _show_running_state(key_prefix: str = ""):
    """실행 중 상태 표시 + 중지 버튼."""
    st.warning("테스트가 실행 중입니다. 완료 또는 중지 후 새 테스트를 시작할 수 있습니다.")
    _plan_live_progress()

    if st.button("테스트 중지", type="primary", width="stretch", key=f"{key_prefix}_stop_btn"):
        stop_ev = st.session_state.get("_stress_stop_event")
        if stop_ev:
            stop_ev.set()
        st.rerun()


def _show_completed_state(key_prefix: str = "", mock_mode: bool | None = None,
                          test_mode: str | None = None):
    """완료/취소 상태 표시 + 초기화."""
    progress = st.session_state.get("_stress_progress", {})
    status = progress.get("status", "")
    if status not in ("completed", "cancelled"):
        return False
    if test_mode is not None and st.session_state.get("_stress_test_mode") != test_mode:
        return False
    if mock_mode is not None and st.session_state.get("_stress_mock_mode") != mock_mode:
        return False

    st.success(f"테스트 {status}")
    results = progress.get("round_results", [])
    if results:
        _show_round_summary_table(results)
    st.session_state["_stress_progress"] = {}
    st.divider()
    return True


def _launch_plan(cfg: AppConfig, providers: list[str], user_counts: list[int],
                 mock_mode: bool, lease_wait: int, lease_ttl: int,
                 mock_min: int = 100, mock_max: int = 500,
                 test_mode: str = "mock", burst_window_sec: int = 60):
    """플랜 생성 및 백그라운드 실행."""
    plan_id = str(uuid.uuid4())
    plan_config = StressPlanConfig(
        providers=providers,
        user_counts=user_counts,
        test_mode=test_mode,
        mock_mode=mock_mode,
        mock_latency_min_ms=mock_min,
        mock_latency_max_ms=mock_max,
        lease_wait_sec=lease_wait,
        lease_ttl_sec=lease_ttl,
        burst_window_sec=burst_window_sec,
    )

    progress: dict = {}
    stop_event = threading.Event()

    st.session_state["_stress_progress"] = progress
    st.session_state["_stress_stop_event"] = stop_event
    st.session_state["_stress_current_plan_id"] = plan_id
    st.session_state["_stress_mock_mode"] = mock_mode
    st.session_state["_stress_test_mode"] = test_mode

    admin_user_id = st.session_state.get("user_id", "admin")

    t = threading.Thread(
        target=run_stress_plan,
        args=(cfg, plan_id, plan_config, admin_user_id, progress, stop_event),
        daemon=True,
    )
    t.start()
    st.rerun()


# ── 알고리즘 검증 (Mock) ────────────────────────────────

def render_algorithm_test(cfg: AppConfig):
    """Mock 모드: 키 배치 알고리즘 정상 작동 확인."""
    st.subheader("알고리즘 검증")
    st.caption("실제 FIFO 대기열과 키 배정 로직을 검증합니다. API 호출 없이 mock sleep으로 대체하여 대기·분배·타임아웃 동작을 확인합니다.")

    if _is_running(test_mode="mock"):
        _show_running_state(key_prefix="algo")
        return
    _show_completed_state(key_prefix="algo", test_mode="mock")

    providers = _available_providers()
    fixed_provider = providers[0]

    with st.form("algo_test_form"):
        st.text(f"테스트 Provider: {fixed_provider}")

        user_counts_str = st.text_input(
            "사용자 수 (쉼표 구분)",
            value="5, 10, 15, 20",
            help="이 수만큼 60초 내 랜덤 시점에 FIFO 요청합니다.",
        )

        try:
            counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            counts = [5]
        total_rounds = len(counts)
        wait_min = (total_rounds - 1) * 2 if total_rounds > 1 else 1
        st.caption(
            f"총 {total_rounds}개 라운드  |  "
            f"라운드별 60초 분산  |  "
            f"예상 소요: 약 {wait_min}분+"
        )

        st.info("API를 호출하지 않습니다. FIFO 대기열 → 키 배정 → mock sleep → release 순서로 동작합니다.")

        submitted = st.form_submit_button("알고리즘 검증 실행", type="primary", width="stretch")

    if submitted:
        try:
            user_counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            st.error("사용자 수를 올바르게 입력해주세요.")
            return
        if not user_counts:
            st.error("최소 1개 사용자 수를 입력해주세요.")
            return

        _launch_plan(
            cfg, [fixed_provider], user_counts,
            mock_mode=True, lease_wait=30, lease_ttl=60,
            mock_min=100, mock_max=500, test_mode="mock",
        )


# ── 키 부하 테스트 (Burst) ─────────────────────────────────

def render_burst_test(cfg: AppConfig):
    """Burst 모드: FIFO 우회, 동시 burst API 호출로 키 한계 측정."""
    st.subheader("키 부하 테스트")
    st.caption("FIFO를 우회하고 설정한 사용자 수만큼 동시에 API를 호출하여 키의 한계를 측정합니다.")

    if _is_running(test_mode="burst"):
        _show_running_state(key_prefix="burst")
        return
    _show_completed_state(key_prefix="burst", test_mode="burst")

    providers = _available_providers()

    with st.form("stress_burst_form"):
        selected_providers = st.multiselect(
            "Provider (실행 순서대로)",
            providers,
            default=providers[:1],
        )

        user_counts_str = st.text_input(
            "동시 사용자 수 (쉼표 구분)",
            value="5, 10, 15, 20",
            help="각 provider에 대해 이 순서대로 동시 burst 요청을 보냅니다.",
        )

        try:
            counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            counts = [5]
        total_rounds = len(selected_providers) * len(counts)
        wait_min = (total_rounds - 1) if total_rounds > 1 else 0
        st.caption(
            f"총 {total_rounds}개 라운드  |  "
            f"라운드 간 60초 대기  |  "
            f"예상 소요: 약 {wait_min}분+"
        )

        st.warning("실제 API 키를 소비합니다. Concurrency 한도는 테스트 중 자동으로 상향됩니다.")

        submitted = st.form_submit_button("키 부하 테스트 실행", type="primary", width="stretch")

    if submitted:
        if not selected_providers:
            st.error("최소 1개 provider를 선택해주세요.")
            return
        try:
            user_counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            st.error("사용자 수를 올바르게 입력해주세요.")
            return
        if not user_counts:
            st.error("최소 1개 사용자 수를 입력해주세요.")
            return

        _launch_plan(
            cfg, selected_providers, user_counts,
            mock_mode=False, lease_wait=30, lease_ttl=60,
            test_mode="burst",
        )


# ── 실제 부하테스트 (Realistic) ───────────────────────────

def render_stress_test_execution(cfg: AppConfig):
    """Realistic 모드: FIFO 통해 설정된 시간 내 랜덤 순차 요청으로 실제 운영 시뮬레이션."""
    st.subheader("실제 부하테스트")
    st.caption("설정된 시간 동안 사용자 수만큼 랜덤 시점에 요청을 보내 실제 운영 환경을 시뮬레이션합니다. FIFO 큐를 사용합니다.")

    if _is_running(test_mode="realistic"):
        _show_running_state(key_prefix="real")
        return
    _show_completed_state(key_prefix="real", test_mode="realistic")

    providers = _available_providers()

    with st.form("stress_real_form"):
        selected_providers = st.multiselect(
            "Provider (실행 순서대로)",
            providers,
            default=providers[:1],
        )

        user_counts_str = st.text_input(
            "사용자 수 (쉼표 구분)",
            value="5, 10, 15, 20",
            help="각 provider에 대해 이 수만큼 설정된 시간 내 랜덤 시점에 요청합니다.",
        )

        burst_window = st.slider(
            "요청 분산 시간 (초)",
            5, 120, 10,
            key="stress_burst_window",
            help="학생들이 몰리는 시간 폭. 10초=타이트(거의 동시), 60초=느슨(1분 분산)",
        )

        st.caption(
            "진행 방식: [분산 시간] 동안 요청 발사 → 모든 응답 대기 → 60초 RPM 회복 → 다음 라운드"
        )

        st.warning("실제 API 키를 소비합니다. FIFO 큐를 통한 실제 운영 흐름으로 테스트합니다.")

        submitted = st.form_submit_button("실제 부하테스트 실행", type="primary", width="stretch")

    if submitted:
        if not selected_providers:
            st.error("최소 1개 provider를 선택해주세요.")
            return
        try:
            user_counts = [int(x.strip()) for x in user_counts_str.split(",") if x.strip()]
        except ValueError:
            st.error("사용자 수를 올바르게 입력해주세요.")
            return
        if not user_counts:
            st.error("최소 1개 사용자 수를 입력해주세요.")
            return

        _launch_plan(
            cfg, selected_providers, user_counts,
            mock_mode=False, lease_wait=30, lease_ttl=60,
            test_mode="realistic", burst_window_sec=burst_window,
        )


# ── 결과 조회 ────────────────────────────────────────────

def render_stress_test_results(cfg: AppConfig, mock_mode: bool | None = None,
                                test_mode: str | None = None):
    """플랜 기반 부하 테스트 결과 조회 + 비교 차트.

    test_mode: "mock"|"burst"|"realistic" 필터 (우선).
    mock_mode: True=Mock만, False=Real만, None=전체 (하위호환).
    """
    plans = list_plan_ids(cfg, limit=20, mock_mode=mock_mode, test_mode=test_mode)
    if not plans:
        st.info("테스트 결과가 없습니다.")
        return

    st.subheader("플랜 목록")

    if test_mode:
        mode_suffix = f"_{test_mode}"
    else:
        mode_suffix = "_mock" if mock_mode is True else ("_real" if mock_mode is False else "_all")

    plan_options = {}
    for p in plans:
        # 날짜: "2026-03-20 14:30" 형식
        ts = (p.get("started_at") or "")[:16].replace("T", " ")
        # provider 목록: round_label에서 중복 제거
        rounds_str = p.get("rounds", "") or ""
        providers_set = []
        for rl in rounds_str.split(","):
            prov = rl.rsplit("_", 1)[0].strip()
            if prov and prov not in providers_set:
                providers_set.append(prov)
        prov_tag = ", ".join(providers_set) if providers_set else "?"
        label = f"{ts}  |  {prov_tag}  |  {p['round_count']}라운드"
        plan_options[p["plan_id"]] = label

    selected_plan_id = st.selectbox(
        "플랜 선택",
        options=list(plan_options.keys()),
        format_func=lambda pid: plan_options[pid],
        key=f"stress_plan_select{mode_suffix}",
    )

    if not selected_plan_id:
        return

    rounds = list_stress_test_runs(cfg, limit=100, plan_id=selected_plan_id)
    if not rounds:
        st.warning("라운드 데이터를 찾을 수 없습니다.")
        return

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
        "round_label", "provider", "num_users", "status", "mock_mode",
        "total_requests", "success_rate", "avg_latency_ms",
        "p95_ms", "p99_ms", "successes", "timeouts", "errors",
    ]
    existing = [c for c in summary_cols if c in df.columns]
    display_df = df[existing].copy()
    if "mock_mode" in display_df.columns:
        display_df["mock_mode"] = display_df["mock_mode"].map({True: "Mock", False: "Real"})
        display_df = display_df.rename(columns={"mock_mode": "모드"})
    st.dataframe(display_df, hide_index=True, width="stretch")

    # ── 비교 차트 ──
    if "num_users" in df.columns and "avg_latency_ms" in df.columns:
        st.subheader("평균 지연시간 비교 (사용자 수별)")
        try:
            pivot_latency = df.pivot_table(
                index="num_users", columns="provider",
                values="avg_latency_ms", aggfunc="first",
            )
            st.line_chart(pivot_latency)
        except Exception:
            st.caption("지연시간 비교 차트를 생성할 수 없습니다.")

    if "num_users" in df.columns and "success_rate" in df.columns:
        st.subheader("성공률 비교 (사용자 수별)")
        try:
            pivot_success = df.pivot_table(
                index="num_users", columns="provider",
                values="success_rate", aggfunc="first",
            )
            st.line_chart(pivot_success)
        except Exception:
            st.caption("성공률 비교 차트를 생성할 수 없습니다.")

    if "num_users" in df.columns and "p95_ms" in df.columns:
        st.subheader("P95 지연시간 비교 (사용자 수별)")
        try:
            pivot_p95 = df.pivot_table(
                index="num_users", columns="provider",
                values="p95_ms", aggfunc="first",
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
        key=f"stress_round_select{mode_suffix}",
    )

    if selected_round:
        _render_round_detail(cfg, selected_round, round_data)

    st.divider()
    if st.button("이 플랜 결과 전체 삭제", key=f"del_plan{mode_suffix}_{selected_plan_id}"):
        delete_stress_plan(cfg, selected_plan_id)
        st.rerun()


def _render_round_detail(cfg: AppConfig, test_id: str, round_data: list[dict]):
    """개별 라운드의 상세 정보 + 워커별 결과."""
    rd = next((r for r in round_data if r["test_id"] == test_id), None)
    if not rd:
        return

    mode_label = "Mock" if rd.get("mock_mode", True) else "Real"
    st.markdown(
        f"**{rd.get('round_label', '')}**  |  "
        f"**상태:** `{rd.get('status', '')}`  |  "
        f"**모드:** `{mode_label}`"
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

    if "worker_id" in sdf.columns and "duration_ms" in sdf.columns:
        st.markdown("**워커별 지연시간 (ms)**")
        worker_chart = sdf[["worker_id", "duration_ms"]].copy()
        worker_chart["worker_id"] = worker_chart["worker_id"].astype(str)
        st.bar_chart(worker_chart, x="worker_id", y="duration_ms")

    if "status" in sdf.columns:
        st.markdown("**요청 상태 분포**")
        st.bar_chart(sdf["status"].value_counts())

    key_details = rd.get("key_details")
    if key_details:
        st.markdown("**API 키별 상태**")
        kd_rows = []
        for kn, kd in key_details.items():
            kd_rows.append({
                "키": kn,
                "요청": kd["requests"],
                "성공": kd["successes"],
                "타임아웃": kd["timeouts"],
                "에러": kd["errors"],
                "성공률(%)": kd["success_rate"],
                "평균지연(ms)": kd["avg_latency_ms"],
            })
        st.dataframe(pd.DataFrame(kd_rows), hide_index=True, width="stretch")
    elif rd.get("key_distribution"):
        st.markdown("**API 키별 요청 분배**")
        key_df = pd.DataFrame(
            list(rd["key_distribution"].items()),
            columns=["key_name", "count"],
        )
        st.bar_chart(key_df, x="key_name", y="count")

    with st.expander("전체 샘플 데이터"):
        display_cols = [
            "worker_id", "request_seq", "duration_ms", "status",
            "error_text", "key_name", "started_at",
        ]
        existing = [c for c in display_cols if c in sdf.columns]
        st.dataframe(sdf[existing], hide_index=True, width="stretch")
