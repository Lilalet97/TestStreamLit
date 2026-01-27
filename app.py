import streamlit as st
import requests
import time
import jwt

# --- [공통] API 키 불러오기 ---
# Streamlit Cloud의 Secrets 메뉴에 아래 키들을 등록해야 합니다.
KLING_AK = st.secrets.get("KLING_ACCESS_KEY", "")
KLING_SK = st.secrets.get("KLING_SECRET_KEY", "")
MJ_API_KEY = st.secrets.get("MJ_API_KEY", "") # GoAPI 등 서드파티용

# --- [함수] Kling JWT 토큰 생성 ---
def get_kling_token():
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": KLING_AK,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, KLING_SK, headers=headers)

st.title("🚀 AI API Production Tester")

tab_mj, tab_kl = st.tabs(["🎨 Midjourney", "🎥 Kling AI"])

# --- 1. Midjourney 연결 (GoAPI 예시) ---
with tab_mj:
    mj_prompt = st.text_area("MJ 프롬프트", key="mj_p")
    if st.button("MJ 이미지 생성 요청"):
        if not MJ_API_KEY:
            st.error("Secrets에 MJ_API_KEY를 등록해주세요.")
        else:
            url = "https://api.goapi.ai/mj/v6/imagine" # 예시 URL
            headers = {"X-API-KEY": MJ_API_KEY, "Content-Type": "application/json"}
            payload = {"prompt": mj_prompt, "aspect_ratio": "16:9"} # UI 설정값 연결 가능
            
            res = requests.post(url, json=payload, headers=headers).json()
            st.json(res) # 결과 확인용

# --- 2. Kling AI 연결 (이미지 생성 상세) ---
with tab_kl:
    kl_prompt = st.text_area("Kling 프롬프트", key="kl_p")
    if st.button("Kling 이미지 생성 시작"):
        if not KLING_AK or not KLING_SK:
            st.error("Secrets에 Kling 키들을 등록해주세요.")
        else:
            token = get_kling_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            
            # [Step 1] 작업 제출
            submit_res = requests.post(
                "https://api.klingai.com/v1/images/generations",
                headers=headers,
                json={"model": "kling-v1", "prompt": kl_prompt}
            ).json()
            
            if submit_res.get("code") == 200:
                task_id = submit_res["data"]["task_id"]
                st.info(f"작업 제출 성공 (ID: {task_id})")
                
                # [Step 2] 폴링 (상태 확인)
                placeholder = st.empty()
                while True:
                    check_res = requests.get(
                        f"https://api.klingai.com/v1/images/generations/{task_id}",
                        headers=headers
                    ).json()
                    status = check_res["data"]["task_status"]
                    placeholder.write(f"현재 상태: {status}...")
                    
                    if status == "succeed":
                        img_url = check_res["data"]["task_result"]["images"][0]["url"]
                        st.image(img_url)
                        break
                    elif status == "failed":
                        st.error("생성 실패")
                        break
                    time.sleep(3)
            else:
                st.error(f"오류: {submit_res.get('message')}")