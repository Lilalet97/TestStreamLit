@echo off
chcp 65001 >nul
setlocal

REM ============================
REM EC2 auto deploy
REM 1) DB backup (EC2 container -> ~/data/)
REM 2) Docker build
REM 3) EC2 transfer
REM 4) Container replace (volume mount for DB)
REM ============================

REM -- Config --
set PEM_PATH=d:\StreamLit\TestBase\.streamlit\aimz-edu-key.pem
set EC2_HOST=ec2-user@13.113.181.220
set APP_DIR=d:\StreamLit\TestBase

REM -- DB backup --
echo [1/5] EC2 DB 백업 중...
ssh -i "%PEM_PATH%" %EC2_HOST% "mkdir -p ~/data && docker cp aimz-edu:/app/runs.db ~/data/runs.db 2>/dev/null && echo DB 백업 완료 || echo DB 백업 스킵 (최초 배포 또는 이미 볼륨 사용 중)"

REM -- Build --
echo [2/5] Docker 이미지 빌드 중...
cd /d %APP_DIR%
docker build -t aimz-edu . || (echo [ERROR] 빌드 실패 & pause & exit /b 1)

REM -- Save tar --
echo [3/5] 이미지 저장 중...
docker save aimz-edu:latest -o aimz-edu.tar || (echo [ERROR] 저장 실패 & pause & exit /b 1)

REM -- Transfer to EC2 --
echo [4/5] EC2로 전송 중 (시간이 걸릴 수 있습니다)...
scp -i "%PEM_PATH%" aimz-edu.tar %EC2_HOST%:~/ || (echo [ERROR] 전송 실패 & pause & exit /b 1)

REM -- Replace container on EC2 --
echo [5/5] EC2 컨테이너 재시작 중...
ssh -i "%PEM_PATH%" %EC2_HOST% "docker stop aimz-edu 2>/dev/null; docker rm aimz-edu 2>/dev/null; sudo chown -R 1000:1000 ~/data 2>/dev/null; docker load -i ~/aimz-edu.tar && docker run -d --name aimz-edu --restart unless-stopped --env-file ~/env.list -v ~/data:/app/data -p 8501:8080 aimz-edu:latest && echo [SUCCESS] 배포 완료! || echo [ERROR] 컨테이너 시작 실패"

REM -- Cleanup local tar --
del aimz-edu.tar 2>nul

echo.
echo 배포 완료! 브라우저에서 확인하세요.
timeout /t 5
endlocal
