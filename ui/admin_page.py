# ui/admin_page.py
from pathlib import Path
import streamlit as st

from core.config import AppConfig
from core.auth import current_user, hash_password
from core.db import (
    list_active_jobs_all,
    list_key_waiters,
    list_key_leases,
    list_users,
    list_mj_gallery_admin,
    get_mj_gallery_by_id,
    list_gpt_conversations_admin,
    get_gpt_conversation_by_id,
    list_kling_web_admin,
    get_kling_web_by_id,
    list_elevenlabs_admin,
    get_elevenlabs_by_id,
    list_nanobanana_admin,
    get_nanobanana_by_id,
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
)
from ui.stress_test_tab import render_stress_test_execution, render_stress_test_results
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


def _render_nanobanana_detail(cfg: AppConfig, row_id: int):
    """NanoBanana 이미지 생성 히스토리 상세 내용을 렌더링."""
    item = get_nanobanana_by_id(cfg, row_id)
    if not item:
        st.warning('항목을 찾을 수 없습니다.')
        return
    st.markdown(f"**{(item['prompt'] or '')[:100]}**  ·  `{item['model_label']}`")
    st.caption(
        f"user: {item['user_id']}  |  model: {item['model_id']}  |  "
        f"aspect: {item['aspect_ratio']}  |  created: {item['created_at']}"
    )
    st.divider()

    st.text_area('Prompt', item['prompt'], height=100, disabled=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Model', item['model_label'] or 'N/A')
    c2.metric('Aspect Ratio', item['aspect_ratio'])
    c3.metric('Num Images', item['num_images'])
    c4.metric('Style', item['style_preset'] or 'Auto')

    if item['negative_prompt']:
        st.text_area('Negative Prompt', item['negative_prompt'], height=60, disabled=True)

    if item['settings']:
        with st.expander('Settings'):
            st.json(item['settings'])

    images = item.get('image_urls') or []
    if images:
        st.subheader(f'생성 이미지 ({len(images)}장)')
        cols = st.columns(min(len(images), 4))
        for i, url in enumerate(images):
            with cols[i % len(cols)]:
                if url and url.startswith(('http://', 'https://', 'data:')):
                    st.image(url, width='stretch')
                else:
                    st.info(f'이미지 표시 불가: {url}')
    else:
        st.info('생성된 이미지가 없습니다.')


def _maybe_open_nanobanana_dialog(cfg: AppConfig):
    """NanoBanana 보기 다이얼로그 트리거."""
    row_id = st.session_state.get('_view_nanobanana_row_id')
    if not row_id or not st.session_state.get('_open_nanobanana_detail'):
        return
    st.session_state['_open_nanobanana_detail'] = False

    if hasattr(st, 'dialog'):
        @st.dialog('\U0001f34c NanoBanana 상세', width='large')
        def _dlg():
            _render_nanobanana_detail(cfg, row_id)
        _dlg()
    else:
        with st.expander('\U0001f34c NanoBanana 상세', expanded=True):
            _render_nanobanana_detail(cfg, row_id)


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

@st.fragment(run_every="1s")
def _live_monitor_panel(cfg: AppConfig):
    jobs = _rows_to_dicts(list_active_jobs_all(cfg, limit=500))
    active_users = sorted({j.get('user_id') for j in jobs if j.get('user_id')})

    c1, c2, c3 = st.columns(3)
    c1.metric('Active Jobs', len(jobs))
    c2.metric('Active Users', len(active_users))
    c3.metric('Providers', len({j.get('provider') for j in jobs if j.get('provider')}))

    st.subheader('Active Jobs')
    if jobs:
        st.dataframe(jobs, width="stretch", hide_index=True)
    else:
        st.info('현재 active_jobs가 없습니다.')

def render_viewer_page(cfg: AppConfig):
    """viewer 역할용: 모니터링 + 실행 기록만 표시 (읽기 전용)."""
    u = current_user()
    if not u or u.role != 'viewer':
        st.error('viewer 권한이 필요합니다.')
        return

    _VIEWER_TABS = ['모니터링', '실행 기록', '부하테스트 결과']

    with st.sidebar:
        selected = st.radio(
            "메뉴",
            options=range(len(_VIEWER_TABS)),
            format_func=lambda i: _VIEWER_TABS[i],
            key="viewer_tab",
            label_visibility="collapsed",
        )

    if selected == 0:
        _live_monitor_panel(cfg)

    elif selected == 1:
        user_rows = list_users(cfg, include_inactive=True)
        user_ids = ['(all)'] + [r['user_id'] for r in user_rows]
        sel_user = st.selectbox('필터: user_id', user_ids, index=0, key='viewer_user_filter')
        limit = st.slider('표시 개수', 50, 500, 200, 50, key='viewer_limit')

        filter_uid = None if sel_user == '(all)' else sel_user

        # ── GPT Conversations ──
        st.subheader('💬 GPT Conversations')
        gpt_items = list_gpt_conversations_admin(cfg, limit=limit, user_id=filter_uid)
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
        mj_items = _rows_to_dicts(mj_rows)
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

        # ── Kling Web ──
        st.subheader('🎬 Kling Web')
        kling_items = list_kling_web_admin(cfg, limit=limit, user_id=filter_uid)
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
        el_items = list_elevenlabs_admin(cfg, limit=limit, user_id=filter_uid)
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

        # ── NanoBanana Sessions (멀티턴) ──
        st.subheader('\U0001f34c NanoBanana Sessions')
        nb_sessions = list_nanobanana_sessions_admin(cfg, limit=limit, user_id=filter_uid)
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

    elif selected == 2:
        render_stress_report(cfg)



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
                    st.success(f"{deleted:,}건 삭제 완료")
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
        submitted = st.form_submit_button("설정 저장", use_container_width=True)

    if submitted:
        for tbl in PURGEABLE_TABLES:
            set_admin_setting(cfg, f"purge_days.{tbl['key']}", str(new_vals[tbl["key"]]))
        st.success("자동 삭제 설정이 저장되었습니다.")
        st.rerun()

    if st.button("지금 자동 삭제 실행", key="db_purge_now"):
        results = run_auto_purge(cfg)
        if results:
            for key, cnt in results.items():
                label = next((t["label"] for t in PURGEABLE_TABLES if t["key"] == key), key)
                st.success(f"{label}: {cnt:,}건 삭제")
        else:
            st.info("삭제 대상이 없거나 자동 삭제가 비활성 상태입니다.")


def render_admin_page(cfg: AppConfig):
    u = current_user()
    if not u or u.role != 'admin':
        st.error('관리자 권한이 필요합니다.')
        return

    _ADMIN_TABS = ['모니터링', '키풀 상태', '실행 기록', '부하테스트', '계정 관리', 'DB 관리']

    with st.sidebar:
        selected = st.radio(
            "관리 메뉴",
            options=range(len(_ADMIN_TABS)),
            format_func=lambda i: _ADMIN_TABS[i],
            key="admin_tab",
            label_visibility="collapsed",
        )

    # --- 모니터링 ---
    if selected == 0:
        _live_monitor_panel(cfg)

    # --- 키풀 상태 ---
    elif selected == 1:
        st.subheader('Waiters')
        waiters = _rows_to_dicts(list_key_waiters(cfg, limit=500))
        if waiters:
            st.dataframe(waiters, width="stretch", hide_index=True)
        else:
            st.info('대기열(waiters)이 없습니다.')

        st.subheader('Leases')
        leases = _rows_to_dicts(list_key_leases(cfg, limit=500))
        if leases:
            st.dataframe(leases, width="stretch", hide_index=True)
        else:
            st.info('임대(leases)가 없습니다.')

    # --- 실행 기록 ---
    elif selected == 2:
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

        # ── 향후 추가: Wisk 등 ──

    # --- 부하테스트 ---
    elif selected == 3:
        render_stress_test_execution(cfg)
        st.divider()
        render_stress_test_results(cfg)

    # --- 계정 관리 ---
    elif selected == 4:
        st.subheader('계정 목록')
        users = _rows_to_dicts(list_users(cfg, include_inactive=True))
        if users:
            st.dataframe(users, width="stretch", hide_index=True)
        else:
            st.warning('등록된 계정이 없습니다(부트스트랩 관리자만 있는 경우에도 여기에 보입니다).')

        st.markdown('---')

        st.subheader('계정 추가')
        tenant_ids = _list_tenant_ids(cfg)
        with st.form('create_user'):
            new_user_id = st.text_input('User ID')
            new_pw = st.text_input('Password', type='password')
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
                upsert_user(cfg, user_id=new_user_id, password_hash=hash_password(new_pw), role=new_role, school_id=new_school_id, is_active=1)
                st.success('계정이 추가/갱신되었습니다.')
                st.rerun()

        st.subheader('계정 수정')
        if users:
            ids = [x['user_id'] for x in users]
            target = st.selectbox('대상 계정', ids, key='edit_target')
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

                col3, col4 = st.columns(2)
                with col3:
                    cur_active = bool(target_row.get('is_active', 1))
                    new_active = st.toggle('활성 상태', value=cur_active)
                with col4:
                    new_pw2 = st.text_input('새 비밀번호 (변경 시에만 입력)', type='password', key='reset_pw')

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

                submitted_edit = st.form_submit_button('변경 사항 저장', use_container_width=True)

            if submitted_edit:
                changes = []

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

                # 비밀번호 변경
                if new_pw2:
                    set_user_password(cfg, target, hash_password(new_pw2))
                    changes.append('비밀번호 재설정')

                if changes:
                    st.success('변경 완료: ' + ', '.join(changes))
                    st.rerun()
                elif not any(st.session_state.get(f'_edit_err_{i}') for i in range(4)):
                    st.info('변경된 항목이 없습니다.')

            st.markdown('---')
            st.subheader('계정 삭제(하드 삭제)')
            st.warning('삭제는 되돌릴 수 없습니다. 기본적으로는 비활성화를 권장합니다.')
            confirm = st.text_input('삭제 확인: 대상 user_id를 그대로 입력하세요', key='del_confirm')
            if st.button('하드 삭제 실행'):
                if is_self:
                    st.error('본인 계정은 삭제할 수 없습니다.')
                elif confirm != target:
                    st.error('확인 문구가 일치하지 않습니다.')
                else:
                    hard_delete_user(cfg, target)
                    st.success('삭제되었습니다.')
                    st.rerun()

    # --- DB 관리 ---
    elif selected == 5:
        _render_db_management(cfg)