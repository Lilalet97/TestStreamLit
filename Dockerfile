FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 복사
COPY . .

# DB 데이터 디렉토리 (볼륨 마운트 대상)
RUN mkdir -p /app/data

# non-root 사용자 생성 (uid=1000: EC2 ec2-user와 동일 → 볼륨 마운트 권한 호환)
RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# App Runner는 PORT 환경변수를 주입함
ENV PORT=8080

EXPOSE ${PORT}

# Streamlit 실행 (App Runner 호환 설정)
CMD streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.enableWebsocketCompression=false \
    --browser.gatherUsageStats=false
