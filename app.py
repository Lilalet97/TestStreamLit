import os
import time
import json
import streamlit as st
import requests
import jwt

# ----------------------------
# Page / Secrets
# ----------------------------
st.set_page_config(page_title="Generative AI Multi-API Full Tester", layout="wide")

LEGNEXT_API_KEY = st.secrets.get("MJ_API_KEY", "") or os.getenv("MJ_API_KEY", "")
KLING_AK = st.secrets.get("KLING_ACCESS_KEY", "") or os.getenv("KLING_ACCESS_KEY", "")
KLING_SK = st.secrets.get("KLING_SECRET_KEY", "") or os.getenv("KLING_SECRET_KEY", "")

# ----------------------------
# HTTP helpers
# ----------------------------
def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return None

def http_post_json(url: str, headers: dict, payload: dict, timeout: int = 30):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        return r.status_code, r.text, _safe_json(r)
    except Exception as e:
        return -1, str(e), None

def http_get_json(url: str, headers: dict, timeout: int = 30):
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return r.status_code, r.text, _safe_json(r)
    except Exception as e:
        return -1, str(e), None

# ----------------------------
# LegNext (Midjourney) API
# Docs: POST /api/v1/diffusion, GET /api/v1/job/{job_id}
# ----------------------------
LEGNEXT_BASE = "https://api.legnext.ai/api/v1"

def legnext_submit(text: str, api_key: str, callback: str | None = None):
    url = f"{LEGNEXT_BASE}/diffusion"
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    payload = {"text": text}
    if callback:
        payload["callback"] = callback
    return http_post_json(url, headers, payload, timeout=30)

def legnext_get_job(job_id: str, api_key: str):
    url = f"{LEGNEXT_BASE}/job/{job_id}"
    headers = {"x-api-key": api_key}
    return http_get_json(url, headers, timeout=30)

def legnext_is_error_obj(j: dict | None) -> bool:
    # 에러 응답 예: {"code":401,"message":"..."}
    return isinstance(j, dict) and ("code" in j and "message" in j) and ("job_id" not in j)

def legnext_poll(job_id: str, api_key: str, max_wait_sec: int, interval_sec: float):
    """
    Returns: (final_json, last_status_code, last_raw_text)
    """
    start = time.time()
    last = None
    last_sc = None
    last_raw = None

    while True:
        sc, raw, j = legnext_get_job(job_id, api_key)
        last, last_sc, last_raw = j, sc, raw

        # 네트워크/파싱 실패
        if sc == -1:
            return last, last_sc, last_raw

        # LegNext 에러 오브젝트
        if legnext_is_error_obj(j):
            return j, sc, raw

        status = (j or {}).get("status", "")
        if status in ("completed", "failed"):
            return j, sc, raw

        if time.time() - start >= max_wait_sec:
            return j, sc, raw

        time.sleep(interval_sec)

# ----------------------------
# Kling JWT token (as you had)
# ----------------------------
def get_kling_token():
    headers = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "iss": KLING_AK,
        "exp": now + 1800,
        "nbf": now - 5
    }
    token = jwt.encode(payload, KLING_SK, headers=headers)
    # pyjwt 버전에 따라 bytes가 올 수 있어 안전 처리
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token

# ----------------------------
# UI
# ----------------------------
st.title("🚀 Generative AI Multi-API Full Tester")

tab_mj, tab_kl = st.tabs(["🎨 Midjourney (LegNext) - 완성형", "🎥 Kling AI Options"])

