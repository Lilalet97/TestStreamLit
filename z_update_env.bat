@echo off
chcp 65001 >nul
setlocal

REM ============================
REM EC2 env.list remote update
REM 1) DB backup (EC2 container -> ~/data/)
REM 2) Upload ec2_env.list to EC2
REM 3) Restart container
REM ============================

REM -- Config --
set PEM_PATH=d:\StreamLit\TestBase\.streamlit\aimz-edu-key.pem
set EC2_HOST=ec2-user@13.113.181.220
set APP_DIR=d:\StreamLit\TestBase

REM -- 1) Check ec2_env.list exists --
if not exist "%APP_DIR%\ec2_env.list" (
  echo [ERROR] ec2_env.list 파일이 없습니다.
  pause
  exit /b 1
)

REM -- 2) DB backup --
echo [1/3] EC2 DB 백업 중...
ssh -i "%PEM_PATH%" %EC2_HOST% "mkdir -p ~/data && docker cp aimz-edu:/app/runs.db ~/data/runs.db 2>/dev/null && echo DB 백업 완료 || echo DB 백업 스킵 (최초 배포 또는 이미 볼륨 사용 중)"

REM -- 3) SCP transfer --
echo [2/3] env.list 전송 중...
scp -i "%PEM_PATH%" "%APP_DIR%\ec2_env.list" %EC2_HOST%:~/env.list
if errorlevel 1 (
  echo [ERROR] 전송 실패
  pause
  exit /b 1
)

REM -- 4) Restart container --
echo [3/3] 컨테이너 재시작 중...
ssh -i "%PEM_PATH%" %EC2_HOST% "docker stop aimz-edu 2>/dev/null; docker rm aimz-edu 2>/dev/null; docker run -d --name aimz-edu --restart unless-stopped --env-file ~/env.list -v ~/data:/app/data -p 8501:8080 aimz-edu:latest && echo [SUCCESS] 완료! || echo [ERROR] 컨테이너 시작 실패"

echo.
echo env.list 업데이트 완료!
timeout /t 5
endlocal
