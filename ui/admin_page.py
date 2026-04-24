# ui/admin_page.py
from pathlib import Path
import streamlit as st

from core.config import AppConfig
from core.auth import current_user, hash_password
from core.db import (
    list_users,
    list_mj_gallery_admin,
    get_mj_gallery_by_id,
    list_gpt_conversations_admin,
    get_gpt_conversation_by_id,
    list_kling_web_admin,
    get_kling_web_by_id,
    list_elevenlabs_admin,
    get_elevenlabs_by_id,
    list_nanobanana_sessions_admin,
    get_nanobanana_session_by_id,
    upsert_user,
    update_user_fields,
    set_user_password,
    set_user_active,
    hard_delete_user,
    PURGEABLE_TABLES,
    get_all_admin_settings,
    set_admin_setting,
    get_table_row_counts,
    count_old_rows,
    purge_old_records,
    run_auto_purge,
    list_legacy_tables,
    drop_legacy_tables,
    reset_all_data,
    get_user_balance,
    set_user_balance,
    init_user_balance_from_default,
    add_balance_bulk,
    get_school_credit_report,
    get_student_credit_report,
    get_usage_call_counts,
    get_api_keys_summary, set_api_key_active,
    get_admin_setting,
    list_class_schedules,
    insert_class_schedule,
    update_class_schedule,
    delete_class_schedule,
    log_admin_action,
)
from core.credits import (
    FEATURE_IDS, FEATURE_LABELS, FEATURE_UNITS,
    get_feature_cost, get_api_unit_cost, get_exchange_rate,
)
from providers.gdrive import extract_folder_id
from core.stress_test import PROVIDER_ORDER
from ui.stress_test_tab import render_algorithm_test, render_burst_test, render_stress_test_execution, render_stress_test_results
from ui.stress_report import render_stress_report


def _rows_to_dicts(rows):
    return [dict(r) for r in (rows or [])]


def _render_gpt_detail(cfg: AppConfig, conv_id: str):
    """GPT 대화 상세 내용을 렌더링."""
    conv = get_gpt_conversation_by_id(cfg, conv_id)
    if not conv:
        st.warning('대화를 찾을 수 없습니다.')
        return
    st.markdown(f"**{conv['title']}**  ·  `{conv['model']}`")
    st.caption(f"user: {conv['user_id']}  |  created: {conv['created_at']}  |  updated: {conv['updated_at']}")
    st.divider()
    if not conv['messages']:
        st.info('메시지가 없습니다.')
        return
    for msg in conv['messages']:
        role = msg.get('role', 'user')
        with st.chat_message(role):
            st.markdown(msg.get('content', ''))


def _maybe_open_gpt_dialog(cfg: AppConfig):
    """GPT 대화 보기 다이얼로그 트리거."""
    conv_id = st.session_state.get('_view_gpt_conv_id')
    if not conv_id or not st.session_state.get('_open_gpt_detail'):
        return
    st.session_state['_open_gpt_detail'] = False

    if hasattr(st, 'dialog'):
        @st.dialog('💬 GPT 대화 내용', width='large')
        def _dlg():
            _render_gpt_detail(cfg, conv_id)
        _dlg()
    else:
        with st.expander('💬 GPT 대화 내용', expanded=True):
            _render_gpt_detail(cfg, conv_id)


def _render_mj_detail(cfg: AppConfig, row_id: int):
    """MJ 갤러리 아이템 상세 내용을 렌더링."""
    item = get_mj_gallery_by_id(cfg, row_id)
    if not item:
        st.warning('항목을 찾을 수 없습니다.')
        return
    st.markdown(f"**{(item['prompt'] or '')[:100]}**")
    st.caption(f"user: {item['user_id']}  |  비율: {item['aspect_ratio']}  |  created: {item['created_at']}")
    st.divider()

    # 프롬프트 전문
    st.text_area('Prompt', item['prompt'], height=100, disabled=True)

    # 태그
    if item['tags']:
        st.markdown('**Tags:** ' + ', '.join(f'`{t}`' for t in item['tags']))

    # 설정
    if item['settings']:
        with st.expander('Settings'):
            st.json(item['settings'])

    # 생성 이미지
    images = item.get('images') or []
    if images:
        st.subheader(f'생성 이미지 ({len(images)}장)')
        cols = st.columns(min(len(images), 4))
        for i, url in enumerate(images):
            with cols[i % len(cols)]:
                st.image(url, width='stretch')

    # 첨부 이미지 (dict: {"imagePrompts": [...], "styleRef": [...], "omniRef": [...]})
    attached = item.get('attached_images')
    if attached and isinstance(attached, dict):
        label_map = {"imagePrompts": "Image Prompts", "styleRef": "Style Ref", "omniRef": "Omni Ref"}
        for key in ("imagePrompts", "styleRef", "omniRef"):
            imgs = attached.get(key) or []
            if not imgs:
                continue
            st.subheader(f'첨부: {label_map.get(key, key)} ({len(imgs)}장)')
            cols2 = st.columns(min(len(imgs), 4))
            for i, data_url in enumerate(imgs):
                with cols2[i % len(cols2)]:
                    st.image(data_url, width='stretch')


def _maybe_open_mj_dialog(cfg: AppConfig):
    """MJ 갤러리 보기 다이얼로그 트리거."""
    row_id = st.session_state.get('_view_mj_row_id')
    if not row_id or not st.session_state.get('_open_mj_detail'):
        return
    st.session_state['_open_mj_detail'] = False

    if hasattr(st, 'dialog'):
        @st.dialog('🎨 Midjourney 상세', width='large')
        def _dlg():
            _render_mj_detail(cfg, row_id)
        _dlg()
    else:
        with st.expander('🎨 Midjourney 상세', expanded=True):
            _render_mj_detail(cfg, row_id)


def _render_kling_detail(cfg: AppConfig, row_id: int):
    """Kling 웹 히스토리 상세 내용을 렌더링."""
    item = get_kling_web_by_id(cfg, row_id)
    if not item:
        st.warning('항목을 찾을 수 없습니다.')
        return
    st.markdown(f"**{(item['prompt'] or '')[:100]}**  ·  `{item['model_label']}`")
    st.caption(
        f"user: {item['user_id']}  |  model: {item['model_id']} v{item['model_ver']}  |  "
        f"created: {item['created_at']}"
    )
    st.divider()

    # 프롬프트 전문
    st.text_area('Prompt', item['prompt'], height=100, disabled=True)

    # 메타 정보
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Frame Mode', item['frame_mode'] or 'N/A')
    c2.metric('Sound', 'ON' if item['sound_enabled'] else 'OFF')
    c3.metric('Start Frame', 'O' if item['has_start_frame'] else 'X')
    c4.metric('End Frame', 'O' if item['has_end_frame'] else 'X')

    # 프레임 이미지
    start_data = item.get('start_frame_data')
    end_data = item.get('end_frame_data')
    if start_data or end_data:
        st.subheader('프레임 이미지')
        fc1, fc2 = st.columns(2)
        with fc1:
            if start_data:
                st.caption('Start Frame')
                st.image(start_data, width='stretch')
        with fc2:
            if end_data:
                st.caption('End Frame')
                st.image(end_data, width='stretch')

    # 설정
    if item['settings']:
        with st.expander('Settings'):
            st.json(item['settings'])

    # 비디오 URL
    urls = item.get('video_urls') or []
    if urls:
        st.subheader(f'생성 비디오 ({len(urls)}개)')
        for i, url in enumerate(urls):
            st.caption(f'Video {i + 1}')
            st.video(url)
    else:
        st.info('생성된 비디오가 없습니다.')


def _maybe_open_kling_dialog(cfg: AppConfig):
    """Kling 웹 보기 다이얼로그 트리거."""
    row_id = st.session_state.get('_view_kling_row_id')
    if not row_id or not st.session_state.get('_open_kling_detail'):
        return
    st.session_state['_open_kling_detail'] = False

    if hasattr(st, 'dialog'):
        @st.dialog('🎬 Kling Web 상세', width='large')
        def _dlg():
            _render_kling_detail(cfg, row_id)
        _dlg()
    else:
        with st.expander('🎬 Kling Web 상세', expanded=True):
            _render_kling_detail(cfg, row_id)


def _render_elevenlabs_detail(cfg: AppConfig, row_id: int):
    """ElevenLabs TTS 히스토리 상세 내용을 렌더링."""
    item = get_elevenlabs_by_id(cfg, row_id)
    if not item:
        st.warning('항목을 찾을 수 없습니다.')
        return
    st.markdown(f"**{(item['text'] or '')[:100]}**  ·  `{item['voice_name']}`")
    st.caption(
        f"user: {item['user_id']}  |  voice: {item['voice_name']}  |  "
        f"model: {item['model_label']}  |  created: {item['created_at']}"
    )
    st.divider()

    st.text_area('Text', item['text'], height=100, disabled=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Voice', item['voice_name'] or 'N/A')
    c2.metric('Model', item['model_label'] or 'N/A')
    c3.metric('Lang Override', 'ON' if item['language_override'] else 'OFF')
    c4.metric('Speaker Boost', 'ON' if item['speaker_boost'] else 'OFF')

    if item['settings']:
        with st.expander('Settings'):
            st.json(item['settings'])

    audio_url = item.get('audio_url') or ''
    if audio_url and audio_url.startswith(('http://', 'https://', 'data:')):
        st.subheader('생성 오디오')
        st.audio(audio_url)
    elif audio_url:
        st.info(f'오디오 재생 불가 (값: {audio_url})')
    else:
        st.info('생성된 오디오가 없습니다.')


def _maybe_open_elevenlabs_dialog(cfg: AppConfig):
    """ElevenLabs 보기 다이얼로그 트리거."""
    row_id = st.session_state.get('_view_elevenlabs_row_id')
    if not row_id or not st.session_state.get('_open_elevenlabs_detail'):
        return
    st.session_state['_open_elevenlabs_detail'] = False

    if hasattr(st, 'dialog'):
        @st.dialog('🔊 ElevenLabs 상세', width='large')
        def _dlg():
            _render_elevenlabs_detail(cfg, row_id)
        _dlg()
    else:
        with st.expander('🔊 ElevenLabs 상세', expanded=True):
            _render_elevenlabs_detail(cfg, row_id)



def _render_nanobanana_session_detail(cfg: AppConfig, session_id: str):
    """NanoBanana 세션 상세 내용을 렌더링 (턴별 프롬프트 + 이미지)."""
    session = get_nanobanana_session_by_id(cfg, session_id)
    if not session:
        st.warning('세션을 찾을 수 없습니다.')
        return
    st.markdown(f"**{session['title']}**  ·  `{session['model']}`")
    st.caption(
        f"user: {session['user_id']}  |  created: {session['created_at']}  |  "
        f"updated: {session['updated_at']}"
    )
    st.divider()

    turns = session.get('turns') or []
    if not turns:
        st.info('턴이 없습니다.')
        return

    for idx, turn in enumerate(turns):
        label = "EDIT" if turn.get('is_edit') else "GEN"
        st.markdown(f"**Turn {idx + 1}** · `{label}` · {turn.get('model_label', 'N/A')} · {turn.get('aspect_ratio', '1:1')}")
        st.text_area(f'Prompt (Turn {idx + 1})', turn.get('prompt', ''), height=80, disabled=True, key=f'nb_sess_prompt_{session_id}_{idx}')

        if turn.get('negative_prompt'):
            st.caption(f"Negative: {turn['negative_prompt']}")

        images = turn.get('image_urls') or []
        if images:
            cols = st.columns(min(len(images), 4))
            for i, url in enumerate(images):
                with cols[i % len(cols)]:
                    if url and url.startswith(('http://', 'https://', 'data:')):
                        st.image(url, width='stretch')
                    else:
                        st.info(f'이미지 표시 불가')
        else:
            st.info('이미지 없음')

        if idx < len(turns) - 1:
            st.divider()


def _maybe_open_nanobanana_session_dialog(cfg: AppConfig):
    """NanoBanana 세션 보기 다이얼로그 트리거."""
    session_id = st.session_state.get('_view_nb_session_id')
    if not session_id or not st.session_state.get('_open_nb_session_detail'):
        return
    st.session_state['_open_nb_session_detail'] = False

    if hasattr(st, 'dialog'):
        @st.dialog('\U0001f34c NanoBanana 세션 상세', width='large')
        def _dlg():
            _render_nanobanana_session_detail(cfg, session_id)
        _dlg()
    else:
        with st.expander('\U0001f34c NanoBanana 세션 상세', expanded=True):
            _render_nanobanana_session_detail(cfg, session_id)


def _list_tenant_ids(cfg: AppConfig) -> list[str]:
    """tenants 디렉토리의 JSON 파일에서 tenant_id 목록을 반환."""
    tenant_dir = Path(cfg.tenant_config_dir) if cfg.tenant_config_dir else Path(".")
    candidates = [tenant_dir] + [Path("tenants")]
    seen = set()
    ids = []
    for d in candidates:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            tid = f.stem
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)
    return ids or ["default"]

