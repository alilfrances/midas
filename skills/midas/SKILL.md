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
- Bash router: bare cat/grep/find denied once per class; Claude uses Read/Grep/Glob, Codex uses rg plus bounded reads.
- Freshness gate: API/version-looking failures or upgrade prompts nudge current-docs check.
- Lessons: per-project thrash/fix pairs + `midas-lesson` notes inject top pitfalls.
- Scope: missing tools/access/network? state limit, offer nearest route, decline beats fake.

Each gate/nudge fires max once per session. Disable everything: `MIDAS_DISABLE=1`.

Playbooks (load on demand): midas:plan midas:explore midas:edit midas:debug midas:verify midas:ask midas:scope.

## Self-review (on demand)

User asks how session went or /midas review:
1. Locate lesson store: $CLAUDE_CONFIG_DIR/midas-data, else ~/.claude/midas-data — file lessons-<sha1(cwd)[:12]>.json. Session state: $TMPDIR/midas-<session_id>.json.
2. Read both. Report: gates/nudges fired this session, top recurring lessons (n desc).
3. Suggest ONE concrete improvement max: CLAUDE.md rule, repo skill, or prompt habit — tied to highest-n lesson. No lesson n>=2 → say "no recurring failures, nothing to suggest".
Zero cost until invoked — stats live in files, not context.
