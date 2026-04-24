@echo off
chcp 65001 >nul
setlocal

REM ============================
REM Full Deploy: Code + Env
REM 1) DB backup (EC2 container -> ~/data/)
REM 2) Docker build
REM 3) EC2 transfer
REM 4) Upload env.list
REM 5) Container replace (volume mount for DB)
REM ============================

REM -- Config --
set PEM_PATH=d:\StreamLit\TestBase\.streamlit\aimz-edu-key.pem
set EC2_HOST=ec2-user@13.113.181.220
set APP_DIR=d:\StreamLit\TestBase

REM -- 1) Check files --
if not exist "%APP_DIR%\ec2_env.list" (
  echo [ERROR] ec2_env.list 파일이 없습니다.
  pause
  exit /b 1
)

REM -- 2) DB backup --
echo [1/6] EC2 DB 백업 중...
ssh -i "%PEM_PATH%" %EC2_HOST% "mkdir -p ~/data && docker cp aimz-edu:/app/runs.db ~/data/runs.db 2>/dev/null && echo DB 백업 완료 || echo DB 백업 스킵 (최초 배포 또는 이미 볼륨 사용 중)"

REM -- 3) Build --
echo [2/6] Docker 이미지 빌드 중...
cd /d %APP_DIR%
docker build -t aimz-edu . || (echo [ERROR] 빌드 실패 & pause & exit /b 1)

REM -- 4) Save tar --
echo [3/6] 이미지 저장 중...
docker save aimz-edu:latest -o aimz-edu.tar || (echo [ERROR] 저장 실패 & pause & exit /b 1)

REM -- 5) Transfer to EC2 --
echo [4/6] EC2로 전송 중 (시간이 걸릴 수 있습니다)...
scp -i "%PEM_PATH%" aimz-edu.tar %EC2_HOST%:~/ || (echo [ERROR] 전송 실패 & pause & exit /b 1)

REM -- 6) Upload env.list --
echo [5/6] env.list 전송 중...
scp -i "%PEM_PATH%" "%APP_DIR%\ec2_env.list" %EC2_HOST%:~/env.list || (echo [ERROR] env.list 전송 실패 & pause & exit /b 1)

REM -- 7) Replace container on EC2 --
echo [6/6] EC2 컨테이너 재시작 중...
ssh -i "%PEM_PATH%" %EC2_HOST% "docker stop aimz-edu 2>/dev/null; docker rm aimz-edu 2>/dev/null; sudo chown -R 1000:1000 ~/data 2>/dev/null; docker load -i ~/aimz-edu.tar && docker run -d --name aimz-edu --restart unless-stopped --env-file ~/env.list -v ~/data:/app/data -p 8501:8080 aimz-edu:latest && echo [SUCCESS] 배포 완료! || echo [ERROR] 컨테이너 시작 실패"

REM -- Cleanup local tar --
del aimz-edu.tar 2>nul

echo.
echo 코드 + 환경변수 전체 배포 완료!
timeout /t 5
endlocal
