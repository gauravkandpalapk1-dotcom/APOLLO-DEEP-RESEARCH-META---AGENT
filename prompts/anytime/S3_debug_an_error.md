# Prompt S3 — debug an error (anyone, any time)

```text
Read CLAUDE.md. Here is the error and what I was doing:
<paste the full traceback or the wrong behaviour, and the command or click that caused it>

1. Find the root cause before changing anything. Tell me in plain words what is wrong
   and why, with file and line.
2. Propose the smallest fix and wait for my OK.
3. After the fix, add a test that would have caught it and run the offline test.
Don't hide the error with a broad try/except.
```