def _render_key_pool_summary(cfg: AppConfig, editable: bool = False):
    """키풀 현황: 등록된 API 키 요약 표시. editable=True이면 활성/비활성 토글 제공."""
    import pandas as pd
    st.subheader("등록된 API 키")
    keys = get_api_keys_summary(cfg)
    if not keys:
        st.info("등록된 API 키가 없습니다.")
        return

    # provider별 그룹핑
    by_prov: dict[str, list] = {}
    for k in keys:
        by_prov.setdefault(k["provider"], []).append(k)

    # registry.py 탭 순서 기준 정렬
    ordered_provs = [p for p in PROVIDER_ORDER if p in by_prov]
    ordered_provs += sorted(set(by_prov.keys()) - set(ordered_provs))

    for provider in ordered_provs:
        pkeys = by_prov[provider]
        active_cnt = sum(1 for k in pkeys if k["is_active"])
        total_conc = sum(k["concurrency_limit"] for k in pkeys if k["is_active"])
        st.markdown(
            f"**{provider}** "
            f"<span style='font-size:0.85em;color:gray;'>"
            f"활성 {active_cnt}/{len(pkeys)}  |  동시 사용 {total_conc}</span>",
            unsafe_allow_html=True,
        )

        for k in pkeys:
            cur_active = bool(k["is_active"])
            kid = k["api_key_id"]

            if editable:
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 1.5])
            else:
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 1.5])

            with c1:
                status = "🟢" if cur_active else "🔴"
                st.markdown(f"{status} **{k['key_name']}**")
            with c2:
                st.caption(f"동시 {k['concurrency_limit']}")
            with c3:
                st.caption(f"RPM {k['rpm_limit'] or '-'}")
            with c4:
                if editable:
                    new_active = st.toggle(
                        "활성", value=cur_active,
                        key=f"key_toggle_{kid}",
                        label_visibility="collapsed",
                    )
                    if new_active != cur_active:
                        set_api_key_active(cfg, kid, new_active)
                        st.rerun()
                else:
                    st.caption("활성" if cur_active else "비활성")

        st.markdown("---")


def _render_api_cost_estimation(cfg: AppConfig, editable: bool = False, key_prefix: str = ""):
    """API 비용 추정 섹션. editable=True이면 단가/환율 설정 가능."""
    import pandas as pd

    st.subheader("API 비용 추정")
    st.caption("credit_usage_log의 호출 횟수 × 설정 단가로 추정합니다. 실제 청구 금액과 다를 수 있습니다.")

    cc1, cc2 = st.columns(2)
    with cc1:
        cost_days = st.selectbox(
            "기간", [7, 14, 30, 60, 90], index=2,
            format_func=lambda d: f"최근 {d}일",
            key=f"{key_prefix}cost_days",
        )
    with cc2:
        rate = get_exchange_rate(cfg)
        if editable:
            rate = st.number_input(
                "환율 (USD→KRW)", min_value=1.0, max_value=99999.0,
                value=float(rate), step=10.0, key=f"{key_prefix}exchange_rate",
            )
        else:
            st.metric("환율 (USD→KRW)", f"₩{rate:,.0f}")

    counts = get_usage_call_counts(cfg, days=cost_days)
    rows = []
    total_usd = 0.0
    for fid in FEATURE_IDS:
        cnt = counts.get(fid, 0)
        if cnt == 0:
            continue
        unit_cost = get_api_unit_cost(cfg, fid)
        est_usd = cnt * unit_cost
        total_usd += est_usd
        rows.append({
            "기능": FEATURE_LABELS.get(fid, fid),
            "호출 수": cnt,
            "단가 (USD)": f"${unit_cost:.4f}",
            "추정 비용 (USD)": f"${est_usd:.2f}",
            "추정 비용 (KRW)": f"₩{est_usd * rate:,.0f}",
        })

    if rows:
        rows.append({
            "기능": "합계",
            "호출 수": sum(r["호출 수"] for r in rows),
            "단가 (USD)": "",
            "추정 비용 (USD)": f"${total_usd:.2f}",
            "추정 비용 (KRW)": f"₩{total_usd * rate:,.0f}",
        })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("해당 기간에 사용 내역이 없습니다.")

    # 관리자만 단가 설정 가능
    if editable:
        st.markdown("---")
        st.subheader("API 단가 설정 (USD)")
        st.caption("기능별 API 1회 호출당 예상 비용 (USD). 0 = 비용 추정 제외.")
        with st.form(f"{key_prefix}api_cost_form"):
            new_costs: dict[str, float] = {}
            cols = st.columns(3)
            for i, fid in enumerate(FEATURE_IDS):
                with cols[i % 3]:
                    cur = float(get_api_unit_cost(cfg, fid))
                    new_costs[fid] = st.number_input(
                        FEATURE_LABELS.get(fid, fid),
                        min_value=0.0, max_value=99.0,
                        value=cur, step=0.001, format="%.4f",
                        key=f"{key_prefix}api_cost_{fid}",
                    )
            if st.form_submit_button("단가 저장", width="stretch"):
                for fid, val in new_costs.items():
                    set_admin_setting(cfg, f"api_unit_cost.{fid}", f"{val:.6f}")
                set_admin_setting(cfg, "api_exchange_rate", str(rate))
                st.session_state["_admin_toast"] = "저장되었습니다."
                st.rerun()


