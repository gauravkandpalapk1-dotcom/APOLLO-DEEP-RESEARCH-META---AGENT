# START HERE

Do steps 1 to 7 once, on one laptop. After step 7 the team splits up.

## 1. Install (skip what you already have)
- Python 3.11 or newer (Windows: tick "Add python.exe to PATH" in the installer)
- Git, then set your name once:
  `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`
- VS Code, and the Claude Code extension: Ctrl+Shift+X, search "Claude Code", install the one
  whose publisher is **Anthropic** (verified), then sign in from its panel.

## 2. Open this folder in VS Code
File > Open Folder > choose this folder (the one containing this file). Accept the
recommended extensions if VS Code offers them.

## 3. Run setup
Terminal > Run Task > **Setup (first time)**.
If that doesn't start, open a terminal (Ctrl + `) and type `.\setup.bat` (Windows) or
`bash setup.sh` (Mac/Linux). It creates `.venv` and `.env`, installs packages, makes the first
git commit and runs the tests. It must end with `ALL CHECKS PASSED`.

## 4. Fill in `.env` yourself
Open `.env` (not `.env.example`). Fill in:
- the keys: `NVIDIA_API_KEYS`, `GEMINI_API_KEYS`, `GROQ_API_KEYS`, `HF_API_KEYS` (comma-separate
  several keys for one provider), and `TAVILY_API_KEYS`
- in `STRONG_CHAIN` and `FAST_CHAIN`, replace `GEMINI_FLASH_MODEL_ID` and `GROQ_MODEL_ID` with
  real model ids from Google AI Studio and the Groq console
Never paste keys into the Claude Code chat. `.env` is git-ignored.

Model chains (tried left to right; if one fails, the next takes over):
- STRONG (planner, writers, assembler): Nemotron 3 Ultra 550B → DeepSeek V4 Pro → Gemini Flash
- FAST (Hermes, verifier): Nemotron 3 Super 120B → DeepSeek V4 Flash → Gemini Flash → Groq

## 5. Check the keys
Terminal > Run Task > **Check keys**. It tests every model with every key, plus Tavily,
and ends with READY or NOT READY. Fix FAILED lines (usually a wrong model id) until READY.

## 6. Paste the prompts in order
Open the Claude Code panel (Claude icon on the left, or Ctrl+Shift+P > "Claude Code: Open").
`prompts/README.md` has the order. For each file, copy only the text inside the ```text block.
Start with `prompts/01_ALL_P0.1_check_keys_and_tour.md`, then `02_ALL_P0.2_first_live_run.md`.
After each prompt: Run Task > **Run tests**, then commit.

## 7. Split up after Gate 1 (a real topic produced a cited report.md)
- Push to a **private** GitHub repo.
- Each teammate: clone it, do steps 3 to 5 on their laptop, create their branch
  (`git checkout -b role-a`, `role-b`, `role-c`, `role-d`), open Claude Code, and go down
  their list in `prompts/README.md`.
- A merges branches into `main` (B first in Phase 1).

## VS Code tasks (Terminal > Run Task)
- **Check keys**: which keys and models work right now
- **Run tests**: run after every prompt
- **Run Apollo app**: opens the Streamlit app in the browser
