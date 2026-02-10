# ui/tabs/legnext_tab.py
import time
import uuid
import traceback
import streamlit as st
import os

from core.config import AppConfig
from core.db import (
    guard_concurrency_or_raise, insert_run_and_activate, finish_run,
    update_run, update_run_and_touch, now_iso
)
from core.redact import redact_obj, json_dumps_safe
from core.analysis import analyze_error
from providers import legnext
from ui.sidebar import SidebarState
from core.key_pool import acquire_lease, release_lease, heartbeat, consume_rpm
from ui import result_store


def render_legnext_tab(cfg: AppConfig, sidebar: SidebarState):
    def _get_secret(name: str) -> str:
        v = os.getenv(name)
        if v:
            return v
        try:
            # Streamlit secrets 지원
            return str(st.secrets.get(name, "")).strip()
        except Exception:
            return ""

    # 1) KEY_POOL_JSON은 secrets/env 둘 다 지원
    _key_pool_json = _get_secret("KEY_POOL_JSON")
    if _key_pool_json and not os.getenv("KEY_POOL_JSON"):
        # core.key_pool 쪽이 os.getenv만 보는 구현이어도 동작하게 강제 주입
        os.environ["KEY_POOL_JSON"] = _key_pool_json

    use_key_pool = bool(_key_pool_json)

    # 2) LegNext API Key도 secrets/env 보조 (cfg가 비면 여기도 확인)
    fallback_api_key = (cfg.legnext_api_key or _get_secret("MJ_API_KEY")).strip()
    
    result_store.init("legnext")

    st.header("Midjourney via LegNext (Image)")

    # key pool 사용이 기본이면 cfg.legnext_api_key 체크는 'fallback' 용도임
    if (not sidebar.test_mode) and (not use_key_pool) and (not fallback_api_key):
        st.warning("Secrets 또는 환경변수에 MJ_API_KEY(=LegNext API Key) 또는 KEY_POOL_JSON을 설정해야 합니다.")

    mj_prompt = st.text_area(
        "프롬프트 입력",
        placeholder="A cinematic shot of a cyber-punk city...",
        height=140,
        key="mj_prompt",
    )

    use_adv_mj = st.toggle("MJ 상세 파라미터 활성화", value=False, key="mj_toggle")

    mj_params = ""
    if use_adv_mj:
        with st.expander("🛠️ MJ 파라미터 (프롬프트 뒤에 붙여 전송)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("### 📐 Canvas & Model")
                mj_ar = st.selectbox("화면 비율 (--ar)", ["1:1", "16:9", "9:16", "4:5", "2:3", "3:2", "21:9"])
                mj_ver = st.selectbox("모델 버전 (--v)", ["7", "6.1", "6.0", "5.2", "5.1", "Niji 6", "Niji 5"])
                mj_quality = st.select_slider("품질 (--q)", options=[0.25, 0.5, 1], value=1)
            with c2:
                st.markdown("### 🎨 Artistic Control")
                mj_stylize = st.number_input("스타일 강도 (--s)", 0, 1000, 250, step=50)
                mj_chaos = st.number_input("카오스 (다양성, --c)", 0, 100, 0)
                mj_weird = st.number_input("기괴함 (--w)", 0, 3000, 0, step=100)
            with c3:
                st.markdown("### ⚙️ Extra")
                mj_stop = st.slider("생성 중단 시점 (--stop)", 10, 100, 100)
                mj_tile = st.checkbox("패턴 타일링 (--tile)")
                mj_raw = st.checkbox("RAW 스타일 적용 (--style raw)")
                mj_draft = st.checkbox("초안 모드 (--draft)")

            mj_params = f" --ar {mj_ar} --v {mj_ver} --q {mj_quality} --s {mj_stylize} --c {mj_chaos}"
            if mj_weird > 0:
                mj_params += f" --w {mj_weird}"
            if mj_tile:
                mj_params += " --tile"
            if mj_raw:
                mj_params += " --style raw"
            if mj_draft:
                mj_params += " --draft"
            if mj_stop < 100:
                mj_params += f" --stop {mj_stop}"
    
    is_mode = st.toggle("⚙️ 실행 옵션", key="leg_play_mode")
    auto_poll = True
    poll_interval = 2.0
    max_wait= 120

    if is_mode:
        auto_poll = st.toggle("제출 후 자동 폴링", value=True, key="mj_auto_poll")
        poll_interval = st.slider("폴링 간격(초)", 1.0, 10.0, 2.0, 0.5, key="mj_poll_interval")
        max_wait = st.slider("최대 대기(초)", 10, 300, 120, 10, key="mj_max_wait")

    st.markdown("---")
    submit = st.button("LegNext로 생성 요청(제출)", key="mj_submit_btn", width="stretch")

    if not submit:
        result_store.render(
            "legnext",
            title=None,
            show_history=False,
            show_clear=False,
            show_inflight=True,
        )
        return

    provider = "legnext"
    operation = "image.generate"
    endpoint = f"{legnext.LEGNEXT_BASE}/diffusion"
    run_id = str(uuid.uuid4())
    start_t = time.time()

    request_obj = {
        "prompt": mj_prompt,
        "mj_params": mj_params,
        "auto_poll": auto_poll,
        "poll_interval": poll_interval,
        "max_wait": max_wait,
        "mock": sidebar.test_mode,
        "mock_scenario": sidebar.mock_scenario if sidebar.test_mode else None,
    }

    active_added = False
    lease = None
    used_key_label = "secrets/fallback"
    api_key = ""

    blocks = []

    def log(t: str, **kw):
        blocks.append({"t": t, **kw})

    try:
        guard_concurrency_or_raise(cfg)
        insert_run_and_activate(cfg, {
            "run_id": run_id,
            "created_at": now_iso(),
            "user_id": st.session_state.user_id,
            "session_id": st.session_state.session_id,
            "provider": provider,
            "operation": operation,
            "endpoint": endpoint,
            "request_json": json_dumps_safe(redact_obj(request_obj)),
            "state": "running",
        }, provider, operation)
        active_added = True

        if not mj_prompt.strip():
            raise RuntimeError("프롬프트를 입력하세요.")

        if (not use_key_pool) and (not fallback_api_key):
            raise RuntimeError("KEY_POOL_JSON 또는 MJ_API_KEY(=LegNext API Key)를 등록해주세요.")

        # inflight 시작
        result_store.set_inflight(
            "legnext",
            stage="run.start",
            ts=now_iso(),
            run_id=run_id,
        )

        wait_ph = st.empty()
        _last_wait = {"t": 0.0}

        def _on_wait(info):
            now = time.time()
            if now - _last_wait["t"] < 0.7:
                return
            _last_wait["t"] = now

            stt = (info.get("state") or "waiting").strip()
            pos = info.get("pos")

            if stt == "waiting_turn":
                msg = f"⏳ 대기열 대기중… (내 순번: {pos})"
                wait_ph.info(msg)
                result_store.update_inflight("legnext", stage="run.waiting_turn", pos=pos, ts=now_iso())
            elif stt == "waiting_key":
                msg = "⏳ 내 차례지만 사용 가능한 키가 없어 대기중… (동시성/RPM 제한)"
                wait_ph.warning(msg)
                result_store.update_inflight("legnext", stage="run.waiting_key", pos=pos, ts=now_iso())
            elif stt in ("waiting_rpm", "rate_limited", "rpm_wait"):
                # (4번에서 RPM 대기 표시랑 연결)
                msg = f"⏳ RPM 제한으로 대기중… (pos: {pos})" if pos is not None else "⏳ RPM 제한으로 대기중…"
                wait_ph.warning(msg)
                result_store.update_inflight("legnext", stage="run.waiting_rpm", pos=pos, ts=now_iso())
            else:
                # ✅ 나머지 상태도 전부 UI에 표시
                msg = f"⏳ 대기중… ({stt})"
                if pos is not None:
                    msg += f" / pos={pos}"
                wait_ph.info(msg)
                result_store.update_inflight("legnext", stage="run.waiting_any", status=stt, pos=pos, ts=now_iso())

        # 키 확보
        api_key = ""
        lease = None

        if use_key_pool:
            wait_ph.info("⏳ 키 풀에서 키를 할당받는 중…")
            result_store.update_inflight("legnext", stage="run.waiting_any", ts=now_iso())
            lease = acquire_lease(
                cfg,
                provider="legnext",
                run_id=run_id,
                user_id=st.session_state.user_id,
                session_id=st.session_state.session_id,
                school_id=st.session_state.get("school_id", "default"),
                wait=True,
                max_wait_sec=int(max_wait),
                poll_interval_sec=min(2.0, float(poll_interval)),
                request_units=1,
                on_wait=_on_wait,
            )
            used_key_label = f"{lease.key_name} (api_key_id={lease.api_key_id})"
            st.caption(f"🔑 사용 키: {used_key_label}")
            log("info", msg=f"사용 키: {used_key_label}")
            api_key = lease.key_payload.get("api_key", "")
            if not api_key:
                raise RuntimeError("키 풀에서 legnext api_key를 얻지 못했습니다. KEY_POOL_JSON/시드 설정을 확인하세요.")

            result_store.update_inflight(
                "legnext",
                stage="run.lease_acquired",
                lease_id=lease.lease_id,
                api_key_id=getattr(lease, "api_key_id", None),
                key_name=getattr(lease, "key_name", None),
                ts=now_iso(),
            )
        else:
            api_key = fallback_api_key
            if not api_key:
                raise RuntimeError("MJ_API_KEY(=LegNext API Key)를 얻지 못했습니다. 설정을 확인하세요.")
            result_store.update_inflight("legnext", stage="run.key_from_cfg", ts=now_iso())

        msg = "키 확보 완료. 작업 진행합니다."
        wait_ph.success(msg)
        log("success", msg=msg)

        full_text = f"{mj_prompt}{mj_params}"
        st.info("요청 텍스트(프롬프트+옵션) 미리보기")
        st.code(full_text)
        log("info", msg="요청 텍스트(프롬프트+옵션) 미리보기")
        log("code", body=full_text)

        result_store.update_inflight("legnext", stage="run.submitting", ts=now_iso())

        with st.spinner("LegNext에 작업 제출 중..."):
            if sidebar.test_mode:
                sc, raw, j = legnext.mock_submit(full_text, sidebar.mock_scenario)
            else:
                sc, raw, j = legnext.submit(full_text, api_key)

        update_run(cfg, run_id,
                   http_status=sc,
                   response_text=raw,
                   response_json=json_dumps_safe(redact_obj(j)) if isinstance(j, (dict, list)) else None)

        if sc != 200 or not isinstance(j, dict) or legnext.is_error_obj(j) or not j.get("job_id"):
            analysis = analyze_error(cfg, provider, operation, endpoint,
                                     {"text": full_text, "meta": request_obj},
                                     sc, raw, j if isinstance(j, dict) else None, None)
            update_run(cfg, run_id,
                       gpt_analysis=json_dumps_safe(analysis),
                       state="failed",
                       error_text=f"Submit failed. HTTP={sc}\n{raw[:5000]}",
                       duration_ms=int((time.time() - start_t) * 1000))

            st.error(f"제출 실패 (HTTP {sc})")
            st.text(raw)
            if isinstance(j, dict):
                st.json(j)
            with st.expander("🧠 원인 분석", expanded=True):
                st.json(analysis)

            log("error", msg=f"제출 실패 (HTTP {sc})")
            log("json", obj={"http_status": sc, "raw": raw, "json": redact_obj(j) if isinstance(j, dict) else None})
            log("expander", label="🧠 원인 분석", expanded=True, blocks=[{"t": "json", "obj": analysis}])

            result_store.push("legnext", {
                "ts": now_iso(),
                "kind": "blocks",
                "run_id": run_id,
                "job_id": "",
                "blocks": blocks,
            })
            return

        job_id = j["job_id"]
        st.success(f"제출 성공! job_id = {job_id}")
        st.json(j)
        log("success", msg=f"제출 성공! job_id = {job_id}")
        log("json", obj=redact_obj(j))

        update_run_and_touch(cfg, run_id, active_state="submitted", job_id=job_id, state="submitted")
        result_store.update_inflight("legnext", stage="run.submitted", job_id=job_id, ts=now_iso())

        if not auto_poll:
            update_run(cfg, run_id, duration_ms=int((time.time() - start_t) * 1000))
            result_store.push("legnext", {
                "ts": now_iso(),
                "kind": "blocks",
                "run_id": run_id,
                "job_id": job_id,
                "blocks": blocks,
            })
            return

        st.markdown("### ⏳ 자동 폴링 진행")
        log("markdown", body="### ⏳ 자동 폴링 진행")

        status_ph = st.empty()
        prog = st.progress(0.0)

        deadline = time.time() + float(max_wait)
        last_json = None
        last_status = ""
        poll_count = 0

        result_store.update_inflight("legnext", stage="run.polling", ts=now_iso())

        while time.time() < deadline:
            # heartbeat는 3회마다 1번 (네트워크 커밋 절약)
            if lease and poll_count % 3 == 0:
                heartbeat(cfg, lease.lease_id)

            if lease and getattr(lease, "api_key_id", None):
                consume_rpm(
                    cfg,
                    lease.api_key_id,
                    units=1,
                    wait=True,
                    max_wait_sec=30,
                    poll_interval_sec=1.0,
                    on_wait=_on_wait,
                )

            if sidebar.test_mode:
                sc2, raw2, j2 = legnext.mock_get_job(job_id)
            else:
                sc2, raw2, j2 = legnext.get_job(job_id, api_key)

            last_json = j2 if isinstance(j2, dict) else None

            # update_run + touch_active_job → 단일 커밋
            update_run_and_touch(cfg, run_id,
                       http_status=sc2,
                       response_text=raw2,
                       response_json=json_dumps_safe(redact_obj(j2)) if isinstance(j2, (dict, list)) else None)
            poll_count += 1

            if sc2 != 200 or not isinstance(j2, dict) or legnext.is_error_obj(j2):
                analysis = analyze_error(cfg, provider, operation, f"{legnext.LEGNEXT_BASE}/job/{job_id}",
                                         request_obj, sc2, raw2, j2 if isinstance(j2, dict) else None, None)
                update_run(cfg, run_id,
                           gpt_analysis=json_dumps_safe(analysis),
                           state="failed",
                           error_text=f"Poll failed. HTTP={sc2}\n{raw2[:5000]}")

                st.error(f"폴링 실패 (HTTP {sc2})")
                st.text(raw2)
                with st.expander("🧠 원인 분석", expanded=True):
                    st.json(analysis)

                log("error", msg=f"폴링 실패 (HTTP {sc2})")
                log("json", obj={"http_status": sc2, "raw": raw2, "json": redact_obj(j2) if isinstance(j2, dict) else None})
                log("expander", label="🧠 원인 분석", expanded=True, blocks=[{"t": "json", "obj": analysis}])

                result_store.push("legnext", {
                    "ts": now_iso(),
                    "kind": "blocks",
                    "run_id": run_id,
                    "job_id": job_id,
                    "blocks": blocks,
                })
                return

            status = (j2.get("status") or "").lower()
            status_ph.info(f"status: {status}")
            elapsed_ratio = 1.0 - max(0.0, (deadline - time.time()) / float(max_wait))
            prog.progress(min(1.0, max(0.0, elapsed_ratio)))

            # inflight는 status 바뀔 때만 갱신 (스팸 방지)
            if status != last_status:
                result_store.update_inflight("legnext", stage="run.polling", job_id=job_id, status=status, ts=now_iso())
                last_status = status

            if status in ("completed", "succeeded"):
                out = j2.get("output") or {}
                image_urls = []
                if isinstance(out, dict):
                    image_urls = out.get("image_urls") or []

                st.success("완료!")
                log("info", msg=f"status: {status}")
                log("success", msg="완료!")

                if image_urls:
                    for u in image_urls:
                        st.image(u)
                    log("images", urls=image_urls)
                else:
                    st.json(j2)
                    log("json", obj=redact_obj(j2))

                update_run_and_touch(cfg, run_id, active_state="completed",
                                     state="completed", output_json=json_dumps_safe(redact_obj(out)))

                result_store.push("legnext", {
                    "ts": now_iso(),
                    "kind": "blocks",
                    "run_id": run_id,
                    "job_id": job_id,
                    "blocks": blocks,
                })
                return

            if status in ("failed", "error"):
                analysis = analyze_error(cfg, provider, operation, f"{legnext.LEGNEXT_BASE}/job/{job_id}",
                                         request_obj, sc2, raw2, j2, None)
                update_run(cfg, run_id,
                           gpt_analysis=json_dumps_safe(analysis),
                           state="failed",
                           error_text=f"Job failed.\n{raw2[:5000]}")

                st.error("작업 실패 상태입니다.")
                st.json(j2)
                with st.expander("🧠 원인 분석", expanded=True):
                    st.json(analysis)

                log("error", msg="작업 실패 상태입니다.")
                log("json", obj=redact_obj(j2))
                log("expander", label="🧠 원인 분석", expanded=True, blocks=[{"t": "json", "obj": analysis}])

                result_store.push("legnext", {
                    "ts": now_iso(),
                    "kind": "blocks",
                    "run_id": run_id,
                    "job_id": job_id,
                    "blocks": blocks,
                })
                return

            time.sleep(float(poll_interval))

        # timeout
        analysis = analyze_error(cfg, provider, operation, f"{legnext.LEGNEXT_BASE}/job/{job_id}",
                                 request_obj, 200, "timeout", last_json, "timeout")
        update_run(cfg, run_id,
                   gpt_analysis=json_dumps_safe(analysis),
                   state="failed",
                   error_text=f"Timeout after {max_wait}s",
                   duration_ms=int((time.time() - start_t) * 1000))

        st.warning(f"최대 대기 시간({max_wait}s)을 초과했습니다.")
        with st.expander("🧠 원인 분석", expanded=True):
            st.json(analysis)

        log("warning", msg=f"최대 대기 시간({max_wait}s)을 초과했습니다.")
        log("expander", label="🧠 원인 분석", expanded=True, blocks=[{"t": "json", "obj": analysis}])

        result_store.push("legnext", {
            "ts": now_iso(),
            "kind": "blocks",
            "run_id": run_id,
            "job_id": job_id,
            "blocks": blocks,
        })

    except Exception as e:
        err = "".join(traceback.format_exception(type(e), e, e.__traceback__))[:8000]
        st.error(str(e))

        try:
            update_run(cfg, run_id,
                       state="failed",
                       error_text=err,
                       duration_ms=int((time.time() - start_t) * 1000))
        except Exception:
            pass

        analysis = analyze_error(cfg, provider, operation, endpoint, request_obj, None, None, None, err)
        try:
            update_run(cfg, run_id, gpt_analysis=json_dumps_safe(analysis))
        except Exception:
            pass

        with st.expander("🧠 원인 분석(예외 발생)", expanded=True):
            st.json(analysis)

        log("error", msg=str(e))
        log("expander", label="🧠 원인 분석(예외 발생)", expanded=True, blocks=[{"t": "json", "obj": analysis}])

        result_store.push("legnext", {
            "ts": now_iso(),
            "kind": "blocks",
            "run_id": run_id,
            "job_id": "",
            "blocks": blocks,
        })

    finally:
        result_store.clear_inflight("legnext")
        try:
            if lease:
                release_lease(cfg, lease.lease_id)
        except Exception:
            pass
        if active_added:
            try:
                finish_run(cfg, run_id, remove_active=True,
                           duration_ms=int((time.time() - start_t) * 1000))
            except Exception:
                pass
        if hasattr(sidebar, "refresh_counts"):
            try:
                sidebar.refresh_counts()
            except Exception:
                pass


TAB = {
    "tab_id": "legnext",
    "title": "🎨 Midjourney (LegNext)",
    "required_features": {"tab.legnext"},
    "render": render_legnext_tab,
}
