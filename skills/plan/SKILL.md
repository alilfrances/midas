---
name: plan
description: Decomposition template for multi-step work. Use before any task needing 3+ actions or touching 2+ files. Prevents malformed plans and mid-task drift.
---

# Plan

Before acting on multi-step work, write plan in this shape (5-10 lines max):

1. **Goal** — one sentence, restate what user asked. If restatement uncertain, that signal to ask (midas:ask).
2. **Unknowns** — what you must discover first. Each unknown maps to one explore action (Grep/Glob/Read).
3. **Steps** — ordered, each one concrete action. Each step names files touched.
4. **Verify** — per step or at end: exact command proving it worked.
5. **Out of scope** — what you will NOT touch. Guards against drift.

Rules:
- Resolve unknowns BEFORE step 1. Never plan around a guess.
- Step with no verify method = smell. Rework it.
- Plan changed mid-task? State new plan in one line, then continue. Silent pivots cause thrash.
- Small task (1 file, obvious change): skip template, just state goal + verify.
