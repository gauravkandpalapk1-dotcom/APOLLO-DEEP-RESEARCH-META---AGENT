"""All limits in one place. Every one of these is enforced in code, not in prompts."""
import os
from dataclasses import dataclass, field
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SOURCE_TYPES = ("web", "news", "arxiv", "edgar")

# ---------- LLM providers (keys and chains live in .env) ----------
PROVIDERS = ("nvidia", "gemini", "groq", "hf")
LLM_MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "3"))
LLM_TIMEOUTS = {"nvidia": 120, "default": 60}   # seconds per call; reasoning models are slow
DISABLE_THINKING = os.environ.get("DISABLE_THINKING", "true").lower() in ("1", "true", "yes")
# Extra request body that turns "thinking" off, per model family (matched in the model id).
# If a provider rejects it, the client retries that model without it automatically.
# Check each model's page on build.nvidia.com and add families here once confirmed.
THINKING_OFF = {
    "nemotron": {"chat_template_kwargs": {"enable_thinking": False}},
}


@dataclass
class Limits:
    max_rounds: int = 3              # hard cap on search -> review rounds
    max_seconds: int = 900           # wall-clock cap for the whole research loop
    token_budget: int = 400_000      # total LLM tokens (prompt + completion)
    max_attempts_per_sq: int = 2     # searches per sub-question before it is marked unfillable
    min_sources_per_sq: int = 2      # distinct URLs needed before a sub-question counts as covered
    stale_years: int = 3             # a sub-question whose dated sources are all older than this is "stale"
    results_per_query: int = 5
    chunks_per_sq: int = 6           # evidence chunks per sub-question shown to Hermes / writers
    parallel_searches: int = 4       # Athena worker threads
    search_timeout: int = 20         # seconds per HTTP call
    search_retries: int = 2
    enabled_sources: tuple = field(default_factory=lambda: SOURCE_TYPES)

    @property
    def stale_before_year(self) -> int:
        return datetime.now().year - self.stale_years


def env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing environment variable {name}. Copy .env.example to .env and fill it in.")
    return value
