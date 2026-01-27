import streamlit as st
import requests
import time
import jwt

# --- [설정] 페이지 및 Secrets 로드 ---
st.set_page_config(page_title="AI API Full-Option Tester", layout="wide")

KLING_AK = st.secrets.get("KLING_ACCESS_KEY", "")
KLING_SK = st.secrets.get("KLING_SECRET_KEY", "")
MJ_API_KEY = st.secrets.get("MJ_API_KEY", "") # Legnext API 키

# --- [함수] Kling JWT 토큰 생성 ---
def get_kling_token():
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": KLING_AK, "exp": int(time.time()) + 1800, "nbf": int(time.time()) - 5}
    return jwt.encode(payload, KLING_SK, headers=headers)

st.title("🚀 Legnext & Kling AI Multi-Tester")

tab_mj, tab_kl = st.tabs(["🎨 Midjourney (Legnext)", "🎥 Kling AI Options"])

# --- 1. Midjourney 탭 (Legnext AI 규격) ---
with tab_mj:
    st.header("Midjourney V6.1 - Legnext")
    mj_prompt = st.text_area("프롬프트 입력", placeholder="A cinematic shot...", key="mj_p_full")
    
    use_adv_mj = st.toggle("MJ 상세 파라미터 사용", value=False)
    
    # 기본값 및 파라미터 빌더
    mj_params = ""
    if use_adv_mj:
        with st.expander("🛠️ 모든 파라미터 설정", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                mj_ar = st.selectbox("화면 비율 (--ar)", ["1:1", "16:9", "9:16", "4:5", "3:2", "21:9"])
                mj_ver = st.selectbox("모델 버전 (--v)", ["6.1", "6.0", "5.2", "Niji 6"])
                mj_quality = st.select_slider("품질 (--q)", options=[0.25, 0.5, 1], value=1)
            with c2:
                mj_stylize = st.number_input("스타일 강도 (--s)", 0, 1000, 250)
                mj_chaos = st.number_input("카오스 (--c)", 0, 100, 0)
                mj_weird = st.number_input("기괴함 (--w)", 0, 3000, 0)
            with c3:
                mj_stop = st.slider("생성 중단 (--stop)", 10, 100, 100)
                mj_tile = st.checkbox("패턴 타일링 (--tile)")
                mj_raw = st.checkbox("RAW 스타일 적용")

            mj_params = f" --ar {mj_ar} --v {mj_ver} --q {mj_quality} --s {mj_stylize} --c {mj_chaos}"
            if mj_weird > 0: mj_params += f" --w {mj_weird}"
            if mj_tile: mj_params += " --tile"
            if mj_raw: mj_params += " --style raw"
            if mj_stop < 100: mj_params += f" --stop {mj_stop}"

    if st.button("Midjourney 요청 전송"):
        if not MJ_API_KEY:
            st.error("Secrets에 MJ_API_KEY를 등록해주세요.")
        else:
            # Legnext Imagine 엔드포인트
            url = "https://api.legnext.ai/v1/mj/imagine"
            headers = {
                "Authorization": f"Bearer {MJ_API_KEY}",
                "Content-Type": "application/json"
            }
            full_prompt = f"{mj_prompt}{mj_params}"
            payload = {"prompt": full_prompt} # Legnext는 보통 prompt 하나에 인자를 포함해 보냅니다.

            with st.spinner("Legnext 서버로 요청 중..."):
                try:
                    response = requests.post(url, json=payload, headers=headers)
                    if response.status_code == 200:
                        st.success("작업 제출 성공!")
                        st.json(response.json())
                    else:
                        st.error(f"오류 발생 (Status: {response.status_code})")
                        st.text(response.text)
                except Exception as e:
                    st.error(f"연결 오류: {e}")

# --- 2. Kling AI 탭 (기능 유지) ---
with tab_kl:
    st.header("Kling AI Image/Video")
    kl_prompt = st.text_area("프롬프트 입력", key="kl_p")
    kl_neg_prompt = st.text_area("제외할 프롬프트", key="kl_n_p")
    use_adv_kl = st.toggle("Kling 상세 파라미터 사용", value=False)
    
    kl_args = {}
    if use_adv_kl:
        with st.expander("🛠️ 상세 설정", expanded=True):
            k1, k2 = st.columns(2)
            with k1:
                kl_model = st.selectbox("모델", ["kling-v1", "kling-v1-pro"])
                kl_ar = st.selectbox("종횡비", ["1:1", "16:9", "9:16"])
            with k2:
                kl_cfg = st.slider("CFG Scale", 0.0, 20.0, 5.0)
                kl_step = st.slider("스텝", 10, 100, 50)
            kl_args = {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}

    is_video = st.toggle("🎥 비디오 모드")
    if st.button("Kling AI 요청"):
        if not KLING_AK or not KLING_SK:
            st.error("Kling 키를 등록해주세요.")
        else:
            token = get_kling_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            endpoint = "video/generations" if is_video else "images/generations"
            payload = {
                "model": kl_model if use_adv_kl else "kling-v1",
                "prompt": kl_prompt,
                "negative_prompt": kl_neg_prompt,
                "arguments": kl_args if use_adv_kl else {"ratio": "1:1"}
            }
            if is_video: payload["arguments"]["duration"] = "5"
            
            res = requests.post(f"https://api.klingai.com/v1/{endpoint}", headers=headers, json=payload)
            st.json(res.json())