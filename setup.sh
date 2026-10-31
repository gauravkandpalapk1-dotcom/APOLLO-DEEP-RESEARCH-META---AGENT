#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.11+ not found. Install it first."; exit 1; }
[ -d .venv ] || { echo "Creating virtual environment..."; python3 -m venv .venv; }
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example. Fill in your keys before prompt 01."; fi
if [ ! -d .git ]; then git init && git add . && git commit -m "Starting point"; fi
echo; echo "Running the offline test..."
python -m tests.run_all
echo; echo "Setup done. Next: fill in .env, run the Check keys task until it says READY, then paste prompts/01_ALL_P0.1_check_keys_and_tour.md into Claude Code"
