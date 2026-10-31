# Prompt S4 — resolve a merge conflict (A, at each merge)

```text
Read CLAUDE.md. I'm merging <branch> into main and git reports conflicts in: <files>.
For each conflict, show what each side was trying to do in one line, then resolve it so
both intentions survive. If they truly contradict, stop and ask me. After resolving, run
the offline test and every test file, and list what you kept from each side.
```