# =========================================================
# 1) Midjourney (LegNext) Tab
# =========================================================
with tab_mj:
    st.header("Midjourney via LegNext (Text → Image, Submit → Poll → Display)")

    if not LEGNEXT_API_KEY:
        st.warning("Secrets 또는 환경변수에 MJ_API_KEY(=LegNext API Key)를 설정해야 합니다.")

    colA, colB = st.columns([2, 1])

    with colA:
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

    with colB:
        st.markdown("### ⚙️ 실행 옵션")
        auto_poll = st.toggle("제출 후 자동 폴링", value=True, key="mj_auto_poll")
        poll_interval = st.slider("폴링 간격(초)", 1.0, 10.0, 2.0, 0.5, key="mj_poll_interval")
        max_wait = st.slider("최대 대기(초)", 10, 300, 120, 10, key="mj_max_wait")

        st.markdown("---")
        st.markdown("### 🔎 기존 job_id 조회")
        existing_job_id = st.text_input("job_id 입력", key="mj_existing_job_id")
        if st.button("상태 조회", key="mj_check_btn"):
            if not LEGNEXT_API_KEY:
                st.error("MJ_API_KEY가 없습니다.")
            elif not existing_job_id.strip():
                st.error("job_id를 입력하세요.")
            else:
                sc, raw, j = legnext_get_job(existing_job_id.strip(), LEGNEXT_API_KEY)
                if sc == 200 and isinstance(j, dict) and j.get("job_id"):
                    st.success(f"조회 성공 (status: {j.get('status')})")
                    st.json(j)
                    st.session_state["last_job_id"] = j.get("job_id")
                else:
                    st.error(f"조회 실패 (HTTP {sc})")
                    st.text(raw)

    st.markdown("---")
    submit_col1, submit_col2 = st.columns([1, 2])

    with submit_col1:
        submit = st.button("🚀 LegNext로 생성 요청(제출)", key="mj_submit_btn", use_container_width=True)

    with submit_col2:
        st.caption("LegNext는 비동기 방식이라, 제출 후 job_id를 받아서 완료될 때까지 상태 조회가 필요합니다. (completed/failed)")

    if submit:
        if not LEGNEXT_API_KEY:
            st.error("Secrets/환경변수에 MJ_API_KEY(=LegNext API Key)를 등록해주세요.")
        elif not mj_prompt.strip():
            st.error("프롬프트를 입력하세요.")
        else:
            full_text = f"{mj_prompt}{mj_params}"
            st.info("요청 텍스트(프롬프트+옵션) 미리보기")
            st.code(full_text)

            with st.spinner("LegNext에 작업 제출 중..."):
                sc, raw, j = legnext_submit(full_text, LEGNEXT_API_KEY)

            if sc != 200 or not isinstance(j, dict) or legnext_is_error_obj(j) or not j.get("job_id"):
                st.error(f"제출 실패 (HTTP {sc})")
                st.text(raw)
                if isinstance(j, dict):
                    st.json(j)
            else:
                job_id = j["job_id"]
                st.session_state["last_job_id"] = job_id
                st.success(f"제출 성공! job_id = {job_id}")
                st.json(j)

                if auto_poll:
                    st.markdown("### ⏳ 자동 폴링 진행")
                    status_box = st.empty()
                    prog = st.progress(0)

                    # 폴링 루프(진행 표시)
                    start_t = time.time()
                    final_j = None
                    last_sc = None
                    last_raw = None

                    while True:
                        elapsed = time.time() - start_t
                        pct = min(int((elapsed / max_wait) * 100), 100)
                        prog.progress(pct)

                        sc2, raw2, j2 = legnext_get_job(job_id, LEGNEXT_API_KEY)
                        last_sc, last_raw, final_j = sc2, raw2, j2

                        if sc2 == -1:
                            status_box.error(f"통신 오류: {raw2}")
                            break

                        if legnext_is_error_obj(j2):
                            status_box.error(f"에러 응답(HTTP {sc2}): {j2.get('message')}")
                            st.json(j2)
                            break

                        status = (j2 or {}).get("status", "unknown")
                        status_box.info(f"현재 상태: **{status}** (대기 {int(elapsed)}s / 최대 {max_wait}s)")

                        if status in ("completed", "failed"):
                            break

                        if elapsed >= max_wait:
                            status_box.warning("최대 대기 시간을 초과했습니다. '기존 job_id 조회'로 다시 확인하세요.")
                            break

                        time.sleep(poll_interval)

                    # 결과 출력
                    if isinstance(final_j, dict) and final_j.get("job_id"):
                        st.markdown("### 📦 최종 Job 결과")
                        st.json(final_j)

                        if final_j.get("status") == "completed":
                            output = final_j.get("output") or {}
                            urls = output.get("image_urls") or []
                            single = output.get("image_url")

                            st.markdown("### 🖼️ 결과 이미지")
                            if urls:
                                st.image(urls, caption=[f"Image {i}" for i in range(len(urls))], use_container_width=True)
                                st.markdown("#### 이미지 URL 목록")
                                st.code("\n".join(urls))
                            elif single:
                                st.image(single, caption="Image", use_container_width=True)
                                st.code(single)
                            else:
                                st.warning("completed 이지만 image_urls/image_url이 비어 있습니다. 잠시 후 job_id로 재조회해보세요.")
                        elif final_j.get("status") == "failed":
                            err = final_j.get("error") or {}
                            st.error(f"작업 실패: {err.get('message') or 'Unknown error'}")
                else:
                    st.info("자동 폴링이 꺼져 있습니다. 우측 '기존 job_id 조회'에서 job_id로 확인하세요.")

    # 마지막 job 빠른 조회
    if "last_job_id" in st.session_state and st.session_state["last_job_id"]:
        st.markdown("---")
        st.markdown("### 🧾 마지막 job_id 빠른 액세스")
        st.code(st.session_state["last_job_id"])


