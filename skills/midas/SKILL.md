---
name: midas
description: Midas overview + status. Trigger /midas for what plugin does, which gates active, how to disable (MIDAS_DISABLE=1).
---

# Midas

Scaffolding for mid-tier models. Active pieces:

- Session protocol injected at start (~150 tokens, once).
- Edit gate: first edit with zero exploration denied once — grep first, retry.
- Verify gate: stop after edits with no check blocked once — verify or state why skipped.
- Thrash nudge: same command failing 2x triggers debug protocol pointer.
- Large-read nudge: unbounded read >400 lines triggers grep-first pointer.

Each gate/nudge fires max once per session. Disable everything: `MIDAS_DISABLE=1`.

Playbooks (load on demand): midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask.
