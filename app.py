import streamlit as st
import requests
import time
import jwt

# --- [설정] 페이지 및 Secrets 로드 ---
st.set_page_config(page_title="AI API Hybrid Tester", layout="wide")

KLING_AK = st.secrets.get("KLING_ACCESS_KEY", "")
KLING_SK = st.secrets.get("KLING_SECRET_KEY", "")
MJ_API_KEY = st.secrets.get("MJ_API_KEY", "")

# --- [함수] Kling JWT 토큰 생성 ---
def get_kling_token():
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": KLING_AK, "exp": int(time.time()) + 1800, "nbf": int(time.time()) - 5}
    return jwt.encode(payload, KLING_SK, headers=headers)

st.title("🚀 Generative AI Hybrid API Tester")

tab_mj, tab_kl = st.tabs(["🎨 Midjourney", "🎥 Kling AI"])

# --- 1. Midjourney 탭 ---
with tab_mj:
    st.header("Midjourney V6.1")
    mj_prompt = st.text_area("프롬프트 입력", placeholder="A cinematic shot...", key="mj_p")
    
    # 상세 설정 사용 여부 토글
    use_advanced_mj = st.toggle("상세 파라미터 활성화 (Advanced Settings)", value=False)
    
    params = "" # 기본값은 빈 문자열
    process_mode = "fast"
    
    if use_advanced_mj:
        with st.expander("🛠️ 세부 파라미터 설정", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                mj_ar = st.selectbox("화면 비율 (--ar)", ["1:1", "16:9", "9:16", "4:5", "3:2"])
                mj_ver = st.selectbox("모델 버전 (--v)", ["6.1", "6.0", "5.2", "Niji 6"])
            with c2:
                mj_stylize = st.number_input("스타일 강도 (--s)", 0, 1000, 250)
                mj_chaos = st.number_input("카오스 (--c)", 0, 100, 0)
            with c3:
                mj_fast = st.radio("속도", ["fast", "relax", "turbo"], horizontal=True)
                process_mode = mj_fast
            
            # 파라미터 문자열 조립
            params = f" --ar {mj_ar} --v {mj_ver} --s {mj_stylize} --c {mj_chaos}"

    if st.button("Midjourney 요청", key="mj_btn"):
        full_prompt = f"{mj_prompt}{params}"
        st.info(f"전송되는 최종 프롬프트: {full_prompt}")
        # API 호출 로직 (생략 - 이전과 동일)

# --- 2. Kling AI 탭 ---
with tab_kl:
    st.header("Kling AI")
    kl_prompt = st.text_area("프롬프트 입력", key="kl_p")
    
    # 상세 설정 사용 여부 토글
    use_advanced_kl = st.toggle("상세 파라미터 활성화", value=False)
    
    # 기본 페이로드 설정
    payload_args = {} 
    
    if use_advanced_kl:
        with st.expander("🛠️ API 세부 파라미터 설정", expanded=True):
            k1, k2 = st.columns(2)
            with k1:
                kl_model = st.selectbox("엔진 모델", ["kling-v1", "kling-v1-pro"])
                kl_ar = st.selectbox("종횡비", ["1:1", "16:9", "9:16"])
            with k2:
                kl_cfg = st.slider("CFG Scale", 0.0, 20.0, 5.0)
                kl_step = st.slider("스텝", 10, 100, 50)
            
            # 토글이 켜졌을 때만 페이로드에 상세 인자 추가
            payload_args = {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}

    if st.button("Kling AI 요청", key="kl_btn"):
        # API 요청 구조 생성
        final_payload = {
            "model": kl_model if use_advanced_kl else "kling-v1",
            "prompt": kl_prompt
        }
        if payload_args: # 상세 인자가 있을 때만 추가
            final_payload["arguments"] = payload_args
            
        st.json(final_payload)
        # API 호출 로직 (생략 - 이전과 동일)