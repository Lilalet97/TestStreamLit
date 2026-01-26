import streamlit as st
import requests
import time
import jwt  # PyJWT 설치 필요

# 1. JWT 토큰 생성 함수 (Kling 인증 방식)
def generate_kling_token(ak, sk):
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800, # 30분 유효
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, sk, headers=headers)

st.title("🎨 Kling AI 이미지 생성기")

# 사이드바에서 API 키 관리
with st.sidebar:
    ak = st.text_input("Kling Access Key", type="password")
    sk = st.text_input("Kling Secret Key", type="password")

prompt = st.text_input("어떤 이미지를 그릴까요?", placeholder="A futuristic city with neon lights...")

if st.button("이미지 생성 시작"):
    if not ak or not sk:
        st.error("API 키를 입력해주세요.")
    else:
        token = generate_kling_token(ak, sk)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        # 2. 이미지 생성 요청 (Task 제출)
        submit_url = "https://api.klingai.com/v1/images/generations"
        payload = {"model": "kling-v1", "prompt": prompt}
        
        response = requests.post(submit_url, headers=headers, json=payload).json()
        
        if response.get("code") == 200:
            task_id = response["data"]["task_id"]
            st.info(f"작업 시작! (ID: {task_id}) 잠시만 기다려주세요...")
            
            # 3. 결과 확인 (Polling)
            check_url = f"https://api.klingai.com/v1/images/generations/{task_id}"
            while True:
                status_res = requests.get(check_url, headers=headers).json()
                status = status_res["data"]["task_status"]
                
                if status == "succeed":
                    image_url = status_res["data"]["task_result"]["images"][0]["url"]
                    st.image(image_url, caption="Kling이 생성한 이미지")
                    break
                elif status == "failed":
                    st.error("이미지 생성에 실패했습니다.")
                    break
                
                time.sleep(2) # 2초마다 확인
        else:
            st.error(f"오류 발생: {response.get('message')}")