def render_viewer_page(cfg: AppConfig):
    """viewer 역할용: 크레딧 현황, 키풀, 실행 기록 등 (읽기 전용)."""
    u = current_user()
    if not u or u.role != 'viewer':
        st.error('viewer 권한이 필요합니다.')
        return

    _VIEWER_GROUPS = [
        ("운영",  ["시간표"]),
        ("관리",  ["크레딧 현황", "키풀 관리"]),
        ("기록",  ["실행 기록", "부하테스트 결과"]),
    ]

    if "_viewer_active" not in st.session_state:
        st.session_state["_viewer_active"] = "시간표"

    with st.sidebar:
        for group_name, items in _VIEWER_GROUPS:
            st.caption(group_name)
            for item in items:
                is_active = st.session_state["_viewer_active"] == item
                if st.button(
                    item,
                    key=f"_vm_{item}",
                    width="stretch",
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state["_viewer_active"] = item
                    st.rerun()

    selected_label = st.session_state["_viewer_active"]

    if selected_label == "키풀 관리":
        _render_key_pool_summary(cfg)

    elif selected_label == "실행 기록":
        # viewer는 자기 학교 사용자만 조회 가능
        viewer_school = u.school_id
        all_users = list_users(cfg, include_inactive=True)
        user_rows = [r for r in all_users if r.get('school_id') == viewer_school]
        user_ids = ['(all)'] + [r['user_id'] for r in user_rows]
        sel_user = st.selectbox('필터: user_id', user_ids, index=0, key='viewer_user_filter')
        limit = st.slider('표시 개수', 50, 500, 200, 50, key='viewer_limit')

        filter_uid = None if sel_user == '(all)' else sel_user
        _school_uids = {r['user_id'] for r in user_rows}

        def _filter_school(items):
            """viewer 학교에 속한 사용자 기록만 필터."""
            if filter_uid:
                return items
            return [it for it in items if (it.get('user_id') or '') in _school_uids]

        # ── GPT Conversations ──
        st.subheader('💬 GPT Conversations')
        gpt_items = _filter_school(list_gpt_conversations_admin(cfg, limit=limit, user_id=filter_uid))
        if gpt_items:
            import pandas as pd
            df = pd.DataFrame(gpt_items)
            df.insert(0, '보기', False)

            tbl_ver = st.session_state.get('_v_gpt_tbl_ver', 0)
            edited = st.data_editor(
                df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'v_gpt_conv_table_{tbl_ver}',
            )

            checked = edited.index[edited['보기'] == True].tolist()
            if checked:
                idx = checked[0]
                if idx < len(gpt_items):
                    st.session_state['_view_gpt_conv_id'] = gpt_items[idx]['id']
                    st.session_state['_open_gpt_detail'] = True
                    st.session_state['_v_gpt_tbl_ver'] = tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 GPT 대화가 없습니다.')

        _maybe_open_gpt_dialog(cfg)

        # ── Midjourney ──
        st.subheader('🎨 Midjourney')
        mj_rows = list_mj_gallery_admin(cfg, limit=limit, user_id=filter_uid)
        mj_items = _filter_school(_rows_to_dicts(mj_rows))
        if mj_items:
            import pandas as pd
            mj_df = pd.DataFrame(mj_items)
            mj_df.insert(0, '보기', False)

            mj_tbl_ver = st.session_state.get('_v_mj_tbl_ver', 0)
            mj_edited = st.data_editor(
                mj_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in mj_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'v_mj_table_{mj_tbl_ver}',
            )

            mj_checked = mj_edited.index[mj_edited['보기'] == True].tolist()
            if mj_checked:
                idx = mj_checked[0]
                if idx < len(mj_items):
                    st.session_state['_view_mj_row_id'] = mj_items[idx]['id']
                    st.session_state['_open_mj_detail'] = True
                    st.session_state['_v_mj_tbl_ver'] = mj_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 MJ 기록이 없습니다.')

        _maybe_open_mj_dialog(cfg)

        # ── NanoBanana Sessions (멀티턴) ──
        st.subheader('\U0001f34c NanoBanana Sessions')
        nb_sessions = _filter_school(list_nanobanana_sessions_admin(cfg, limit=limit, user_id=filter_uid))
        if nb_sessions:
            import pandas as pd
            nb_df = pd.DataFrame(nb_sessions)
            nb_df.insert(0, '보기', False)

            nb_tbl_ver = st.session_state.get('_v_nb_tbl_ver', 0)
            nb_edited = st.data_editor(
                nb_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in nb_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'v_nb_table_{nb_tbl_ver}',
            )

            nb_checked = nb_edited.index[nb_edited['보기'] == True].tolist()
            if nb_checked:
                idx = nb_checked[0]
                if idx < len(nb_sessions):
                    st.session_state['_view_nb_session_id'] = nb_sessions[idx]['id']
                    st.session_state['_open_nb_session_detail'] = True
                    st.session_state['_v_nb_tbl_ver'] = nb_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 NanoBanana 세션이 없습니다.')

        _maybe_open_nanobanana_session_dialog(cfg)

        # ── Kling Web ──
        st.subheader('🎬 Kling Web')
        kling_items = _filter_school(list_kling_web_admin(cfg, limit=limit, user_id=filter_uid))
        if kling_items:
            import pandas as pd
            kling_df = pd.DataFrame(kling_items)
            kling_df.insert(0, '보기', False)

            kling_tbl_ver = st.session_state.get('_v_kling_tbl_ver', 0)
            kling_edited = st.data_editor(
                kling_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in kling_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'v_kling_web_table_{kling_tbl_ver}',
            )

            kling_checked = kling_edited.index[kling_edited['보기'] == True].tolist()
            if kling_checked:
                idx = kling_checked[0]
                if idx < len(kling_items):
                    st.session_state['_view_kling_row_id'] = kling_items[idx]['id']
                    st.session_state['_open_kling_detail'] = True
                    st.session_state['_v_kling_tbl_ver'] = kling_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 Kling Web 기록이 없습니다.')

        _maybe_open_kling_dialog(cfg)

        # ── ElevenLabs TTS ──
        st.subheader('🔊 ElevenLabs TTS')
        el_items = _filter_school(list_elevenlabs_admin(cfg, limit=limit, user_id=filter_uid))
        if el_items:
            import pandas as pd
            el_df = pd.DataFrame(el_items)
            el_df.insert(0, '보기', False)

            el_tbl_ver = st.session_state.get('_v_el_tbl_ver', 0)
            el_edited = st.data_editor(
                el_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in el_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'v_el_table_{el_tbl_ver}',
            )

            el_checked = el_edited.index[el_edited['보기'] == True].tolist()
            if el_checked:
                idx = el_checked[0]
                if idx < len(el_items):
                    st.session_state['_view_elevenlabs_row_id'] = el_items[idx]['id']
                    st.session_state['_open_elevenlabs_detail'] = True
                    st.session_state['_v_el_tbl_ver'] = el_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 ElevenLabs 기록이 없습니다.')

        _maybe_open_elevenlabs_dialog(cfg)

    elif selected_label == "부하테스트 결과":
        render_stress_report(cfg, school_id=u.school_id)

    # ── 크레딧 현황 (읽기 전용) ──
    elif selected_label == "크레딧 현황":
        import pandas as pd

        st.subheader("학교별 크레딧 현황")
        report_days = st.selectbox(
            "기간", [7, 14, 30, 60, 90], index=2,
            format_func=lambda d: f"최근 {d}일",
            key="v_report_days",
        )
        report = get_school_credit_report(cfg, days=report_days)
        if report:
            rows = []
            for r in report:
                row = {"학교": r["school_id"], "잔여 크레딧": r["remaining"], "사용자 수": r["user_count"]}
                for fid in FEATURE_IDS:
                    row[FEATURE_LABELS.get(fid, fid)] = r["used_by_tab"].get(fid, 0)
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("아직 크레딧 사용 내역이 없습니다.")

        st.markdown("---")

        st.subheader("학생별 크레딧 현황")
        sc1, sc2 = st.columns(2)
        with sc1:
            student_days = st.selectbox(
                "기간", [7, 14, 30, 60, 90], index=2,
                format_func=lambda d: f"최근 {d}일",
                key="v_student_report_days",
            )
        with sc2:
            school_opts = ["전체"] + _list_tenant_ids(cfg)
            student_school = st.selectbox(
                "학교",
                school_opts,
                format_func=lambda x: x if x == "전체" else f"{cfg.get_layout(x)} ({x})",
                key="v_student_report_school",
            )
        student_report = get_student_credit_report(
            cfg,
            school_id=None if student_school == "전체" else student_school,
            days=student_days,
        )
        if student_report:
            s_rows = []
            for r in student_report:
                s_row = {
                    "학교": r["school_id"],
                    "사용자": r["user_id"],
                    "역할": r["role"],
                    "잔여 크레딧": r["remaining"],
                }
                total_used = 0
                for fid in FEATURE_IDS:
                    used = r["used_by_tab"].get(fid, 0)
                    s_row[FEATURE_LABELS.get(fid, fid)] = used
                    total_used += used
                s_row["합계 사용"] = total_used
                s_rows.append(s_row)
            st.dataframe(pd.DataFrame(s_rows), width="stretch", hide_index=True)
        else:
            st.info("해당 조건에 맞는 사용자가 없습니다.")


    # ── 시간표 (읽기 전용) ──
    elif selected_label == "시간표":
        tenant_ids = _list_tenant_ids(cfg)
        schedules = list_class_schedules(cfg)

        st.subheader("수업 시간표")
        st.caption(
            "수업 시간에는 해당 학교 학생만 전체 탭을 사용할 수 있고, "
            "다른 학교 학생은 갤러리 탭만 이용 가능합니다."
        )

        _render_timetable_grid(cfg, schedules, tenant_ids)

        if schedules:
            st.markdown("---")
            st.subheader("등록된 수업 목록")
            for s in schedules:
                day_label = _DAY_LABELS[s["day_of_week"]] if 0 <= s["day_of_week"] < 7 else "?"
                start_str = f'{s["start_hour"]:02d}:{s["start_minute"]:02d}'
                end_str = f'{s["end_hour"]:02d}:{s["end_minute"]:02d}'
                color = _school_color(tenant_ids, s["school_id"])
                school_name = cfg.get_layout(s["school_id"])
                st.markdown(
                    f'<span style="display:inline-block;width:12px;height:12px;border-radius:3px;'
                    f'background:{color};vertical-align:middle;margin-right:6px;"></span>'
                    f'**{s.get("label", "")}** · {school_name} · {day_label} {start_str}~{end_str}',
                    unsafe_allow_html=True,
                )


def _render_db_management(cfg: AppConfig):
    """DB 관리 탭: 테이블 현황, 수동 삭제, 자동 삭제 설정."""
    import pandas as pd

    st.subheader("테이블 현황")
    counts = get_table_row_counts(cfg)
    purge_settings = get_all_admin_settings(cfg, prefix="purge_days.")

    overview = []
    for tbl in PURGEABLE_TABLES:
        cnt = counts.get(tbl["key"], 0)
        child_cnt = counts.get(tbl["key"] + "_child")
        days = purge_settings.get(f"purge_days.{tbl['key']}", "0")
        display = f"{cnt:,}"
        if child_cnt is not None:
            display += f"  (+ samples {child_cnt:,})"
        overview.append({
            "테이블": tbl["label"],
            "레코드 수": display,
            "자동 삭제": f"{days}일" if days != "0" else "비활성",
        })
    st.dataframe(pd.DataFrame(overview), hide_index=True, width="stretch")

    st.divider()

    # ── 수동 삭제 ──
    st.subheader("수동 삭제")
    col1, col2 = st.columns(2)
    with col1:
        tbl_opts = {t["key"]: t["label"] for t in PURGEABLE_TABLES}
        sel_table = st.selectbox("대상 테이블", list(tbl_opts.keys()),
                                 format_func=lambda k: tbl_opts[k], key="db_purge_table")
    with col2:
        del_days = st.number_input("N일 이전 데이터 삭제", min_value=1, max_value=3650,
                                   value=30, step=1, key="db_purge_days")

    if sel_table and del_days > 0:
        old_cnt = count_old_rows(cfg, sel_table, del_days)
        st.info(f"**{tbl_opts[sel_table]}** 에서 {del_days}일 이전 레코드: **{old_cnt:,}건**")

        if old_cnt > 0:
            confirm = st.text_input(
                f"삭제 확인: 아래에 **{sel_table}** 을 입력하세요",
                key="db_purge_confirm",
            )
            if st.button("삭제 실행", type="primary", key="db_purge_btn"):
                if confirm.strip() == sel_table:
                    deleted = purge_old_records(cfg, sel_table, del_days)
                    log_admin_action(cfg, st.session_state.get("user_id", ""), "purge_records", sel_table, f"{deleted} rows, older_than={del_days}d")
                    st.session_state["_admin_toast"] = f"{deleted:,}건 삭제 완료"
                    st.rerun()
                else:
                    st.error("확인 문구가 일치하지 않습니다.")

    st.divider()

    # ── 자동 삭제 설정 ──
    st.subheader("자동 삭제 설정")
    st.caption("0 = 비활성 (자동 삭제 안 함). 앱 시작 시 세션당 1회 자동 실행됩니다.")

    with st.form("auto_purge_form"):
        new_vals = {}
        cols = st.columns(3)
        for i, tbl in enumerate(PURGEABLE_TABLES):
            cur_val = purge_settings.get(f"purge_days.{tbl['key']}", "0")
            with cols[i % 3]:
                new_vals[tbl["key"]] = st.number_input(
                    tbl["label"], min_value=0, max_value=3650,
                    value=int(cur_val) if cur_val.isdigit() else 0,
                    step=1, key=f"purge_days_{tbl['key']}",
                )
        submitted = st.form_submit_button("설정 저장", width="stretch")

    if submitted:
        for tbl in PURGEABLE_TABLES:
            set_admin_setting(cfg, f"purge_days.{tbl['key']}", str(new_vals[tbl["key"]]))
        st.session_state["_admin_toast"] = "자동 삭제 설정이 저장되었습니다."
        st.rerun()

    if st.button("지금 자동 삭제 실행", key="db_purge_now"):
        results = run_auto_purge(cfg)
        if results:
            for key, cnt in results.items():
                label = next((t["label"] for t in PURGEABLE_TABLES if t["key"] == key), key)
                st.session_state["_admin_toast"] = f"{label}: {cnt:,}건 삭제"
        else:
            st.info("삭제 대상이 없거나 자동 삭제가 비활성 상태입니다.")

    st.divider()

    # ── 레거시 테이블 정리 ──
    st.subheader("미사용 테이블 정리")
    st.caption("업데이트 후 더 이상 사용하지 않는 레거시 테이블을 삭제합니다.")

    legacy = list_legacy_tables(cfg)
    if legacy:
        for lt in legacy:
            st.markdown(
                f"- **{lt['label']}** (`{lt['table']}`) — {lt['row_count']:,}건  \n"
                f"  사유: {lt['reason']}"
            )
        confirm_legacy = st.text_input(
            '삭제 확인: 아래에 **정리** 를 입력하세요',
            key="db_legacy_confirm",
        )
        if st.button("레거시 테이블 삭제", type="primary", key="db_legacy_drop"):
            if confirm_legacy.strip() == "정리":
                dropped = drop_legacy_tables(cfg)
                st.session_state["_admin_toast"] = f"삭제 완료: {', '.join(dropped)}"
                st.rerun()
            else:
                st.error("확인 문구가 일치하지 않습니다.")
    else:
        st.info("정리할 레거시 테이블이 없습니다.")

    st.divider()

    # ── 전체 데이터 초기화 ──
    st.subheader("전체 데이터 초기화")
    st.caption("모든 데이터 테이블의 레코드를 삭제합니다. 사용자 계정·설정은 유지됩니다.")

    with st.expander("삭제 대상 테이블 보기"):
        st.markdown(
            "runs, active_jobs, stress_test_runs/samples, mj_gallery, "
            "gpt_conversations, kling_web_history, elevenlabs_history, "
            "nanobanana_sessions, chat_messages, credit_usage_log, "
            "notices, maintenance_schedule"
        )
        st.warning("users, user_balance, user_sessions, admin_settings, api_keys, class_schedules 등 시스템 테이블은 유지됩니다.")

    confirm_reset = st.text_input(
        '초기화 확인: 아래에 **초기화** 를 입력하세요',
        key="db_reset_confirm",
    )
    if st.button("전체 데이터 초기화", type="primary", key="db_reset_btn"):
        if confirm_reset.strip() == "초기화":
            result = reset_all_data(cfg)
            total = sum(result.values())
            st.session_state["_admin_toast"] = f"초기화 완료 — 총 {total:,}건 삭제"
            for table, cnt in result.items():
                if cnt > 0:
                    st.write(f"  {table}: {cnt:,}건")
            st.rerun()
        else:
            st.error("확인 문구가 일치하지 않습니다.")


def render_admin_page(cfg: AppConfig):
    u = current_user()
    if not u or u.role != 'admin':
        st.error('관리자 권한이 필요합니다.')
        return

    # ── rerun 후 토스트 표시 ──
    _pending_toast = st.session_state.pop("_admin_toast", None)
    if _pending_toast:
        st.toast(_pending_toast, icon="✅")

    _MENU_GROUPS = [
        ("운영",   ["알림/점검", "문의 관리", "강의자료", "시간표 관리", "학교 정보"]),
        ("관리",   ["크레딧 관리", "키풀 관리", "계정 관리", "DB 관리"]),
        ("기록",   ["실행 기록", "부하테스트"]),
    ]

    if "_admin_active" not in st.session_state:
        st.session_state["_admin_active"] = "알림/점검"

    with st.sidebar:
        for group_name, items in _MENU_GROUPS:
            st.caption(group_name)
            for item in items:
                is_active = st.session_state["_admin_active"] == item
                if st.button(
                    item,
                    key=f"_am_{item}",
                    width="stretch",
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state["_admin_active"] = item
                    st.rerun()

    selected_label = st.session_state["_admin_active"]

    # --- 키풀 관리 ---
    if selected_label == "키풀 관리":
        _render_key_pool_summary(cfg, editable=True)

    # --- 실행 기록 ---
    elif selected_label == "실행 기록":
        user_rows = list_users(cfg, include_inactive=True)
        user_ids = ['(all)'] + [r['user_id'] for r in user_rows]
        sel_user = st.selectbox('필터: user_id', user_ids, index=0)
        limit = st.slider('표시 개수', 50, 500, 200, 50)

        filter_uid = None if sel_user == '(all)' else sel_user

        # ── GPT Conversations ──
        st.subheader('💬 GPT Conversations')
        gpt_items = list_gpt_conversations_admin(cfg, limit=limit, user_id=filter_uid)
        if gpt_items:
            import pandas as pd
            df = pd.DataFrame(gpt_items)
            df.insert(0, '보기', False)

            tbl_ver = st.session_state.get('_gpt_tbl_ver', 0)
            edited = st.data_editor(
                df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'gpt_conv_table_{tbl_ver}',
            )

            checked = edited.index[edited['보기'] == True].tolist()
            if checked:
                idx = checked[0]
                if idx < len(gpt_items):
                    st.session_state['_view_gpt_conv_id'] = gpt_items[idx]['id']
                    st.session_state['_open_gpt_detail'] = True
                    st.session_state['_gpt_tbl_ver'] = tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 GPT 대화가 없습니다.')

        _maybe_open_gpt_dialog(cfg)

        # ── Midjourney ──
        st.subheader('🎨 Midjourney')
        mj_rows = list_mj_gallery_admin(cfg, limit=limit, user_id=filter_uid)
        mj_items = _rows_to_dicts(mj_rows)
        if mj_items:
            import pandas as pd
            mj_df = pd.DataFrame(mj_items)
            mj_df.insert(0, '보기', False)

            mj_tbl_ver = st.session_state.get('_mj_tbl_ver', 0)
            mj_edited = st.data_editor(
                mj_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in mj_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'mj_table_{mj_tbl_ver}',
            )

            mj_checked = mj_edited.index[mj_edited['보기'] == True].tolist()
            if mj_checked:
                idx = mj_checked[0]
                if idx < len(mj_items):
                    st.session_state['_view_mj_row_id'] = mj_items[idx]['id']
                    st.session_state['_open_mj_detail'] = True
                    st.session_state['_mj_tbl_ver'] = mj_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 MJ 기록이 없습니다.')

        _maybe_open_mj_dialog(cfg)

        # ── NanoBanana Sessions (멀티턴) ──
        st.subheader('\U0001f34c NanoBanana Sessions')
        nb_sessions = list_nanobanana_sessions_admin(cfg, limit=limit, user_id=filter_uid)
        if nb_sessions:
            import pandas as pd
            nb_df = pd.DataFrame(nb_sessions)
            nb_df.insert(0, '보기', False)

            nb_tbl_ver = st.session_state.get('_nb_tbl_ver', 0)
            nb_edited = st.data_editor(
                nb_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in nb_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'nb_table_{nb_tbl_ver}',
            )

            nb_checked = nb_edited.index[nb_edited['보기'] == True].tolist()
            if nb_checked:
                idx = nb_checked[0]
                if idx < len(nb_sessions):
                    st.session_state['_view_nb_session_id'] = nb_sessions[idx]['id']
                    st.session_state['_open_nb_session_detail'] = True
                    st.session_state['_nb_tbl_ver'] = nb_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 NanoBanana 세션이 없습니다.')

        _maybe_open_nanobanana_session_dialog(cfg)

        # ── Kling Web ──
        st.subheader('🎬 Kling Web')
        kling_items = list_kling_web_admin(cfg, limit=limit, user_id=filter_uid)
        if kling_items:
            import pandas as pd
            kling_df = pd.DataFrame(kling_items)
            kling_df.insert(0, '보기', False)

            kling_tbl_ver = st.session_state.get('_kling_tbl_ver', 0)
            kling_edited = st.data_editor(
                kling_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in kling_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'kling_web_table_{kling_tbl_ver}',
            )

            kling_checked = kling_edited.index[kling_edited['보기'] == True].tolist()
            if kling_checked:
                idx = kling_checked[0]
                if idx < len(kling_items):
                    st.session_state['_view_kling_row_id'] = kling_items[idx]['id']
                    st.session_state['_open_kling_detail'] = True
                    st.session_state['_kling_tbl_ver'] = kling_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 Kling Web 기록이 없습니다.')

        _maybe_open_kling_dialog(cfg)

        # ── ElevenLabs TTS ──
        st.subheader('🔊 ElevenLabs TTS')
        el_items = list_elevenlabs_admin(cfg, limit=limit, user_id=filter_uid)
        if el_items:
            import pandas as pd
            el_df = pd.DataFrame(el_items)
            el_df.insert(0, '보기', False)

            el_tbl_ver = st.session_state.get('_el_tbl_ver', 0)
            el_edited = st.data_editor(
                el_df,
                column_config={
                    '보기': st.column_config.CheckboxColumn('👁', default=False, width='small'),
                    'id': None,
                },
                disabled=[c for c in el_df.columns if c != '보기'],
                hide_index=True,
                width='stretch',
                height=400,
                key=f'el_table_{el_tbl_ver}',
            )

            el_checked = el_edited.index[el_edited['보기'] == True].tolist()
            if el_checked:
                idx = el_checked[0]
                if idx < len(el_items):
                    st.session_state['_view_elevenlabs_row_id'] = el_items[idx]['id']
                    st.session_state['_open_elevenlabs_detail'] = True
                    st.session_state['_el_tbl_ver'] = el_tbl_ver + 1
                    st.rerun()
        else:
            st.info('표시할 ElevenLabs 기록이 없습니다.')

        _maybe_open_elevenlabs_dialog(cfg)

        # ── 향후 추가: Wisk 등 ──

    # --- 부하테스트 ---
    elif selected_label == "부하테스트":
        tab_algo, tab_burst, tab_real = st.tabs(["알고리즘 검증", "키 부하테스트", "실제 부하테스트"])
        with tab_algo:
            render_algorithm_test(cfg)
            st.divider()
            render_stress_test_results(cfg, test_mode="mock")
        with tab_burst:
            render_burst_test(cfg)
            st.divider()
            render_stress_test_results(cfg, test_mode="burst")
        with tab_real:
            render_stress_test_execution(cfg)
            st.divider()
            render_stress_test_results(cfg, test_mode="realistic")

    # --- 계정 관리 ---
    elif selected_label == "계정 관리":
        st.subheader('계정 목록')
        users = _rows_to_dicts(list_users(cfg, include_inactive=True))
        if users:
            import pandas as pd
            user_df = pd.DataFrame(users)

            filter_col1, filter_col2 = st.columns(2)
            with filter_col1:
                school_opts = ["전체"] + sorted(user_df['school_id'].dropna().unique().tolist())
                sel_school = st.selectbox(
                    "학교 필터",
                    school_opts,
                    key="_acct_school_filter",
                )
            with filter_col2:
                search_query = st.text_input("검색 (ID / 닉네임)", key="_acct_search", placeholder="검색어 입력...")

            filtered = user_df.copy()
            if sel_school != "전체":
                filtered = filtered[filtered['school_id'] == sel_school]
            if search_query:
                q = search_query.strip().lower()
                filtered = filtered[
                    filtered['user_id'].str.lower().str.contains(q, na=False)
                    | filtered['nickname'].astype(str).str.lower().str.contains(q, na=False)
                ]

            col_order = ['user_id', 'nickname', 'school_id', 'role', 'is_active', 'created_at', 'updated_at']
            col_order = [c for c in col_order if c in filtered.columns]
            remaining = [c for c in filtered.columns if c not in col_order and c not in ('password_hash', 'suno_account_id')]
            filtered = filtered[col_order + remaining]

            st.caption(f"{len(filtered)}명 표시 / 전체 {len(user_df)}명")
            st.dataframe(filtered, width="stretch", hide_index=True, height=400)
        else:
            st.warning('등록된 계정이 없습니다(부트스트랩 관리자만 있는 경우에도 여기에 보입니다).')

        st.markdown('---')

        st.subheader('계정 추가')
        tenant_ids = _list_tenant_ids(cfg)
        with st.form('create_user'):
            new_user_id = st.text_input('User ID')
            new_pw = st.text_input('Password', type='password')
            new_nickname = st.text_input('닉네임 (선택)')
            new_role = st.selectbox('Role', ['student', 'teacher', 'viewer', 'admin'], index=0)
            new_school_id = st.selectbox(
                'School ID',
                tenant_ids,
                index=tenant_ids.index('default') if 'default' in tenant_ids else 0,
                format_func=lambda tid: f"{cfg.get_layout(tid)}  ({tid})",
            )
            submitted = st.form_submit_button('추가')

        if submitted:
            new_user_id = (new_user_id or '').strip()
            if not new_user_id or not new_pw:
                st.error('User ID와 Password는 필수입니다.')
            else:
                upsert_user(cfg, user_id=new_user_id, password_hash=hash_password(new_pw), role=new_role, school_id=new_school_id, is_active=1, nickname=(new_nickname or '').strip())
                init_user_balance_from_default(cfg, new_user_id)
                log_admin_action(cfg, u.user_id, "create_user", new_user_id, f"role={new_role}, school={new_school_id}")
                st.session_state["_admin_toast"] = '계정이 추가/갱신되었습니다.'
                st.rerun()

        st.markdown('---')

        st.subheader('CSV 일괄 등록')
        st.caption('CSV 형식: 첫 번째 행은 헤더(user_id, password, nickname), 이후 행에 데이터를 입력하세요.')
        st.caption('학교 컬럼이 포함된 CSV(Google Forms 등)는 학교가 자동 지정됩니다.')
        tenant_ids_csv = _list_tenant_ids(cfg)
        # 학교 이름(layout) → tenant_id 매핑 생성
        _school_name_to_id: dict[str, str] = {}
        for _tid in tenant_ids_csv:
            _layout = cfg.get_layout(_tid)
            _school_name_to_id[_layout] = _tid
            # 부분 매칭용: "홍익대" → "hongik" 등
            if _layout.endswith('학교'):
                _school_name_to_id[_layout[:-1]] = _tid  # 홍익대학 → hongik
            if len(_layout) >= 3:
                _school_name_to_id[_layout[:3]] = _tid  # 홍익대 → hongik

        with st.form('csv_bulk_create'):
            csv_file = st.file_uploader('CSV 파일 업로드', type=['csv'], key='csv_bulk_file')
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                csv_role = st.selectbox('역할', ['student', 'teacher'], index=0, key='csv_role')
                csv_school = st.selectbox(
                    'School ID (학교 컬럼 없을 때 사용)',
                    tenant_ids_csv,
                    index=tenant_ids_csv.index('default') if 'default' in tenant_ids_csv else 0,
                    format_func=lambda tid: f"{cfg.get_layout(tid)}  ({tid})",
                    key='csv_school',
                )
            with col_c2:
                csv_credits = st.number_input('초기 크레딧', min_value=0, value=100, step=10, key='csv_credits')
            csv_submitted = st.form_submit_button('일괄 등록')

        if csv_submitted:
            if not csv_file:
                st.error('CSV 파일을 업로드해주세요.')
            else:
                import csv as _csv
                import io
                try:
                    content = csv_file.read().decode('utf-8-sig')
                    reader = _csv.reader(io.StringIO(content))
                    header = next(reader, None)
                    if not header or len(header) < 3:
                        raise ValueError('CSV 헤더가 올바르지 않습니다. 최소 3개 컬럼이 필요합니다.')
                    # 컬럼 매핑: 헤더명 기반 또는 순서 기반 자동 감지
                    _h_lower = [h.strip().lower() for h in header]
                    _idx_school = -1  # 학교 컬럼 인덱스
                    if 'user_id' in _h_lower:
                        # 표준 헤더: user_id, password, nickname [, school]
                        _idx_uid = _h_lower.index('user_id')
                        _idx_pw = _h_lower.index('password') if 'password' in _h_lower else 1
                        _idx_nick = _h_lower.index('nickname') if 'nickname' in _h_lower else -1
                        if 'school' in _h_lower:
                            _idx_school = _h_lower.index('school')
                    else:
                        # Google Forms 비표준 자동 감지
                        if len(header) >= 6:
                            # 타임스탬프, ID, 학교, PW, PW확인, 닉네임
                            _idx_uid = 1
                            _idx_school = 2
                            _idx_pw = 3
                            _idx_nick = 5
                        elif len(header) >= 5:
                            # 기존: 타임스탬프, ID, PW, PW확인, 닉네임
                            _idx_uid = 1
                            _idx_pw = 2
                            _idx_nick = 4
                        else:
                            _idx_uid = 0
                            _idx_pw = 1
                            _idx_nick = 2 if len(header) >= 3 else -1

                    _auto_school = _idx_school >= 0
                    if _auto_school:
                        st.info('📋 학교 컬럼이 감지되었습니다. CSV의 학교 값으로 자동 지정됩니다.')

                    created, skipped, errors = 0, 0, []
                    _school_counts: dict[str, int] = {}
                    for i, row in enumerate(reader, start=2):
                        uid = (row[_idx_uid] if _idx_uid < len(row) else '').strip()
                        pw = (row[_idx_pw] if _idx_pw < len(row) else '').strip()
                        nick = (row[_idx_nick] if 0 <= _idx_nick < len(row) else '').strip()
                        if not uid or not pw:
                            skipped += 1
                            errors.append(f'{i}행: user_id 또는 password 누락')
                            continue

                        # 학교 결정: CSV 컬럼 우선, 없으면 폼 선택값 사용
                        if _auto_school:
                            raw_school = (row[_idx_school] if _idx_school < len(row) else '').strip()
                            resolved = _school_name_to_id.get(raw_school)
                            if resolved is None:
                                # tenant_id 직접 입력도 허용
                                resolved = raw_school if raw_school in tenant_ids_csv else None
                            if resolved is None:
                                skipped += 1
                                errors.append(f'{i}행: 알 수 없는 학교 "{raw_school}"')
                                continue
                            row_school = resolved
                        else:
                            row_school = csv_school

                        upsert_user(
                            cfg, user_id=uid,
                            password_hash=hash_password(pw),
                            role=csv_role, school_id=row_school,
                            is_active=1, nickname=nick,
                        )
                        if csv_credits > 0:
                            set_user_balance(cfg, uid, csv_credits)
                        else:
                            init_user_balance_from_default(cfg, uid)
                        created += 1
                        _school_counts[row_school] = _school_counts.get(row_school, 0) + 1

                    _school_detail = ', '.join(f'{cfg.get_layout(s)}({cnt}명)' for s, cnt in sorted(_school_counts.items()))
                    _log_school = _school_detail if _auto_school else csv_school
                    log_admin_action(cfg, u.user_id, "csv_bulk_create", f"{created}명", f"role={csv_role}, school={_log_school}, credits={csv_credits}")
                    _toast = f'{created}명 등록 완료' + (f', {skipped}명 스킵' if skipped else '')
                    if _auto_school and _school_counts:
                        _toast += f' | {_school_detail}'
                    st.session_state["_admin_toast"] = _toast
                    if errors:
                        st.warning('\n'.join(errors))
                    st.rerun()
                except Exception as e:
                    st.error(f'CSV 처리 오류: {e}')

        st.markdown('---')

        st.subheader('계정 수정')
        if users:
            ids = [x['user_id'] for x in users]
            _nick_map = {x['user_id']: x.get('nickname', '') for x in users}
            target = st.selectbox(
                '대상 계정', ids, key='edit_target',
                format_func=lambda uid: f"{uid} ({_nick_map[uid]})" if _nick_map.get(uid) else uid,
            )
            target_row = next((x for x in users if x['user_id'] == target), {})
            is_self = (target == u.user_id)

            with st.form('edit_user_form'):
                col1, col2 = st.columns(2)
                with col1:
                    cur_role = target_row.get('role', 'user')
                    role_opts = ['student', 'teacher', 'viewer', 'admin']
                    new_role = st.selectbox(
                        'Role',
                        role_opts,
                        index=role_opts.index(cur_role) if cur_role in role_opts else 0,
                    )
                with col2:
                    cur_school = target_row.get('school_id', 'default')
                    new_school = st.selectbox(
                        'School ID',
                        tenant_ids,
                        index=tenant_ids.index(cur_school) if cur_school in tenant_ids else 0,
                        format_func=lambda tid: f"{cfg.get_layout(tid)}  ({tid})",
                    )

                cur_nickname = target_row.get('nickname', '') or ''
                new_nickname = st.text_input('닉네임', value=cur_nickname, key=f'nick_{target}')

                col3, col4 = st.columns(2)
                with col3:
                    cur_active = bool(target_row.get('is_active', 1))
                    new_active = st.toggle('활성 상태', value=cur_active)
                with col4:
                    new_pw2 = st.text_input('새 비밀번호 (변경 시에만 입력)', type='password', key=f'reset_pw_{target}')

                # Suno 계정 배정
                suno_accounts = cfg.get_suno_accounts()
                suno_opts = {0: '0 - 배정 없음'}
                for acc in suno_accounts:
                    aid = acc.get('id', 0)
                    if aid != 0:
                        label = f"{aid} - {acc.get('email', '?')}"
                        if acc.get('memo'):
                            label += f" ({acc['memo']})"
                        suno_opts[aid] = label
                suno_ids = list(suno_opts.keys())
                cur_suno = int(target_row.get('suno_account_id', 0) or 0)
                new_suno = st.selectbox(
                    'Suno 계정 배정',
                    suno_ids,
                    index=suno_ids.index(cur_suno) if cur_suno in suno_ids else 0,
                    format_func=lambda x: suno_opts[x],
                )

                # 크레딧 잔액
                st.markdown("**크레딧 잔액**")
                _cur_balance = get_user_balance(cfg, target)
                _new_balance = st.number_input(
                    "크레딧",
                    min_value=0, max_value=999999,
                    value=_cur_balance,
                    step=1, key=f"credit_{target}",
                )

                submitted_edit = st.form_submit_button('변경 사항 저장', width="stretch")

            if submitted_edit:
                changes = []

                # 크레딧 변경
                if _new_balance != _cur_balance:
                    set_user_balance(cfg, target, _new_balance)
                    changes.append(f'크레딧: {_cur_balance} → {_new_balance}')

                # Role 변경
                if new_role != cur_role:
                    if is_self and new_role != 'admin':
                        st.error('본인의 admin 권한은 해제할 수 없습니다.')
                    else:
                        update_user_fields(cfg, target, role=new_role)
                        changes.append(f'Role: {cur_role} → {new_role}')

                # School ID 변경
                if new_school != cur_school:
                    update_user_fields(cfg, target, school_id=new_school)
                    changes.append(f'School: {cur_school} → {new_school}')

                # 활성 상태 변경
                if new_active != cur_active:
                    if is_self:
                        st.error('본인 계정의 활성 상태는 변경할 수 없습니다.')
                    else:
                        set_user_active(cfg, target, new_active)
                        changes.append(f'활성: {"ON" if new_active else "OFF"}')

                # Suno 배정 변경
                if new_suno != cur_suno:
                    update_user_fields(cfg, target, suno_account_id=new_suno)
                    changes.append(f'Suno: #{cur_suno} → #{new_suno}')

                # 닉네임 변경
                _new_nick = (new_nickname or '').strip()
                if _new_nick != cur_nickname:
                    update_user_fields(cfg, target, nickname=_new_nick)
                    changes.append(f'닉네임: "{cur_nickname}" → "{_new_nick}"')

                # 비밀번호 변경
                if new_pw2:
                    set_user_password(cfg, target, hash_password(new_pw2))
                    changes.append('비밀번호 재설정')

                if changes:
                    log_admin_action(cfg, u.user_id, "modify_user", target, "; ".join(changes))
                    st.session_state["_admin_toast"] = '변경 완료: ' + ', '.join(changes)
                    st.rerun()
                elif not any(st.session_state.get(f'_edit_err_{i}') for i in range(4)):
                    st.info('변경된 항목이 없습니다.')

            st.markdown('---')
            st.subheader('계정 삭제(하드 삭제)')
            st.warning('삭제는 되돌릴 수 없습니다. 기본적으로는 비활성화를 권장합니다.')
            confirm = st.text_input(f'삭제 확인: **{target}** 을 그대로 입력하세요', key=f'del_confirm_{target}')
            if st.button('하드 삭제 실행'):
                if is_self:
                    st.error('본인 계정은 삭제할 수 없습니다.')
                elif confirm.strip() != target:
                    st.error('확인 문구가 일치하지 않습니다.')
                else:
                    hard_delete_user(cfg, target)
                    st.session_state["_admin_toast"] = '삭제되었습니다.'
                    st.rerun()

    # --- DB 관리 ---
    elif selected_label == "DB 관리":
        _render_db_management(cfg)

    # --- 크레딧 관리 ---
    elif selected_label == "크레딧 관리":
        st.subheader("학교별 크레딧 현황")
        report_days = st.selectbox("기간", [7, 14, 30, 60, 90], index=2, format_func=lambda d: f"최근 {d}일", key="report_days")
        report = get_school_credit_report(cfg, days=report_days)
        if report:
            import pandas as pd
            rows = []
            for r in report:
                row = {"학교": r["school_id"], "잔여 크레딧": r["remaining"], "사용자 수": r["user_count"]}
                for fid in FEATURE_IDS:
                    row[FEATURE_LABELS.get(fid, fid)] = r["used_by_tab"].get(fid, 0)
                rows.append(row)
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch", hide_index=True)
        else:
            st.info("아직 크레딧 사용 내역이 없습니다.")

        st.markdown("---")

        st.subheader("학생별 크레딧 현황")
        sc1, sc2 = st.columns(2)
        with sc1:
            student_days = st.selectbox(
                "기간", [7, 14, 30, 60, 90], index=2,
                format_func=lambda d: f"최근 {d}일",
                key="student_report_days",
            )
        with sc2:
            school_opts = ["전체"] + _list_tenant_ids(cfg)
            student_school = st.selectbox(
                "학교",
                school_opts,
                format_func=lambda x: x if x == "전체" else f"{cfg.get_layout(x)} ({x})",
                key="student_report_school",
            )
        student_report = get_student_credit_report(
            cfg,
            school_id=None if student_school == "전체" else student_school,
            days=student_days,
        )
        if student_report:
            import pandas as pd
            s_rows = []
            for r in student_report:
                s_row = {
                    "학교": r["school_id"],
                    "사용자": r["user_id"],
                    "역할": r["role"],
                    "잔여 크레딧": r["remaining"],
                }
                total_used = 0
                for fid in FEATURE_IDS:
                    used = r["used_by_tab"].get(fid, 0)
                    s_row[FEATURE_LABELS.get(fid, fid)] = used
                    total_used += used
                s_row["합계 사용"] = total_used
                s_rows.append(s_row)
            s_df = pd.DataFrame(s_rows)
            st.dataframe(s_df, width="stretch", hide_index=True)
        else:
            st.info("해당 조건에 맞는 사용자가 없습니다.")

        st.markdown("---")
        _render_api_cost_estimation(cfg, editable=True, key_prefix="a_")

        st.markdown("---")

        st.subheader("기능별 단위 비용")
        st.caption("0 = 무제한 (크레딧 차감 없음). 영상 기능은 '초당' 비용입니다.")
        with st.form("credit_cost_form"):
            new_costs = {}
            cc1, cc2, cc3 = st.columns(3)
            for i, fid in enumerate(FEATURE_IDS):
                with [cc1, cc2, cc3][i % 3]:
                    cur_val = get_feature_cost(cfg, fid)
                    new_costs[fid] = st.number_input(
                        f"{FEATURE_LABELS.get(fid, fid)} (/{FEATURE_UNITS.get(fid, '회')})",
                        min_value=0, max_value=9999,
                        value=cur_val,
                        step=1, key=f"cost_{fid}",
                    )
            if st.form_submit_button("비용 저장", width="stretch"):
                for fid, val in new_costs.items():
                    set_admin_setting(cfg, f"credit_cost.{fid}", str(val))
                st.session_state["_admin_toast"] = "저장되었습니다."
                st.rerun()

        st.markdown("---")

        st.subheader("신규 계정 기본 크레딧")
        st.caption("계정 추가 시 자동으로 부여되는 초기 크레딧입니다.")
        cur_default = get_admin_setting(cfg, "credit_default", "0")
        with st.form("credit_default_form"):
            new_default = st.number_input(
                "기본 크레딧",
                min_value=0, max_value=999999,
                value=int(cur_default) if cur_default.isdigit() else 0,
                step=10, key="default_credit",
            )
            if st.form_submit_button("기본값 저장", width="stretch"):
                set_admin_setting(cfg, "credit_default", str(new_default))
                st.session_state["_admin_toast"] = "저장되었습니다."
                st.rerun()

        st.markdown("---")

        st.subheader("일괄 크레딧 추가")
        st.caption("선택한 대상의 기존 잔액에 입력값을 더합니다 (덮어쓰기 아님).")
        with st.form("credit_bulk_form"):
            bc1, bc2 = st.columns(2)
            with bc1:
                role_choice = st.selectbox(
                    "대상",
                    ["student", "teacher", "student,teacher"],
                    format_func=lambda x: {"student": "전체 학생", "teacher": "전체 교사", "student,teacher": "전체 (학생+교사)"}[x],
                    key="bulk_role",
                )
            with bc2:
                school_opts = ["all"] + _list_tenant_ids(cfg)
                school_choice = st.selectbox(
                    "학교",
                    school_opts,
                    format_func=lambda x: "전체" if x == "all" else x,
                    key="bulk_school",
                )
            bulk_amount = st.number_input(
                "추가 크레딧",
                min_value=0, max_value=999999,
                value=0, step=10, key="bulk_amount",
            )
            if st.form_submit_button("추가 실행", width="stretch"):
                affected = add_balance_bulk(cfg, role_choice, school_choice, bulk_amount)
                if affected > 0:
                    st.session_state["_admin_toast"] = f"{affected}명에게 {bulk_amount} 크레딧이 추가되었습니다."
                else:
                    st.warning("대상 사용자가 없거나 추가할 크레딧이 없습니다.")

        st.markdown("---")

        st.subheader("자동 크레딧 충전")
        st.caption("매월 지정일에 전체 학생·교사 계정에 크레딧을 자동 추가합니다. 0일로 설정하면 비활성화됩니다.")
        cur_refill = get_all_admin_settings(cfg, prefix="credit_refill")
        with st.form("credit_refill_form"):
            cur_day = cur_refill.get("credit_refill_day", "0")
            refill_day = st.number_input(
                "충전 일 (0 = 비활성)",
                min_value=0, max_value=28,
                value=int(cur_day) if cur_day.isdigit() else 0,
                step=1, key="refill_day",
            )
            last_refill = cur_refill.get("credit_refill_last", "없음")
            st.caption(f"마지막 자동 충전: {last_refill}")

            cur_refill_amount = cur_refill.get("credit_refill_amount", "0")
            refill_amount = st.number_input(
                "충전 크레딧",
                min_value=0, max_value=999999,
                value=int(cur_refill_amount) if cur_refill_amount.isdigit() else 0,
                step=10, key="refill_amount",
            )
            if st.form_submit_button("자동 충전 설정 저장", width="stretch"):
                set_admin_setting(cfg, "credit_refill_day", str(refill_day))
                set_admin_setting(cfg, "credit_refill_amount", str(refill_amount))
                st.session_state["_admin_toast"] = "저장되었습니다."
                st.rerun()

    # --- 강의자료 ---
    elif selected_label == "강의자료":
        tenant_ids = _list_tenant_ids(cfg)

        st.subheader("강의자료 (Google Drive)")
        sel_school = st.selectbox(
            "학교 선택", tenant_ids, key="lm_school_select",
            format_func=lambda tid: f"{cfg.get_layout(tid)}  ({tid})",
        )

        # ── Drive 폴더 설정 ──
        current_folder = get_admin_setting(cfg, f"drive_folder.{sel_school}", "")

        with st.expander("Drive 폴더 설정", expanded=not current_folder):
            new_folder = st.text_input(
                "Drive 폴더 ID 또는 URL",
                value=current_folder,
                key=f"lm_folder_id_{sel_school}",
                placeholder="URL 또는 폴더 ID를 붙여넣으세요",
            )
            st.caption(
                "Drive 폴더를 **'링크가 있는 모든 사용자에게 공개'**로 설정해야 합니다.\n\n"
                "파일 추가/삭제는 Google Drive에서 직접 관리합니다."
            )
            if st.button("폴더 ID 저장", key="lm_save_folder"):
                folder_val = extract_folder_id(new_folder)
                set_admin_setting(cfg, f"drive_folder.{sel_school}", folder_val)
                st.session_state["_admin_toast"] = "폴더 ID가 저장되었습니다."
                st.rerun()

        folder_id = get_admin_setting(cfg, f"drive_folder.{sel_school}", "")
        if not folder_id:
            st.info("Drive 폴더 ID를 먼저 설정해주세요.")
        else:
            # ── 폴더 미리보기 + Drive에서 열기 ──
            st.markdown("---")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader("폴더 미리보기")
            with col2:
                st.link_button(
                    "Drive에서 열기",
                    f"https://drive.google.com/drive/folders/{folder_id}",
                    width="stretch",
                )
            st.markdown(
                f'<div style="border-radius:12px;overflow:hidden;border:1px solid #3d3d5c;">'
                f'<iframe src="https://drive.google.com/embeddedfolderview?id={folder_id}#list"'
                f' style="width:100%;height:400px;border:none;background:#fff;"></iframe>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # --- 시간표 관리 ---
    elif selected_label == "시간표 관리":
        _render_timetable_admin(cfg)

    # --- 학교 정보 ---
    elif selected_label == "학교 정보":
        _render_school_info(cfg)

    # --- 알림/점검 ---
    elif selected_label == "알림/점검":
        _render_notice_and_maintenance(cfg)

    # --- 문의 관리 ---
    elif selected_label == "문의 관리":
        _render_support_management(cfg)


# ── 문의 관리 ──────────────────────────────────────────────

def _render_support_management(cfg: AppConfig):
    from core.db import (
        list_support_tickets_admin,
        reply_support_ticket,
        delete_support_ticket,
    )
    from datetime import datetime, timedelta, timezone as _tz
    _KST = _tz(timedelta(hours=9))

    st.header("📮 문의 관리")

    _filter = st.radio(
        "상태 필터",
        options=["전체", "대기 중", "답변 완료"],
        horizontal=True,
        key="_admin_support_filter",
    )
    status_map = {"전체": "", "대기 중": "open", "답변 완료": "answered"}
    tickets = list_support_tickets_admin(cfg, status=status_map[_filter], limit=200)

    if not tickets:
        st.info("문의 내역이 없습니다.")
        return

    _me = current_user()
    admin_uid = _me.user_id if _me else ""

    for t in tickets:
        try:
            _dt = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).astimezone(_KST).strftime("%m/%d %H:%M")
        except Exception:
            _dt = t.get("created_at", "")
        has_reply = bool(t.get("reply"))
        badge = "✅" if has_reply else "⏳"
        user_label = f"{t['user_id']}"
        if t.get("school_id"):
            user_label += f" ({t['school_id']})"
        title = f"{badge} [{user_label}] {t['subject']} · {_dt}"
        with st.expander(title, expanded=not has_reply):
            st.markdown(f"**문의 내용**  \n{t['message']}")

            if has_reply:
                try:
                    _rdt = datetime.fromisoformat(t["reply_at"].replace("Z", "+00:00")).astimezone(_KST).strftime("%m/%d %H:%M")
                except Exception:
                    _rdt = t.get("reply_at", "")
                st.markdown(f"---\n**답변** ({t.get('reply_by','')}, {_rdt})  \n{t['reply']}")

            st.markdown("---")
            new_reply = st.text_area(
                "답변 작성" if not has_reply else "답변 수정",
                value=t.get("reply") or "",
                key=f"_adm_reply_{t['ticket_id']}",
                height=100,
                max_chars=5000,
            )
            cols = st.columns([3, 1])
            with cols[0]:
                if st.button("답변 저장", key=f"_adm_reply_save_{t['ticket_id']}", type="primary"):
                    if not new_reply.strip():
                        st.error("답변 내용을 입력해주세요.")
                    else:
                        try:
                            reply_support_ticket(cfg, int(t["ticket_id"]), new_reply, admin_uid)
                            st.success("답변이 저장되었습니다.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"저장 실패: {e}")
            with cols[1]:
                if st.button("삭제", key=f"_adm_reply_del_{t['ticket_id']}", help="부적절한 문의 삭제"):
                    try:
                        delete_support_ticket(cfg, int(t["ticket_id"]))
                        st.rerun()
                    except Exception as e:
                        st.error(f"삭제 실패: {e}")


# ── 알림/점검 관리 ──────────────────────────────────────────────

def _render_notice_and_maintenance(cfg: AppConfig):
    from core.db import (
        create_notice, list_notices, deactivate_notice,
        schedule_maintenance, get_upcoming_maintenance, cancel_maintenance,
        reactivate_all_users,
    )
    from core.maintenance import check_maintenance, complete_maintenance

    tab_notice, tab_maint = st.tabs(["📢 알림 관리", "🔧 서버 점검"])

    # ── 알림 관리 ──
    with tab_notice:
        st.subheader("새 알림 보내기")
        with st.form("notice_form", clear_on_submit=True):
            msg = st.text_area("알림 메시지", placeholder="사용자에게 보여줄 메시지를 입력하세요")
            col1, col2 = st.columns(2)
            with col1:
                target = st.text_input("대상 학교 (비우면 전체)", placeholder="aimz, mokwon 등")
            with col2:
                hours = st.number_input("자동 만료 (시간, 0=수동)", min_value=0, value=0, step=1)
            submitted = st.form_submit_button("알림 보내기")
            if submitted and msg.strip():
                exp = None
                if hours > 0:
                    from datetime import datetime, timedelta, timezone
                    exp = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
                tgt = target.strip() if target.strip() else None
                create_notice(cfg, msg.strip(), target_school=tgt, expires_at=exp)
                st.session_state["_admin_toast"] = "알림이 전송되었습니다."
                st.rerun()

        st.divider()
        st.subheader("현재 알림")
        notices = list_notices(cfg, active_only=True)
        if notices:
            n = notices[0]
            scope = n.get("target_school") or "전체"
            st.markdown(f"📢 **{n['message']}** · 대상: {scope} · {n.get('created_at', '')[:16]}")
            if st.button("알림 끄기", key=f"notice_off_{n['notice_id']}"):
                deactivate_notice(cfg, n["notice_id"])
                st.rerun()
        else:
            st.info("현재 활성 알림이 없습니다.")

    # ── 서버 점검 ──
    with tab_maint:
        maint = check_maintenance(cfg)
        upcoming = get_upcoming_maintenance(cfg)

        if maint.is_maintenance_active:
            st.error("🔴 현재 서버 점검 중입니다.")
            st.markdown(f"**메시지**: {maint.message}")
            if st.button("✅ 점검 완료 — 서비스 재개", type="primary"):
                complete_maintenance(cfg, maint.maintenance_id)
                st.session_state["_admin_toast"] = "서비스가 재개되었습니다. 모든 사용자가 재활성화됩니다."
                st.rerun()
        elif upcoming and upcoming["status"] == "scheduled":
            st.warning(f"⏰ 점검 예정: **{upcoming['scheduled_at'][:16]}** (KST)")
            st.markdown(f"메시지: {upcoming['message']}")
            if maint.is_warning_period:
                st.info(f"경고 기간 진입 — 사용자에게 **{maint.minutes_remaining}분** 남았다는 배너가 표시 중")
            if st.button("❌ 점검 취소"):
                cancel_maintenance(cfg, upcoming["id"])
                st.session_state["_admin_toast"] = "점검이 취소되었습니다."
                st.rerun()
        else:
            st.info("예정된 서버 점검이 없습니다.")

        st.divider()
        st.subheader("새 점검 예약")
        with st.form("maint_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                maint_date = st.date_input("점검 날짜")
            with col2:
                from datetime import time as _time
                maint_time = st.time_input("점검 시각 (KST)", value=_time(0, 0))
            maint_msg = st.text_input("점검 메시지", value="서버 점검이 예정되어 있습니다. 작업을 저장해 주세요.")
            if st.form_submit_button("점검 예약"):
                from datetime import datetime
                dt = datetime.combine(maint_date, maint_time)
                scheduled_at = dt.isoformat().replace("+00:00", "Z")
                schedule_maintenance(cfg, scheduled_at, maint_msg)
                st.session_state["_admin_toast"] = f"점검이 {scheduled_at[:16]} (KST)에 예약되었습니다."
                st.rerun()


# ── 시간표 관리 ──────────────────────────────────────────────

_DAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]

_SCHEDULE_COLORS = [
    "#f87171", "#fb923c", "#facc15", "#4ade80", "#60a5fa",
    "#a78bfa", "#f472b6", "#2dd4bf", "#fbbf24", "#818cf8",
]


def _render_timetable_admin(cfg: AppConfig):
    tenant_ids = _list_tenant_ids(cfg)
    schedules = list_class_schedules(cfg)

    st.subheader("수업 시간표 관리")
    st.caption(
        "수업 시간에는 해당 학교 학생만 전체 탭을 사용할 수 있고, "
        "다른 학교 학생은 갤러리 탭만 이용 가능합니다."
    )

    # ── 시간표 시각화 (주간 그리드) ──
    _render_timetable_grid(cfg, schedules, tenant_ids)

    st.markdown("---")

    # ── 수업 추가 ──
    with st.expander("➕ 수업 추가", expanded=False):
        _render_schedule_add_form(cfg, tenant_ids)

    # ── 기존 수업 목록 ──
    st.markdown("---")
    st.subheader("등록된 수업 목록")

    if not schedules:
        st.info("등록된 수업이 없습니다.")
    else:
        for s in schedules:
            _render_schedule_row(cfg, s, tenant_ids)


def _render_timetable_grid(cfg: AppConfig, schedules: list, tenant_ids: list):
    """주간 시간표 그리드를 HTML로 시각화. 항상 고정된 그리드를 표시."""

    # 고정 시간 범위: 9시~22시, 월~금 (스케줄이 범위 밖이면 확장)
    fixed_start = 9
    fixed_end = 22
    show_days = 5  # 월~금 (토/일에 수업 있으면 7로 확장)

    if schedules:
        min_hour = min(s["start_hour"] for s in schedules)
        max_hour = max(s["end_hour"] for s in schedules)
        fixed_start = min(fixed_start, min_hour)
        fixed_end = max(fixed_end, max_hour)
        if any(s["day_of_week"] >= 5 for s in schedules):
            show_days = 7

    # 학교별 색상 매핑
    school_colors = {}
    for i, tid in enumerate(tenant_ids):
        school_colors[tid] = _SCHEDULE_COLORS[i % len(_SCHEDULE_COLORS)]

    # 요일-시간 → 스케줄 매핑 (빠른 조회용)
    grid_map = {}  # (dow, hour) → schedule (start_hour == hour인 경우만)
    covered_set = set()  # (dow, hour) — rowspan으로 커버되는 셀
    for s in schedules:
        dow = s["day_of_week"]
        if dow >= show_days:
            continue
        for h in range(s["start_hour"], s["end_hour"]):
            if h == s["start_hour"]:
                grid_map[(dow, h)] = s
            else:
                covered_set.add((dow, h))

    # 테마 대응 CSS 변수
    border_color = "#ddd"
    border_color_dark = "#444"
    time_color = "#888"

    html_parts = [
        '<style>',
        '.tt-grid{overflow-x:auto}',
        '.tt-grid table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}',
        f'.tt-grid th,.tt-grid td{{padding:4px 6px;border:1px solid {border_color_dark};text-align:center}}',
        '.tt-grid th{font-weight:600;font-size:13px}',
        f'.tt-grid .tt-time{{font-size:11px;color:{time_color};vertical-align:top;width:50px}}',
        '.tt-grid .tt-empty{background:transparent}',
        '@media(prefers-color-scheme:light){',
        f'  .tt-grid th,.tt-grid td{{border-color:{border_color}}}',
        '}',
        '</style>',
        '<div class="tt-grid"><table>',
    ]

    # 헤더
    html_parts.append('<tr><th class="tt-time">시간</th>')
    for d in range(show_days):
        html_parts.append(f'<th>{_DAY_LABELS[d]}</th>')
    html_parts.append('</tr>')

    # 각 시간대 행
    for hour in range(fixed_start, fixed_end):
        html_parts.append(f'<tr><td class="tt-time">{hour:02d}:00</td>')
        for dow in range(show_days):
            if (dow, hour) in covered_set:
                # rowspan으로 이미 커버된 셀 → td 생략
                continue

            sched = grid_map.get((dow, hour))
            if sched:
                color = school_colors.get(sched["school_id"], "#6366f1")
                label = sched.get("label", "")
                school_name = cfg.get_layout(sched["school_id"])
                start_str = f'{sched["start_hour"]:02d}:{sched["start_minute"]:02d}'
                end_str = f'{sched["end_hour"]:02d}:{sched["end_minute"]:02d}'
                duration = sched["end_hour"] - sched["start_hour"]
                rowspan = max(1, duration)
                html_parts.append(
                    f'<td rowspan="{rowspan}" style="vertical-align:top;text-align:left;'
                    f'background:{color}22;border-left:3px solid {color};">'
                    f'<div style="font-weight:600;color:{color};font-size:12px;">{label}</div>'
                    f'<div style="font-size:10px;color:#999;margin-top:2px;">{school_name}</div>'
                    f'<div style="font-size:10px;color:#999;">{start_str}-{end_str}</div>'
                    f'</td>'
                )
            else:
                html_parts.append('<td class="tt-empty">&nbsp;</td>')

        html_parts.append('</tr>')

    html_parts.append('</table>')

    # 범례 (학교가 있을 때만)
    if tenant_ids:
        html_parts.append('<div style="margin-top:8px;display:flex;gap:12px;flex-wrap:wrap;">')
        for tid in tenant_ids:
            color = school_colors.get(tid, "#6366f1")
            name = cfg.get_layout(tid)
            html_parts.append(
                f'<span style="display:flex;align-items:center;gap:4px;font-size:12px;">'
                f'<span style="width:12px;height:12px;border-radius:3px;background:{color};display:inline-block;"></span>'
                f'{name}</span>'
            )
        html_parts.append('</div>')

    html_parts.append('</div>')

    st.markdown(''.join(html_parts), unsafe_allow_html=True)


def _render_schedule_add_form(cfg: AppConfig, tenant_ids: list):
    """수업 추가 폼."""
    col1, col2 = st.columns(2)
    with col1:
        school_id = st.selectbox(
            "학교", tenant_ids, key="sched_add_school",
            format_func=lambda tid: f"{cfg.get_layout(tid)} ({tid})",
        )
        day_of_week = st.selectbox(
            "요일", list(range(7)), key="sched_add_dow",
            format_func=lambda i: _DAY_LABELS[i],
        )
        label = st.text_input("수업명", key="sched_add_label", placeholder="예: 광고학-전공")
    with col2:
        start_hour = st.number_input("시작 시", 0, 23, 9, key="sched_add_sh")
        start_minute = st.selectbox("시작 분", [0, 10, 15, 20, 30, 40, 45, 50], key="sched_add_sm")
        end_hour = st.number_input("종료 시", 0, 23, 10, key="sched_add_eh")
        end_minute = st.selectbox("종료 분", [0, 10, 15, 20, 30, 40, 45, 50], key="sched_add_em")

    if st.button("수업 추가", key="sched_add_btn", type="primary"):
        if end_hour * 60 + end_minute <= start_hour * 60 + start_minute:
            st.error("종료 시간이 시작 시간보다 빠릅니다.")
        elif not label.strip():
            st.error("수업명을 입력해주세요.")
        else:
            insert_class_schedule(cfg, {
                "school_id": school_id,
                "day_of_week": day_of_week,
                "start_hour": start_hour,
                "start_minute": start_minute,
                "end_hour": end_hour,
                "end_minute": end_minute,
                "label": label.strip(),
                "color": "",
            })
            st.session_state["_admin_toast"] = f"수업 '{label}' 추가 완료"
            st.rerun()


def _school_color(tenant_ids: list, school_id: str) -> str:
    """학교 ID에 대한 자동 색상 반환."""
    try:
        idx = tenant_ids.index(school_id)
    except ValueError:
        idx = 0
    return _SCHEDULE_COLORS[idx % len(_SCHEDULE_COLORS)]


def _render_schedule_row(cfg: AppConfig, s: dict, tenant_ids: list):
    """개별 수업 행: 수정/삭제 UI."""
    sid = s["id"]
    school_name = cfg.get_layout(s["school_id"])
    day_label = _DAY_LABELS[s["day_of_week"]] if 0 <= s["day_of_week"] < 7 else "?"
    start_str = f'{s["start_hour"]:02d}:{s["start_minute"]:02d}'
    end_str = f'{s["end_hour"]:02d}:{s["end_minute"]:02d}'
    color = _school_color(tenant_ids, s["school_id"])

    col_info, col_edit, col_del = st.columns([4, 1, 1])
    with col_info:
        st.markdown(
            f'<span style="display:inline-block;width:12px;height:12px;border-radius:3px;'
            f'background:{color};vertical-align:middle;margin-right:6px;"></span>'
            f'**{s.get("label", "")}** · {school_name} · {day_label} {start_str}~{end_str}',
            unsafe_allow_html=True,
        )

    with col_edit:
        if st.button("✏️", key=f"sched_edit_{sid}"):
            st.session_state[f"_sched_editing_{sid}"] = True

    with col_del:
        if st.button("🗑️", key=f"sched_del_{sid}"):
            delete_class_schedule(cfg, sid)
            st.session_state["_admin_toast"] = "삭제 완료"
            st.rerun()

    # 수정 폼
    if st.session_state.get(f"_sched_editing_{sid}"):
        with st.container():
            ec1, ec2 = st.columns(2)
            with ec1:
                e_school = st.selectbox(
                    "학교", tenant_ids, key=f"sched_e_school_{sid}",
                    index=tenant_ids.index(s["school_id"]) if s["school_id"] in tenant_ids else 0,
                    format_func=lambda tid: f"{cfg.get_layout(tid)} ({tid})",
                )
                e_dow = st.selectbox(
                    "요일", list(range(7)), key=f"sched_e_dow_{sid}",
                    index=s["day_of_week"],
                    format_func=lambda i: _DAY_LABELS[i],
                )
                e_label = st.text_input("수업명", value=s.get("label", ""), key=f"sched_e_label_{sid}")
            with ec2:
                e_sh = st.number_input("시작 시", 0, 23, s["start_hour"], key=f"sched_e_sh_{sid}")
                e_sm_options = [0, 10, 15, 20, 30, 40, 45, 50]
                e_sm = st.selectbox("시작 분", e_sm_options, key=f"sched_e_sm_{sid}",
                                    index=e_sm_options.index(s["start_minute"]) if s["start_minute"] in e_sm_options else 0)
                e_eh = st.number_input("종료 시", 0, 23, s["end_hour"], key=f"sched_e_eh_{sid}")
                e_em = st.selectbox("종료 분", e_sm_options, key=f"sched_e_em_{sid}",
                                    index=e_sm_options.index(s["end_minute"]) if s["end_minute"] in e_sm_options else 0)

            bc1, bc2 = st.columns(2)
            with bc1:
                if st.button("저장", key=f"sched_save_{sid}", type="primary"):
                    update_class_schedule(cfg, sid, {
                        "school_id": e_school,
                        "day_of_week": e_dow,
                        "start_hour": e_sh,
                        "start_minute": e_sm,
                        "end_hour": e_eh,
                        "end_minute": e_em,
                        "label": e_label.strip(),
                        "color": "",
                    })
                    del st.session_state[f"_sched_editing_{sid}"]
                    st.session_state["_admin_toast"] = "수정 완료"
                    st.rerun()
            with bc2:
                if st.button("취소", key=f"sched_cancel_{sid}"):
                    del st.session_state[f"_sched_editing_{sid}"]
                    st.rerun()


# ── 학교 정보 ──────────────────────────────────────────────

def _render_school_info(cfg: AppConfig):
    """등록된 학교(tenant) 목록 및 상세 정보 표시."""
    import json
    import base64
    from pathlib import Path

    st.subheader("등록된 학교 목록")

    tenant_dir = Path(cfg.tenant_config_dir) / "tenants"
    if not tenant_dir.exists():
        tenant_dir = Path("tenants")

    _top = {"default": 0, "aimz": 1}
    tenant_files = sorted(tenant_dir.glob("*.json"), key=lambda f: (_top.get(f.stem, 2), f.stem))
    if not tenant_files:
        st.info("등록된 학교가 없습니다.")
        return

    for tf in tenant_files:
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
        except Exception:
            continue

        tid = data.get("tenant_id", tf.stem)
        layout = data.get("layout", tid)
        branding = data.get("branding", {})
        features = data.get("enabled_features", [])
        logo_path = branding.get("logo_path", "")
        main_path = branding.get("main_path", "")

        with st.expander(f"🏫 {layout} ({tid})", expanded=False):
            c1, c2 = st.columns([1, 2])

            with c1:
                # 로고 표시
                if logo_path and Path(logo_path).exists():
                    logo_b64 = base64.b64encode(Path(logo_path).read_bytes()).decode()
                    st.markdown(
                        f'<div style="background:#fff;border-radius:8px;padding:8px;text-align:center;margin-bottom:8px;">'
                        f'<img src="data:image/png;base64,{logo_b64}" style="max-height:60px;object-fit:contain;"></div>',
                        unsafe_allow_html=True,
                    )
                    st.caption("✅ 로고")
                else:
                    st.caption("❌ 로고 없음")

                # 메인 배너 표시
                if main_path and Path(main_path).exists():
                    main_b64 = base64.b64encode(Path(main_path).read_bytes()).decode()
                    st.markdown(
                        f'<div style="background:#fff;border-radius:8px;padding:8px;text-align:center;">'
                        f'<img src="data:image/png;base64,{main_b64}" style="max-height:60px;object-fit:contain;"></div>',
                        unsafe_allow_html=True,
                    )
                    st.caption("✅ 메인 배너")
                else:
                    st.caption(f"❌ 메인 배너 없음 ({Path(main_path).name if main_path else '미설정'})")

            with c2:
                st.markdown(f"**페이지 타이틀:** {branding.get('page_title', '-')}")
                st.markdown(f"**브라우저 탭:** {branding.get('browser_tab_title', '-')}")
                st.markdown(f"**로고 경로:** `{logo_path or '없음'}`")
                st.markdown(f"**메인 배너:** `{main_path or '없음'}`")

                # 오픈 탭 뱃지
                tabs = [f.replace("tab.", "") for f in features if f.startswith("tab.")]
                if tabs:
                    _tab_colors = {
                        "gpt": "#2ecc71", "mj": "#e74c3c", "nanobanana": "#f39c12", "nanobanana_2": "#f39c12",
                        "nanobanana_pro": "#f39c12", "kling": "#9b59b6", "kling_veo": "#9b59b6",
                        "kling_grok": "#9b59b6", "elevenlabs": "#3498db", "suno": "#e67e22",
                    }
                    _badges = ''.join(
                        f'<span style="display:inline-block;background:{_tab_colors.get(t, "#95a5a6")};'
                        f'color:#fff;padding:2px 8px;border-radius:10px;font-size:0.75em;'
                        f'font-weight:600;margin:2px 3px;">{t}</span>'
                        for t in tabs
                    )
                    st.markdown(f"**오픈 탭 ({len(tabs)})**<br>{_badges}", unsafe_allow_html=True)
                else:
                    st.markdown("**오픈 탭:** 없음")