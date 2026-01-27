import streamlit as st
import requests
import time
import jwt

# --- [설정] 페이지 및 Secrets 로드 ---
st.set_page_config(page_title="AI API Full-Option Tester", layout="wide")

# Secrets 설정 (Streamlit Cloud 설정창에서 입력 필수)
KLING_AK = st.secrets.get("KLING_ACCESS_KEY", "")
KLING_SK = st.secrets.get("KLING_SECRET_KEY", "")
LEGNEXT_API_KEY = st.secrets.get("MJ_API_KEY", "")

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

tab_mj, tab_kl = st.tabs(["🎨 Midjourney (LegNext)", "🎥 Kling AI Options"])

# --- 1. Midjourney 탭 (LegNext AI 규격 및 풀 파라미터) ---
with tab_mj:
    st.header("Midjourney V6.1 Advanced Settings")
    mj_prompt = st.text_area("프롬프트 입력", placeholder="A cinematic shot of a cyber-punk city...", key="mj_p_full")
    
    # 상세 설정 사용 여부 토글
    use_adv_mj = st.toggle("MJ 상세 파라미터 활성화", value=False, key="mj_toggle")
    
    mj_params = ""
    if use_adv_mj:
        with st.expander("🛠️ 모든 파라미터 제어 (Full Parameters)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("### 📐 Canvas & Model")
                mj_ar = st.selectbox("화면 비율 (--ar)", ["1:1", "16:9", "9:16", "4:5", "2:3", "3:2", "21:9"])
                mj_ver = st.selectbox("모델 버전 (--v)", ["6.1", "6.0", "5.2", "5.1", "Niji 6", "Niji 5"])
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

            # 파라미터 문자열 조합 로직
            mj_params = f" --ar {mj_ar} --v {mj_ver} --q {mj_quality} --s {mj_stylize} --c {mj_chaos}"
            if mj_weird > 0: mj_params += f" --w {mj_weird}"
            if mj_tile: mj_params += " --tile"
            if mj_raw: mj_params += " --style raw"
            if mj_stop < 100: mj_params += f" --stop {mj_stop}"

    if st.button("Midjourney 요청 전송 (LegNext)", key="mj_btn"):
        if not LEGNEXT_API_KEY:
            st.error("Secrets에 MJ_API_KEY(LegNext용)를 등록해주세요.")
        else:
            url = "https://api.legnext.ai/v1/mj/imagine"
            headers = {
                "Authorization": f"Bearer {LEGNEXT_API_KEY}",
                "Content-Type": "application/json"
            }
            full_prompt = f"{mj_prompt}{mj_params}"
            # LegNext 규격: 프롬프트에 모든 명령어를 포함하여 전송
            payload = {"prompt": full_prompt}

            with st.spinner("LegNext 서버로 작업 제출 중..."):
                try:
                    response = requests.post(url, json=payload, headers=headers, timeout=20)
                    if response.status_code == 200:
                        st.success("작업 제출 성공!")
                        st.json(response.json())
                    else:
                        st.error(f"API 에러 (Status: {response.status_code})")
                        st.text(f"응답 내용: {response.text}")
                except Exception as e:
                    st.error(f"연결 오류: {e}")

# --- 2. Kling AI 탭 (풀 파라미터 및 비디오 모드 통합) ---
with tab_kl:
    st.header("Kling AI Image/Video Advanced Settings")
    kl_prompt = st.text_area("프롬프트 입력", placeholder="High-end fashion photography...", key="kl_p_full")
    kl_neg_prompt = st.text_area("제외할 프롬프트 (Negative)", placeholder="low quality, blurry...")

    use_adv_kl = st.toggle("Kling 상세 파라미터 사용", value=False, key="kl_toggle")
    
    kl_args = {}
    kl_model_val = "kling-v1"

    if use_adv_kl:
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
            
            kl_args = {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}
            if kl_seed != -1: kl_args["seed"] = kl_seed
            kl_model_val = kl_model

    is_video = st.toggle("🎥 비디오 생성 모드로 전환", key="video_mode")
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
                endpoint = "video/generations" if is_video else "images/generations"
                
                payload = {
                    "model": kl_model_val,
                    "prompt": kl_prompt,
                    "negative_prompt": kl_neg_prompt,
                    "arguments": kl_args if use_adv_kl else {"ratio": "1:1"}
                }
                if is_video:
                    if "arguments" not in payload: payload["arguments"] = {}
                    payload["arguments"]["duration"] = v_duration
                    payload["arguments"]["creativity"] = v_creativity
                
                try:
                    res = requests.post(f"https://api.klingai.com/v1/{endpoint}", headers=headers, json=payload)
                    result_json = res.json()
                    if res.status_code == 200 and result_json.get("code") == 200:
                        st.success(f"작업 성공! ID: {result_json['data']['task_id']}")
                        st.json(result_json)
                    else:
                        st.error(f"오류 발생: {result_json.get('message', 'Unknown error')}")
                except Exception as e:
                    st.error(f"통신 오류: {e}")