# =========================================================
# 2) Kling Tab (기존 코드 기반 안정화)
# =========================================================
with tab_kl:
    st.header("Kling AI Image/Video (현재 구현 유지 + 안정화)")

    if not (KLING_AK and KLING_SK):
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
                kl_model_val = st.selectbox("엔진 모델", ["kling-v1", "kling-v1-pro"])
                kl_ar = st.selectbox("종횡비 (Aspect Ratio)", ["1:1", "16:9", "9:16", "4:3", "3:4"])
            with k2:
                kl_cfg = st.slider("CFG Scale", 0.0, 20.0, 5.0, 0.5)
                kl_seed = st.number_input("Seed (-1이면 랜덤)", -1, 2**32, -1)
                kl_step = st.slider("샘플링 스텝", 10, 100, 50)

            kl_args = {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}
            if kl_seed != -1:
                kl_args["seed"] = int(kl_seed)

    is_video = st.toggle("🎥 비디오 생성 모드", key="kl_video_mode")
    v_duration = None
    v_creativity = None
    if is_video:
        v_duration = st.radio("길이 (초)", ["5", "10"], horizontal=True, key="kl_duration")
        v_creativity = st.slider("창의성 레벨", 0, 10, 5, key="kl_creativity")

    if st.button("Kling API 요청", key="kl_btn"):
        if not (KLING_AK and KLING_SK):
            st.error("Kling 키가 없습니다. Secrets/환경변수를 확인하세요.")
        elif not kl_prompt.strip():
            st.error("프롬프트를 입력하세요.")
        else:
            with st.spinner("Kling 작업 제출 중..."):
                token = get_kling_token()
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                endpoint = "video/generations" if is_video else "images/generations"

                payload = {
                    "model": kl_model_val,
                    "prompt": kl_prompt,
                    "negative_prompt": kl_neg_prompt,
                    "arguments": (kl_args if use_adv_kl else {"ratio": "1:1"}),
                }

                if is_video:
                    # 타입 안전: duration은 숫자를 기대하는 API가 많아서 int로 보내는 편이 안전
                    payload["arguments"]["duration"] = int(v_duration) if v_duration else 5
                    payload["arguments"]["creativity"] = int(v_creativity) if v_creativity is not None else 5

                sc, raw, j = http_post_json(f"https://api.klingai.com/v1/{endpoint}", headers, payload, timeout=60)

                if sc != 200:
                    st.error(f"HTTP 오류: {sc}")
                    st.text(raw)
                else:
                    # Kling 응답 스펙에 따라 code/message가 있을 수 있음
                    if isinstance(j, dict) and j.get("code") == 200:
                        st.success(f"작업 성공! ID: {j.get('data', {}).get('task_id', '')}")
                        st.json(j)
                    else:
                        st.warning("응답은 받았지만 success 조건이 다릅니다. 응답을 확인하세요.")
                        st.json(j if j is not None else {"raw": raw})
