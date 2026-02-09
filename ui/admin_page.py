# ui/admin_page.py
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
    set_user_password,
    set_user_active,
    hard_delete_user,
)


def _rows_to_dicts(rows):
    return [dict(r) for r in (rows or [])]


def render_admin_page(cfg: AppConfig):
    u = current_user()
    if not u or u.role != 'admin':
        st.error('관리자 권한이 필요합니다.')
        return

    with st.sidebar:
        st.markdown('### 👤 운영 계정')
        st.write({'user_id': u.user_id, 'role': u.role, 'school_id': u.school_id})
        if st.button('로그아웃', use_container_width=True):
            logout_user()
            st.rerun()

    st.title('🛠️ 운영 페이지')

    tab_monitor, tab_runs, tab_keypool, tab_users = st.tabs(['모니터링', '실행 기록', '키풀 상태', '계정 관리'])

    # --- 모니터링 ---
    with tab_monitor:
        jobs = _rows_to_dicts(list_active_jobs_all(cfg, limit=500))
        active_users = sorted({j.get('user_id') for j in jobs if j.get('user_id')})

        c1, c2, c3 = st.columns(3)
        c1.metric('Active Jobs', len(jobs))
        c2.metric('Active Users', len(active_users))
        c3.metric('Providers', len(sorted({j.get('provider') for j in jobs if j.get('provider')})))

        st.subheader('Active Jobs')
        if jobs:
            st.dataframe(jobs, use_container_width=True, hide_index=True)
        else:
            st.info('현재 active_jobs가 없습니다.')

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
            st.dataframe(runs, use_container_width=True, hide_index=True)
        else:
            st.info('표시할 run 기록이 없습니다.')

    # --- 키풀 상태 ---
    with tab_keypool:
        st.subheader('Waiters')
        waiters = _rows_to_dicts(list_key_waiters(cfg, limit=500))
        if waiters:
            st.dataframe(waiters, use_container_width=True, hide_index=True)
        else:
            st.info('대기열(waiters)이 없습니다.')

        st.subheader('Leases')
        leases = _rows_to_dicts(list_key_leases(cfg, limit=500))
        if leases:
            st.dataframe(leases, use_container_width=True, hide_index=True)
        else:
            st.info('임대(leases)가 없습니다.')

    # --- 계정 관리 ---
    with tab_users:
        st.subheader('계정 목록')
        users = _rows_to_dicts(list_users(cfg, include_inactive=True))
        if users:
            st.dataframe(users, use_container_width=True, hide_index=True)
        else:
            st.warning('등록된 계정이 없습니다(부트스트랩 관리자만 있는 경우에도 여기에 보입니다).')

        st.markdown('---')

        st.subheader('계정 추가')
        with st.form('create_user'):
            new_user_id = st.text_input('User ID')
            new_pw = st.text_input('Password', type='password')
            new_role = st.selectbox('Role', ['user', 'admin'], index=0)
            new_school_id = st.text_input('School ID', value='default')
            submitted = st.form_submit_button('추가')

        if submitted:
            new_user_id = (new_user_id or '').strip()
            new_school_id = (new_school_id or 'default').strip() or 'default'
            if not new_user_id or not new_pw:
                st.error('User ID와 Password는 필수입니다.')
            else:
                upsert_user(cfg, user_id=new_user_id, password_hash=hash_password(new_pw), role=new_role, school_id=new_school_id, is_active=1)
                st.success('계정이 추가/갱신되었습니다.')
                st.rerun()

        st.subheader('계정 상태/비밀번호 변경')
        if users:
            ids = [x['user_id'] for x in users]
            target = st.selectbox('대상 계정', ids)

            colA, colB = st.columns(2)
            with colA:
                st.markdown('**활성/비활성**')
                is_active = st.toggle('활성 상태', value=bool(next((x['is_active'] for x in users if x['user_id']==target), 1)))
                if st.button('상태 적용'):
                    if target == u.user_id:
                        st.error('본인 계정의 활성 상태는 여기서 변경할 수 없습니다.')
                    else:
                        set_user_active(cfg, target, is_active)
                        st.success('상태가 변경되었습니다.')
                        st.rerun()

            with colB:
                st.markdown('**비밀번호 재설정**')
                new_pw2 = st.text_input('새 비밀번호', type='password', key='reset_pw')
                if st.button('비밀번호 변경'):
                    if not new_pw2:
                        st.error('새 비밀번호를 입력하세요.')
                    else:
                        set_user_password(cfg, target, hash_password(new_pw2))
                        st.success('비밀번호가 변경되었습니다.')
                        st.rerun()

            st.markdown('---')
            st.subheader('계정 삭제(하드 삭제)')
            st.warning('삭제는 되돌릴 수 없습니다. 기본적으로는 비활성화를 권장합니다.')
            confirm = st.text_input('삭제 확인: 대상 user_id를 그대로 입력하세요', key='del_confirm')
            if st.button('하드 삭제 실행'):
                if target == u.user_id:
                    st.error('본인 계정은 삭제할 수 없습니다.')
                elif confirm != target:
                    st.error('확인 문구가 일치하지 않습니다.')
                else:
                    hard_delete_user(cfg, target)
                    st.success('삭제되었습니다.')
                    st.rerun()