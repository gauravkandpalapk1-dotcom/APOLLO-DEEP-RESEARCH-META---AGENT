@echo off
cd /d %~dp0
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer from python.org and tick "Add python.exe to PATH".
  exit /b 1
)
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example. Fill in your keys before prompt 01.
)
if not exist .git (
  git init
  git add .
  git commit -m "Starting point"
)
echo.
echo Running the offline test...
python -m tests.run_all
echo.
echo Setup done. Next: fill in .env, run the "Check keys" task until it says READY, then paste prompts\01_ALL_P0.1_check_keys_and_tour.md into Claude Code
