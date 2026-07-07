---
name: ask
description: Question quality rules — when to ask user vs decide, how to batch, when defaults beat questions. Use when tempted to ask user anything.
---

# Ask

Ask ONLY when answer changes what you do next AND you cannot resolve from request, code, or convention.

Don't ask when:
- Codebase answers it (grep/read first — 2 minutes of exploring beats round-trip).
- Convention answers it (project style, framework default).
- Any reasonable option is easily reversible — pick one, state choice, proceed.

When you do ask:
- Batch ALL questions in one message. Serial questions burn user round-trips.
- Each question: concrete options + your recommended default + why. "Option A (recommended): ... Option B: ..." Never open-ended "what do you want?"
- State what you'll do if no answer — then user silence is also answer.

Blocking vs non-blocking: irreversible/destructive/scope-changing = must ask. Everything else = decide, note decision in report, user can course-correct.
