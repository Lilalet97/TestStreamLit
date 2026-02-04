# ui/tabs/kling_tab.py
import time
import uuid
import traceback
import streamlit as st
import os

from core.config import AppConfig
from core.db import (
    guard_concurrency_or_raise, add_active_job, remove_active_job,
    insert_run, update_run, now_iso
)
from core.redact import redact_obj, json_dumps_safe
from core.analysis import analyze_error
from providers import kling
from ui.sidebar import SidebarState
from core.key_pool import acquire_lease, release_lease, consume_rpm, heartbeat
from ui import result_store   # ✅ 추가

KLING_IMAGE_ENDPOINT = "https://api.klingai.com/v1/images/generations"
KLING_VIDEO_ENDPOINT = "https://api.klingai.com/v1/video/generations"


def render_kling_tab(cfg: AppConfig, sidebar: SidebarState):
    def _get_secret(name: str) -> str:
        v = os.getenv(name)
        if v:
            return v
        try:
            return str(st.secrets.get(name, "")).strip()
        except Exception:
            return ""

    _key_pool_json = _get_secret("KEY_POOL_JSON")
    if _key_pool_json and not os.getenv("KEY_POOL_JSON"):
        os.environ["KEY_POOL_JSON"] = _key_pool_json

    use_key_pool = bool(os.environ.get("KEY_POOL_JSON"))
    # ✅ LegNext와 동일: 세션 결과 저장소 초기화
    result_store.init("kling")

    st.header("Kling AI Image/Video (안정화 + MOCK 완전 지원)")

    if (not sidebar.test_mode) and (not use_key_pool) and (not (cfg.kling_access_key and cfg.kling_secret_key)):
        st.warning("Secrets/환경변수에 KLING_ACCESS_KEY, KLING_SECRET_KEY를 설정해야 합니다.")

    kl_prompt = st.text_area("프롬프트 입력", placeholder="High-end fashion photography...", key="kl_prompt", height=120)
    kl_neg_prompt = st.text_area("제외할 프롬프트 (Negative)", placeholder="low quality, blurry...", key="kl_neg_prompt", height=80)

    use_adv_kl = st.toggle("Kling 상세 파라미터 사용", value=False, key="kl_toggle")
    kl_args = {}
    kl_model_val = "kling-v1"

    if use_adv_kl:
        with st.expander("🛠️ API 세부 파라미터 설정", expanded=True):
            k1, k2 = st.columns(2)
            with k1:
                kl_model_val = st.selectbox("엔진 모델", ["kling-v1", "kling-v1-pro"], key="kl_model")
                kl_ar = st.selectbox("종횡비 (Aspect Ratio)", ["1:1", "16:9", "9:16", "4:3", "3:4"], key="kl_ratio")
            with k2:
                kl_cfg = st.slider("CFG Scale", 0.0, 20.0, 5.0, 0.5, key="kl_cfg")
                kl_seed = st.number_input("Seed (-1이면 랜덤)", -1, 2**32, -1, key="kl_seed")
                kl_step = st.slider("샘플링 스텝", 10, 100, 50, key="kl_step")

            kl_args = {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}
            if kl_seed != -1:
                kl_args["seed"] = int(kl_seed)

    is_video = st.toggle("🎥 비디오 생성 모드", key="kl_video_mode")
    v_duration = None
    v_creativity = None
    if is_video:
        v_duration = st.radio("길이 (초)", ["5", "10"], horizontal=True, key="kl_duration")
        v_creativity = st.slider("창의성 레벨", 0, 10, 5, key="kl_creativity")

    submit = st.button("Kling API 요청", key="kl_btn", use_container_width=True)

    # ✅ LegNext처럼: submit 안 눌렀으면 “저장된 blocks”를 기존 UI처럼 재생
    if not submit:
        result_store.render(
            "kling",
            title=None,
            show_history=False,
            show_clear=False,
            show_inflight=True,
        )
        return

    provider = "kling"
    operation = "video.generate" if is_video else "image.generate"
    endpoint = KLING_VIDEO_ENDPOINT if is_video else KLING_IMAGE_ENDPOINT

    run_id = str(uuid.uuid4())
    start_t = time.time()

    request_obj = {
        "model": kl_model_val,
        "prompt": kl_prompt,
        "negative_prompt": kl_neg_prompt,
        "arguments": kl_args if use_adv_kl else {"ratio": "1:1"},
        "is_video": is_video,
        "mock": sidebar.test_mode,
        "mock_scenario": sidebar.mock_scenario if sidebar.test_mode else None,
    }

    if is_video:
        request_obj["arguments"] = dict(request_obj["arguments"])
        request_obj["arguments"]["duration"] = int(v_duration) if v_duration else 5
        request_obj["arguments"]["creativity"] = int(v_creativity) if v_creativity is not None else 5

    # ✅ blocks 기록 시작 (LegNext와 동일)
    blocks = []
    def log(t: str, **kw):
        blocks.append({"t": t, **kw})

    active_added = False
    lease = None

    try:
        guard_concurrency_or_raise(cfg)
        add_active_job(cfg, run_id, provider, operation, "running")
        active_added = True

        insert_run(cfg, {
            "run_id": run_id,
            "created_at": now_iso(),
            "user_id": st.session_state.user_id,
            "session_id": st.session_state.session_id,
            "provider": provider,
            "operation": operation,
            "endpoint": endpoint,
            "request_json": json_dumps_safe(redact_obj(request_obj)),
            "state": "running",
        })

        if not kl_prompt.strip():
            raise RuntimeError("프롬프트를 입력하세요.")

        payload = {
            "model": kl_model_val,
            "prompt": kl_prompt,
            "negative_prompt": kl_neg_prompt,
            "arguments": (kl_args if use_adv_kl else {"ratio": "1:1"}),
        }
        if is_video:
            payload["arguments"] = dict(payload["arguments"])
            payload["arguments"]["duration"] = int(v_duration) if v_duration else 5
            payload["arguments"]["creativity"] = int(v_creativity) if v_creativity is not None else 5

        # inflight 시작
        result_store.set_inflight("kling", stage="run.start", ts=now_iso(), run_id=run_id, is_video=is_video)

        wait_box = st.empty()
        last_wait_state = {"msg": ""}

        def on_wait(info):
            pos = info.get("pos")
            stt = (info.get("state") or "waiting").strip()
            reason = info.get("reason")
            retry_after = info.get("retry_after_sec")

            if stt == "waiting_turn":
                msg = f"⏳ 대기열 대기중… (내 순번: {pos})"
                wait_box.info(msg)
                result_store.update_inflight("kling", stage="run.waiting_turn", pos=pos, ts=now_iso())

            elif stt == "waiting_key":
                # reason을 함께 표시 (concurrency/mixed/no_keys 등)
                tail = f" (reason: {reason})" if reason else ""
                msg = f"⏳ 내 차례지만 사용 가능한 키가 없어 대기중… (동시성/RPM/스코프){tail}"
                wait_box.warning(msg)
                result_store.update_inflight("kling", stage="run.waiting_key", pos=pos, reason=reason, ts=now_iso())

            elif stt == "waiting_rpm":
                # ✅ key_pool에서 실제로 올라오게 됨(1-3 적용 후)
                tail = f" (약 {retry_after}s 후 재시도)" if retry_after else ""
                msg = f"⏳ RPM 제한으로 대기중…{tail}"
                wait_box.warning(msg)
                result_store.update_inflight("kling", stage="run.waiting_rpm", pos=pos, retry_after_sec=retry_after, ts=now_iso())

            else:
                # ✅ “대기 UI 뜨는 조건”을 더 넓게: 어떤 상태든 표시
                msg = f"⏳ 대기중… ({stt})"
                if pos is not None:
                    msg += f" / pos={pos}"
                wait_box.info(msg)
                result_store.update_inflight("kling", stage="run.waiting_any", status=stt, pos=pos, ts=now_iso())

        # 제출
        result_store.update_inflight("kling", stage="run.submitting", ts=now_iso())

        with st.spinner("Kling 작업 제출 중..."):
            if use_key_pool:
                result_store.update_inflight("kling", stage="run.acquire_lease", ts=now_iso())
                wait_box.info("⏳ 키 풀에서 키를 할당받는 중…")
                lease = acquire_lease(
                    cfg,
                    provider="kling",
                    run_id=run_id,
                    user_id=st.session_state.user_id,
                    session_id=st.session_state.session_id,
                    school_id=st.session_state.get("school_id", "default"),
                    wait=True,
                    max_wait_sec=60,
                    poll_interval_sec=1.0,
                    request_units=1,
                    on_wait=on_wait,
                )

                ak = (lease.key_payload or {}).get("access_key", "")
                sk = (lease.key_payload or {}).get("secret_key", "")
                if not (ak and sk):
                    raise RuntimeError("키 풀에서 kling access/secret을 얻지 못했습니다. KEY_POOL_JSON/시드 설정을 확인하세요.")

                result_store.update_inflight(
                    "kling",
                    stage="run.lease_acquired",
                    lease_id=lease.lease_id,
                    api_key_id=getattr(lease, "api_key_id", None),
                    ts=now_iso(),
                )
                msg = "키 확보 완료. 작업 진행합니다."
            else:
                # ✅ 키풀이 없으면(테스트모드도 동일하게) secrets 키를 요구
                ak = cfg.kling_access_key or ""
                sk = cfg.kling_secret_key or ""
                if not (ak and sk):
                    raise RuntimeError("KEY_POOL_JSON이 없고 KLING_ACCESS_KEY/KLING_SECRET_KEY도 없습니다. 설정을 확인하세요.")
                msg = "키(Secrets) 확인 완료. 작업 진행합니다."

            wait_box.success(msg)
            log("success", msg=msg)

            # ✅ 비용 발생하는 실제 API 호출만 mock으로 대체
            if sidebar.test_mode:
                sc, raw, j = kling.mock_submit(is_video=is_video, scenario=sidebar.mock_scenario)
            else:
                if is_video:
                    sc, raw, j = kling.submit_video(ak, sk, endpoint, payload)
                else:
                    sc, raw, j = kling.submit_image(ak, sk, endpoint, payload)

        update_run(cfg, run_id,
                   http_status=sc,
                   response_text=raw,
                   response_json=json_dumps_safe(redact_obj(j)) if isinstance(j, (dict, list)) else None)

        if sc != 200:
            st.error(f"HTTP 오류: {sc}")
            st.text(raw)
            log("error", msg=f"HTTP 오류: {sc}")
            log("code", body=raw)

            analysis = analyze_error(cfg, provider, operation, endpoint, request_obj, sc, raw, j if isinstance(j, dict) else None, None)
            update_run(cfg, run_id, gpt_analysis=json_dumps_safe(analysis), state="failed", error_text=f"HTTP {sc}\n{raw[:5000]}")
            with st.expander("🧠 원인 분석(Kling HTTP 오류)", expanded=True):
                st.json(analysis)
            log("expander", label="🧠 원인 분석(Kling HTTP 오류)", expanded=True, blocks=[{"t": "json", "obj": analysis}])

            result_store.push("kling", {
                "ts": now_iso(),
                "kind": "blocks",
                "run_id": run_id,
                "job_id": "",
                "blocks": blocks,
            })
            return

        # ✅ 성공 판정(원본 유지) + blocks 저장
        if isinstance(j, dict) and j.get("code") == 200:
            data = j.get("data") or {}
            task_id = data.get("task_id", "")

            st.success(f"작업 성공! ID: {task_id}")
            st.json(j)
            log("success", msg=f"작업 성공! ID: {task_id}")
            log("json", obj=redact_obj(j))

            update_run(cfg, run_id, state="completed", job_id=task_id, output_json=json_dumps_safe(redact_obj(j)))
            result_store.update_inflight("kling", stage="run.completed", job_id=task_id, ts=now_iso())

            # 결과 표시(이미지/비디오) + blocks에도 기록
            if is_video and data.get("video_url"):
                st.video(data["video_url"])
                log("video", url=data["video_url"])
            if (not is_video) and data.get("image_url"):
                st.image(data["image_url"])
                log("images", urls=[data["image_url"]])

            # ✅ rerun 시에도 동일 UI 재생되도록 blocks 저장
            result_store.push("kling", {
                "ts": now_iso(),
                "kind": "blocks",
                "run_id": run_id,
                "job_id": task_id,
                "blocks": blocks,
            })
            return

        # Non-success
        st.warning("응답은 받았지만 success 조건이 다릅니다. 응답을 확인하세요.")
        st.json(j if j is not None else {"raw": raw})
        log("warning", msg="응답은 받았지만 success 조건이 다릅니다. 응답을 확인하세요.")
        log("json", obj=redact_obj(j) if isinstance(j, dict) else {"raw": raw})

        analysis = analyze_error(cfg, provider, operation, endpoint, request_obj, sc, raw, j if isinstance(j, dict) else None, None)
        update_run(cfg, run_id, gpt_analysis=json_dumps_safe(analysis), state="failed", error_text=f"Non-success response.\n{raw[:5000]}")
        with st.expander("🧠 원인 분석(Kling Non-success)", expanded=True):
            st.json(analysis)
        log("expander", label="🧠 원인 분석(Kling Non-success)", expanded=True, blocks=[{"t": "json", "obj": analysis}])

        result_store.push("kling", {
            "ts": now_iso(),
            "kind": "blocks",
            "run_id": run_id,
            "job_id": "",
            "blocks": blocks,
        })

    except Exception as e:
        err = "".join(traceback.format_exception(type(e), e, e.__traceback__))[:8000]
        st.error(str(e))
        log("error", msg=str(e))
        log("code", body=err)

        try:
            update_run(cfg, run_id, state="failed", error_text=err, duration_ms=int((time.time() - start_t) * 1000))
        except Exception:
            pass

        analysis = analyze_error(cfg, provider, operation, endpoint, request_obj, None, None, None, err)
        try:
            update_run(cfg, run_id, gpt_analysis=json_dumps_safe(analysis))
        except Exception:
            pass

        with st.expander("🧠 원인 분석(Kling 예외)", expanded=True):
            st.json(analysis)
        log("expander", label="🧠 원인 분석(Kling 예외)", expanded=True, blocks=[{"t": "json", "obj": analysis}])

        result_store.push("kling", {
            "ts": now_iso(),
            "kind": "blocks",
            "run_id": run_id,
            "job_id": "",
            "blocks": blocks,
        })

    finally:
        try:
            update_run(cfg, run_id, duration_ms=int((time.time() - start_t) * 1000))
        except Exception:
            pass

        # ✅ inflight 정리
        result_store.clear_inflight("kling")

        if lease:
            release_lease(cfg, lease.lease_id)
        if active_added:
            remove_active_job(cfg, run_id)
        if hasattr(sidebar, "refresh_counts"):
            sidebar.refresh_counts()


TAB = {
    "tab_id": "kling",
    "title": "🎥 Kling AI Options",
    "required_features": {"tab.kling"},
    "render": render_kling_tab,
}
