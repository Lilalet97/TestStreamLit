# ui/admin_page.py
import base64
from pathlib import Path
import streamlit as st

from core.config import AppConfig
from core.auth import current_user, logout_user, hash_password
from core.db import (
    list_active_jobs_all,
    list_runs_admin,
    list_key_waiters,
    list_key_leases,
    list_users,
    upsert_user,
    update_user_fields,
    set_user_password,
    set_user_active,
    hard_delete_user,
)


def _rows_to_dicts(rows):
    return [dict(r) for r in (rows or [])]


def _encode_logo(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


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

def render_admin_page(cfg: AppConfig):
    u = current_user()
    if not u or u.role != 'admin':
        st.error('관리자 권한이 필요합니다.')
        return

    with st.sidebar:
        # 학교 로고가 있으면 아바타 원 대신 로고 표시
        logo_path = cfg.get_logo_path(u.school_id)
        if logo_path:
            avatar_html = (
                f'<img src="data:image/png;base64,{_encode_logo(logo_path)}" '
                f'style="width:40px;height:40px;border-radius:50%;object-fit:cover;">'
            )
        else:
            avatar_html = (
                f'<div style="'
                f'width:40px;height:40px;border-radius:50%;'
                f'background:linear-gradient(135deg,#e74c3c,#c0392b);'
                f'display:flex;align-items:center;justify-content:center;'
                f'font-size:18px;font-weight:700;color:#fff;'
                f'">{u.user_id[0].upper()}</div>'
            )

        badge_html = (
            '<span style="background:#e74c3c;color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:0.75em;font-weight:600;'
            'letter-spacing:0.5px;">ADMIN</span>'
        )

        st.markdown(
            f"""
            <div style="
                background: linear-gradient(135deg, #1e1e2f 0%, #2d2d44 100%);
                border: 1px solid #3d3d5c;
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 8px;
            ">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                    {avatar_html}
                    <div>
                        <div style="font-size:1em;font-weight:600;color:#f0f0f0;">
                            {u.user_id}
                        </div>
                        <div style="margin-top:2px;">
                            {badge_html}
                        </div>
                    </div>
                </div>
                <div style="
                    font-size:0.8em;color:#a0a0b8;
                    display:flex;align-items:center;gap:5px;
                ">
                    <span>🏫</span>
                    <span>{cfg.get_layout(u.school_id)}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button('로그아웃', icon=":material/logout:", use_container_width=True):
            logout_user(cfg)
            st.rerun()

    st.title('🛠️ 운영 페이지')

    tab_monitor, tab_runs, tab_keypool, tab_users = st.tabs(['모니터링', '실행 기록', '키풀 상태', '계정 관리'])

    # --- 모니터링 ---
    with tab_monitor:
        _live_monitor_panel(cfg)

    # --- 실행 기록 ---
    with tab_runs:
        st.subheader('Runs')
        user_rows = list_users(cfg, include_inactive=True)
        user_ids = ['(all)'] + [r['user_id'] for r in user_rows]
        sel_user = st.selectbox('필터: user_id', user_ids, index=0)
        limit = st.slider('표시 개수', 50, 500, 200, 50)

        rows = list_runs_admin(cfg, limit=limit, user_id=None if sel_user == '(all)' else sel_user)
        runs = _rows_to_dicts(rows)
        if runs:
            st.dataframe(runs, width="stretch", hide_index=True)
        else:
            st.info('표시할 run 기록이 없습니다.')

    # --- 키풀 상태 ---
    with tab_keypool:
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

    # --- 계정 관리 ---
    with tab_users:
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
            new_role = st.selectbox('Role', ['user', 'admin'], index=0)
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
                    role_opts = ['user', 'admin']
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