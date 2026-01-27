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
    st.header("Midjourney V6.1")
    mj_prompt = st.text_area("프롬프트 입력", placeholder="A cinematic shot...", key="mj_p_full")
    
    # [추가] 상세 설정 토글
    use_adv_mj = st.toggle("MJ 상세 파라미터 사용", value=False, key="mj_toggle")
    
    # 기본값 설정
    mj_params = ""
    mj_ar_val = "1:1"
    mj_mode_val = "fast"

    if use_adv_mj:
        with st.expander("🛠️ 모든 파라미터 설정", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("### 📐 Canvas & Model")
                mj_ar = st.selectbox("화면 비율 (--ar)", ["1:1", "16:9", "9:16", "4:5", "2:3", "3:2", "21:9"])
                mj_ver = st.selectbox("모델 버전 (--v)", ["6.1", "6.0", "5.2", "5.1", "Niji 6", "Niji 5"])
                mj_quality = st.select_slider("품질 (--q)", options=[0.25, 0.5, 1], value=1)
            with c2:
                st.markdown("### 🎨 Artistic Control")
                mj_stylize = st.number_input("스타일 강도 (--s)", 0, 1000, 250, step=50)
                mj_chaos = st.number_input("카오스 (--c)", 0, 100, 0)
                mj_weird = st.number_input("기괴함 (--w)", 0, 3000, 0, step=100)
            with c3:
                st.markdown("### ⚙️ Generation Mode")
                mj_stop = st.slider("생성 중단 시점 (--stop)", 10, 100, 100)
                mj_tile = st.checkbox("패턴 타일링 (--tile)")
                mj_raw = st.checkbox("RAW 스타일 적용")
                mj_fast = st.radio("생성 속도", ["fast", "relax", "turbo"], horizontal=True)
            
            # 파라미터 문자열 조합
            mj_params = f" --ar {mj_ar} --v {mj_ver} --q {mj_quality} --s {mj_stylize} --c {mj_chaos}"
            if mj_weird > 0: mj_params += f" --w {mj_weird}"
            if mj_tile: mj_params += " --tile"
            if mj_raw: mj_params += " --style raw"
            if mj_stop < 100: mj_params += f" --stop {mj_stop}"
            mj_ar_val = mj_ar
            mj_mode_val = mj_fast

    if st.button("Midjourney API 요청", key="mj_btn"):
        if not MJ_API_KEY:
            st.error("Secrets에 MJ_API_KEY를 등록해주세요.")
        else:
            full_prompt = f"{mj_prompt}{mj_params}"
            with st.spinner("Midjourney 작업 제출 중..."):
                url = "https://api.goapi.ai/mj/v6/imagine"
                headers = {"X-API-KEY": MJ_API_KEY, "Content-Type": "application/json"}
                payload = {"prompt": full_prompt, "aspect_ratio": mj_ar_val, "process_mode": mj_mode_val}
                
                # [수정된 부분] 응답을 바로 .json()으로 바꾸지 않고 변수에 저장
                response = requests.post(url, json=payload, headers=headers)
                
                # 상태 코드가 200(성공)인지 확인
                if response.status_code == 200:
                    try:
                        result = response.json()
                        st.json(result)
                    except Exception as e:
                        st.error(f"JSON 변환 오류: {e}")
                        st.text(f"서버 응답 내용: {response.text}")
                else:
                    # 성공이 아닐 경우 에러 코드와 실제 응답 내용을 보여줌
                    st.error(f"API 요청 실패 (Status Code: {response.status_code})")
                    st.text(f"상세 에러 내용: {response.text}")

# --- 2. Kling AI 탭 ---
with tab_kl:
    st.header("Kling AI Image/Video")
    kl_prompt = st.text_area("프롬프트 입력", key="kl_p_full")
    kl_neg_prompt = st.text_area("제외할 프롬프트 (Negative)", key="kl_n_p")

    # [추가] 상세 설정 토글
    use_adv_kl = st.toggle("Kling 상세 파라미터 사용", value=False, key="kl_toggle")
    
    # 기본값
    kl_args = {}
    kl_model_val = "kling-v1"

    if use_adv_kl:
        with st.expander("🛠️ API 세부 파라미터 설정", expanded=True):
            k1, k2 = st.columns(2)
            with k1:
                kl_model = st.selectbox("엔진 모델", ["kling-v1", "kling-v1-pro"])
                kl_ar = st.selectbox("종횡비", ["1:1", "16:9", "9:16", "4:3", "3:4"])
                kl_num = st.number_input("생성 개수", 1, 9, 1)
            with k2:
                kl_cfg = st.slider("CFG Scale", 0.0, 20.0, 5.0, 0.5)
                kl_seed = st.number_input("시드 번호", -1, 2**32, -1)
                kl_step = st.slider("샘플링 스텝", 10, 100, 50)
            
            kl_args = {"ratio": kl_ar, "cfg_scale": kl_cfg, "step": kl_step}
            if kl_seed != -1: kl_args["seed"] = kl_seed
            kl_model_val = kl_model

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
                endpoint = "video/generations" if is_video else "images/generations"
                
                payload = {
                    "model": kl_model_val,
                    "prompt": kl_prompt,
                    "negative_prompt": kl_neg_prompt,
                    "arguments": kl_args if use_adv_kl else {"ratio": "1:1"}
                }
                if is_video: payload["arguments"]["duration"] = v_duration
                
                res = requests.post(f"https://api.klingai.com/v1/{endpoint}", headers=headers, json=payload).json()
                
                if res.get("code") == 200:
                    st.success(f"작업 성공! ID: {res['data']['task_id']}")
                    st.json(res)
                else:
                    st.error(f"오류 발생: {res.get('message')}")