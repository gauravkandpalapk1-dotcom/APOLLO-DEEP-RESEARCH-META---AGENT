# Prompt 0.1 — check keys and tour the code
Owner: all (one laptop) · When: H0 · Needs: setup done, .env filled in by you

```text
Read CLAUDE.md. Don't open .env and don't change any code in this task.

1. Run python -m tests.run_all and report the result.
2. Run python -m scripts.check_keys. For every FAILED or WARNING line, tell me in plain
   words the likely cause (wrong model id, bad key, placeholder still in a chain, provider
   down) and exactly which .env line I should change. I will edit .env myself.
3. If a model's "thinks" column says yes, or it took over 30 seconds, tell me whether to
   move it later in its chain.
4. In under 200 words, explain how the files connect, from app.py down to llm.py and
   search.py, and how a failed model call falls over to the next model or key.
```

Done when: `python -m scripts.check_keys` ends with READY.
