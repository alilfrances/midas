---
name: debug
description: Systematic debugging loop. Load when any command/test fails twice, output surprises, or Midas thrash nudge fires. Replaces retry-guessing.
---

# Debug

Loop (max 4 iterations, then report honestly):

1. **Reproduce** — one command that shows failure. No repro = no debugging, find repro first.
2. **Read error verbatim** — quote exact message. First actionable error only; downstream errors are noise.
3. **Locate** — grep the failing symbol/file/line from the error. Read narrow around it.
4. **Hypothesize** — ONE sentence: "fails because X". No hypothesis = gather more data (add print/log, read caller), don't edit.
5. **Test hypothesis** — single smallest change. Never two changes at once — can't attribute result.
6. **Verify** — rerun repro. Fixed: remove debug artifacts, done. Not fixed: hypothesis wrong, revert change, back to 2 with new data.

Bans:
- No shotgun edits (changing several suspects at once).
- No retrying identical command hoping different result.
- No "fix" that silences error without explaining original cause.
- 4 failed loops: report exact command, error, hypotheses tried, current state. Honest stuck beats fake progress.
