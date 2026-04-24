@echo off
setlocal enabledelayedexpansion

REM ============================
REM Streamlit Local Runner
REM - Creates venv if missing
REM - Installs requirements
REM - Runs streamlit app
REM ============================

REM 0) Move to this script directory
cd /d "%~dp0"

REM 1) Check python
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Install Python 3.10+ and check "Add python.exe to PATH".
  pause
  exit /b 1
)

REM 2) Create venv if missing
if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating venv at .\.venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
)

REM 3) Activate venv
call ".venv\Scripts\activate.bat"

REM 4) Upgrade pip
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip >nul
if errorlevel 1 (
  echo [WARN] pip upgrade failed. continuing...
)

REM 5) Install requirements (prefer requirements.txt if exists)
if exist "requirements.txt" (
  echo [INFO] Installing from requirements.txt ...
  pip install -r requirements.txt
  if errorlevel 1 ( 
    echo [ERROR] pip install -r requirements.txt failed.
    pause
    exit /b 1
  )
) else (
  echo [INFO] requirements.txt not found. Installing minimal deps...
  pip install streamlit requests PyJWT
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
  )
)

REM 6) Optional: set env vars (if you want to use env instead of secrets)
REM set "MJ_API_KEY=YOUR_LEGNEXT_KEY"
REM set "KLING_ACCESS_KEY=YOUR_KLING_AK"
REM set "KLING_SECRET_KEY=YOUR_KLING_SK"
REM set "DEBUG_AUTH=1"
set "RUNS_DB_PATH=%~dp0runs.db"

REM 7) Run streamlit
if not exist "app.py" (
  echo [ERROR] app.py not found in: %cd%
  echo Rename your streamlit file to app.py or change the command below.
  pause
  exit /b 1
)

echo [INFO] Starting Streamlit...
python -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1

REM 8) keep window
pause
endlocal
