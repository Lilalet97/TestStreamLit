# ui/sidebar.py
import streamlit as st
from dataclasses import dataclass
from typing import Callable

from core.config import AppConfig
from core.db import list_runs, count_active_jobs, clear_my_active_jobs


@dataclass
class SidebarState:
    session_only: bool
    test_mode: bool
    mock_scenario: str
    refresh_counts: Callable[[], None]


def render_sidebar(cfg: AppConfig) -> SidebarState:
    with st.sidebar:
        st.markdown("## 🧑‍🏫 교육용 세션")

        new_user = st.text_input("User ID", value=st.session_state.user_id)
        if new_user != st.session_state.user_id:
            st.session_state.user_id = new_user.strip() or "guest"

        if st.button("새 세션 시작"):
            import uuid
            st.session_state.session_id = str(uuid.uuid4())
            st.success("새 세션이 시작되었습니다.")

        st.caption(f"session_id: {st.session_state.session_id}")
        st.markdown("---")

        st.markdown("## ⛓️ 동시 실행 제한")
        st.write(f"- 사용자 제한: {cfg.user_max_concurrency}")
        st.write(f"- 전체 제한: {cfg.global_max_concurrency}")
        my_active_ph = st.empty()
        all_active_ph = st.empty()

        def refresh_counts():
            my_active_ph.write(f"- 내 활성 작업: {count_active_jobs(cfg, st.session_state.user_id)}")
            all_active_ph.write(f"- 전체 활성 작업: {count_active_jobs(cfg, None)}")

        refresh_counts()

        st.markdown("---")
        st.markdown("## 📜 실행 히스토리")
        session_only = st.toggle("현재 세션만 보기", value=True)

        hist = list_runs(cfg, st.session_state.user_id, session_only=session_only, limit=30)

        def _label(r):
            t = r["created_at"].replace("T", " ").replace("Z", "")
            return f'{t} | {r["provider"]}/{r["operation"]} | {r["state"]}'

        if hist:
            options = [r["run_id"] for r in hist]

            # ✅ 현재 선택 유지 (가능하면)
            prev_sel = st.session_state.get("selected_run_id")
            if prev_sel in options:
                idx = options.index(prev_sel)
            else:
                idx = 0

            sel = st.selectbox(
                "최근 실행 선택",
                options=options,
                index=idx,
                format_func=lambda rid: _label(next(x for x in hist if x["run_id"] == rid)),
                key="selected_run_id",
            )

            # ✅ 버튼 클릭만 상세 열기 트리거
            if st.button("선택한 실행 상세 열기", use_container_width=True):
                if sel:
                    st.session_state["_open_run_detail"] = True
                else:
                    st.session_state["_open_run_detail"] = False
        else:
            st.info("실행 기록이 아직 없습니다.")
            st.session_state["selected_run_id"] = None
            st.session_state["_open_run_detail"] = False

        st.markdown("---")
        st.markdown("## 🧪 테스트 모드")
        test_mode = st.toggle("MOCK 모드(크레딧 없이)", value=False, help="외부 API를 호출하지 않고 로컬에서 응답을 시뮬레이션합니다.")
        mock_scenario = "SUCCESS"
        if test_mode:
            mock_scenario = st.selectbox(
                "MOCK 시나리오",
                ["SUCCESS", "FAILED_402", "FAILED_401", "FAILED_429", "SERVER_500", "TIMEOUT"],
                index=0
            )

        if st.button("🧹 내 활성 작업 강제 정리"):
            clear_my_active_jobs(cfg, session_only=False, only_stale=False)
            st.success("내 active_jobs를 정리했습니다.")
            st.rerun()

    return SidebarState(
        session_only=session_only,
        test_mode=test_mode,
        mock_scenario=mock_scenario,
        refresh_counts=refresh_counts,
    )
