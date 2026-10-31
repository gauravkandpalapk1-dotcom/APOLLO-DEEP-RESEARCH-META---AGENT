"""List the model ids every provider's keys can use, from each provider's /models endpoint.
Run: python -m scripts.list_models      (never prints keys)

Ids are printed the way STRONG_CHAIN / FAST_CHAIN need them (Gemini's "models/" prefix removed)."""
import sys

from apollo.llm import load_providers


def _openai_client(key: str, base_url: str):
    from openai import OpenAI  # imported lazily so offline tests can run without it
    return OpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=30)


def list_ids(info: dict, client_factory=_openai_client) -> tuple[list[str], str]:
    """Try each key until one can list models. Returns (ids, which key worked or why all failed)."""
    errors = []
    for i, key in enumerate(info["keys"], 1):
        try:
            models = client_factory(key, info["base_url"]).models.list()
            return sorted({m.id.removeprefix("models/") for m in models}), f"key #{i}"
        except Exception as e:  # report the error type and status only: provider messages can echo the key
            errors.append(f"key #{i}: {type(e).__name__} {getattr(e, 'status_code', '') or ''}".strip())
    return [], "; ".join(errors)


def main(providers: dict | None = None, client_factory=_openai_client) -> int:
    providers = load_providers() if providers is None else providers
    if not providers:
        print("No provider has keys. Fill in *_API_KEYS in .env.")
        return 1
    ok = False
    for name, info in providers.items():
        ids, note = list_ids(info, client_factory)
        if not ids:
            print(f"\n{name}: FAILED ({note})")
            continue
        ok = True
        print(f"\n{name}: {len(ids)} models ({note})")
        for model_id in ids:
            print(f"  {name}:{model_id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
