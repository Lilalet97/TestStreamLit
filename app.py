import streamlit as st
from openai import OpenAI

st.title("🤖 나만의 AI 비서")

# 1. API 키 설정 (보안을 위해 설정 파일에서 불러옴)
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# 2. 채팅 내역 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []

# 3. 저장된 메시지 표시
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 4. 사용자 입력 처리
if prompt := st.chat_input("무엇이든 물어보세요!"):
    # 사용자 메시지 표시 및 저장
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 5. AI 응답 생성
    with st.chat_message("assistant"):
        response = client.chat.completions.create(
            model="gpt-4o-mini", # 혹은 "gpt-3.5-turbo"
            messages=[{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
        )
        full_response = response.choices[0].message.content
        st.markdown(full_response)
    
    # AI 응답 저장
    st.session_state.messages.append({"role": "assistant", "content": full_response})