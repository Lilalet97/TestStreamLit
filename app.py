import streamlit as st
import requests
import time
import jwt

# --- [설정] 페이지 및 Secrets 로드 ---
st.set_page_config(page_title="AI API Full-Option Tester", layout="wide")

KLING_AK = st.secrets.get("KLING_ACCESS_KEY", "")
KLING_SK = st.secrets.get("KLING_SECRET_KEY", "")
MJ_API_KEY = st.secrets.get("MJ_API_KEY", "")

# --- [함수] Kling JWT 토큰 생성 ---
def get_kling_token():
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": KLING_AK,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, KLING_SK, headers=headers)

st.title("🚀 Generative AI Multi-API Full Tester")

tab_mj, tab_kl = st.tabs(["🎨 Midjourney Full Options", "🎥 Kling AI Full Options"])

# --- 1. Midjourney 탭 ---
with tab_mj:
    st.header("Midjourney V6.1 Advanced Settings")
    mj_prompt = st.text_area("프롬프트 입력", placeholder="A cinematic shot of a cyber-punk city...", key="mj_p_full")
    
    with st.expander("🛠️ 모든 파라미터 설정 (Parameter Control)", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("### 📐 Canvas & Model")
            mj_ar = st.selectbox("화면 비율 (--ar)", ["1:1", "16:9", "9:16", "4:5", "2:3", "3:2", "21:9"])
            mj_ver = st.selectbox("모델 버전 (--v / --niji)", ["6.1", "6.0", "5.2", "5.1", "Niji 6", "Niji 5"])
            mj_quality = st.select_slider("품질 (--q)", options=[0.25, 0.5, 1], value=1)
        with c2:
            st.markdown("### 🎨 Artistic Control")
            mj_stylize = st.number_input("스타일 강도 (--s)", 0, 1000, 250, step=50)
            mj_chaos = st.number_input("카오스 (다양성, --c)", 0, 100, 0)
            mj_weird = st.number_input("기괴함 (--w)", 0, 3000, 0, step=100)
        with c3:
            st.markdown("### ⚙️ Generation Mode")
            mj_stop = st.slider("생성 중단 시점 (--stop)", 10, 100, 100)
            mj_tile = st.checkbox("패턴 타일링 (--tile)")
            mj_raw = st.checkbox("RAW 스타일 적용 (--style raw)")
            mj_fast = st.radio("생성 속도", ["fast", "relax", "turbo"], horizontal=True)

    if st.button("Midjourney API 요청", key="mj_btn"):
        if not MJ_API_KEY:
            st.error("Secrets에 MJ_API_KEY를 등록해주세요.")
        else:
            # 파라미터 문자열 조합 (실제 프롬프트 뒤에 붙임)
            full_prompt = f"{mj_prompt} --ar {mj_ar} --v {mj_ver} --q {mj_quality} --s {mj_stylize} --c {mj_chaos}"
            if mj_weird > 0: full_prompt += f" --w {mj_weird}"
            if mj_tile: full_prompt += " --tile"
            if mj_raw: full_prompt += " --style raw"
            if mj_stop < 100: full_prompt += f" --stop {mj_stop}"

            with st.spinner("Midjourney 작업 제출 중..."):
                url = "https://api.goapi.ai/mj/v6/imagine" # GoAPI 예시
                headers = {"X-API-KEY": MJ_API_KEY, "Content-Type": "application/json"}
                payload = {"prompt": full_prompt, "aspect_ratio": mj_ar, "process_mode": mj_fast}
                
                response = requests.post(url, json=payload, headers=headers).json()
                st.json(response)

# --- 2. Kling AI 탭 ---
with tab_kl:
    st.header("Kling AI Image/Video Advanced Settings")
    kl_prompt = st.text_area("프롬프트 입력", placeholder="High-end fashion photography...", key="kl_p_full")
    kl_neg_prompt = st.text_area("제외할 프롬프트 (Negative)", placeholder="low quality, blurry, distorted...")

    with st.expander("🛠️ API 세부 파라미터 설정", expanded=True):
        k1, k2 = st.columns(2)
        with k1:
            st.markdown("### 🖼️ Image/Video Spec")
            kl_model = st.selectbox("엔진 모델", ["kling-v1", "kling-v1-pro"])
            kl_ar = st.selectbox("종횡비 (Aspect Ratio)", ["1:1", "16:9", "9:16", "4:3", "3:4"])
            kl_num = st.number_input("생성 개수", 1, 9, 1)
        with k2:
            st.markdown("### 🕹️ 제어 파라미터")
            kl_cfg = st.slider("프롬프트 일치도 (CFG Scale)", 0.0, 20.0, 5.0, 0.5)
            kl_seed = st.number_input("시드 번호 (Seed)", -1, 2**32, -1)
            kl_step = st.slider("샘플링 스텝", 10, 100, 50)

    is_video = st.toggle("🎥 비디오 생성 모드로 전환")
    if is_video:
        v_duration = st.radio("길이 (초)", ["5", "10"], horizontal=True)
        v_creativity = st.slider("창의성 레벨", 0, 10, 5)

    if st.button("Kling AI API 요청", key="kl_btn"):
        if not KLING_AK or not KLING_SK:
            st.error("Secrets에 Kling API 키를 등록해주세요.")
        else:
            with st.spinner("Kling 작업 제출 중..."):
                token = get_kling_token()
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                
                # 엔드포인트 구분 (이미지 vs 비디오)
                endpoint = "video/generations" if is_video else "images/generations"
                url = f"https://api.klingai.com/v1/{endpoint}"
                
                payload = {
                    "model": kl_model,
                    "prompt": kl_prompt,
                    "negative_prompt": kl_neg_prompt,
                    "arguments": {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}
                }
                if is_video: payload["arguments"]["duration"] = v_duration
                
                res = requests.post(url, headers=headers, json=payload).json()
                
                if res.get("code") == 200:
                    task_id = res["data"]["task_id"]
                    st.success(f"작업 성공! ID: {task_id}")
                    # 여기서부터는 이전의 Poling(결과 대기) 로직을 추가하여 이미지를 출력할 수 있습니다.
                    st.json(res)
                else:
                    st.error(f"오류 발생: {res.get('message')}")