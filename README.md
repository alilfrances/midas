# Midas

Midas — turns mid-tier models to gold. Deterministic scaffolding that lifts Haiku/Sonnet agentic quality toward frontier-tier behavior at net-zero token overhead.

## Why

Research shows scaffolding roughly doubles weaker-model success on agentic tasks while barely moving frontier models. Dominant small-model failures are premature edits before exploration, skipped verification, and retry-thrashing. Midas targets exactly these with deterministic gates, not prompt bloat.

## What You Get

| Piece | Fires when | Cost |
| --- | --- | --- |
| Session protocol | Session starts | ~150 tok, once |
| Edit gate | First existing-file edit before exploration | Deny once, 0 tok until fired |
| Verify gate | Stop after edits with no check | Block once |
| Thrash nudge | Same command fails twice | ~25 tok, once |
| Large-read nudge | Unbounded read over 400 lines | ~25 tok, once |
| Bash router | Bare `cat`/`grep`/`find -name` via Bash; Codex allows `rg` | Deny once per class |
| Freshness gate | Stale-looking errors or upgrade/latest prompts | ~25 tok, once |
| Lessons | Prior repo failures or deliberate notes exist | Top 3, <=240 chars |
| Scope skill | Capability/access limits | 0 until loaded |
| Seven on-demand skills | User/model loads playbook | 0 until loaded |

## Install

In Claude Code:

```
/plugin marketplace add alilkuizon/midas
/plugin install midas@midas
```

From a local clone:

```
/plugin marketplace add "/path/to/midas"
/plugin install midas@midas
```

In Codex:

```sh
codex plugin marketplace add /path/to/midas
```

Then restart Codex and install `midas` from the registered marketplace.

Requires Python 3.10+ on PATH (`python3`). No other dependencies.

## Token Budget

| Piece | Cost | Offsetting saving |
| --- | --- | --- |
| Protocol +1 line (capability rule) | ~+20 tok once | - |
| Bash router deny (3 classes, once each) | ~+25 tok each, failure-triggered | Prevents full-file `cat` dumps / unpaged `grep -r` floods — hundreds to thousands of tok each |
| Freshness nudge (once) | ~+25 tok | Prevents retry loops against stale API knowledge (each failed retry >=100 tok) |
| Lesson injection at start (only when lessons exist) | <=60 tok | Prevents repeating a failure sequence that previously cost a full thrash loop |
| Lesson match on PreToolUse (once) | ~+30 tok | Skips known-bad command + its error output |
| UserPromptSubmit hook | 0 tok when silent (99% of prompts) | - |
| New skills (scope) | 0 until loaded; +~15 tok description | - |

Worst-case unconditional new cost is ~55 tok/session. Everything else is failure-triggered, once-only, and meant to be cheaper than the failure it prevents.

## Self-learning

Midas keeps a tiny per-project lesson file outside the repo: `$CLAUDE_CONFIG_DIR/midas-data/lessons-<cwdhash>.json`, else `~/.claude/midas-data/`. Reset by removing that data dir.

Automatic learning records retry-thrash commands and the later successful fix command when it matches the failed command's first token or a verify command. Deliberate notes use:

```sh
midas-lesson "note"
```

`MIDAS_DISABLE=1` disables all hook behavior and the `midas-lesson` CLI. Bloat is bounded: max 40 lessons per project, top 3 injected at session start, and the injected block is capped at 240 chars. Retrieval is honest exact/pattern memory, not semantic search.

## Plays well with others

No companion plugin is required. Midas is stdlib-only and self-contained.

`tokenslim`: large-read threshold is tunable with `MIDAS_READ_NUDGE_LINES`; invalid values fall back to 400.

Other Stop hooks: stop gates can stack, but Midas blocks once and respects active stop hooks to avoid loops.

MCP retrieval tools count as exploration when their names look like query/search/read/symbol/context/localize/triage/repro/docs work, so Cortex, Axon, and similar tools can satisfy the edit gate.

Subagents share the same session state. Router denies are once per class across the session, and compound Bash commands are exempt.

## Disable

`MIDAS_DISABLE=1` env var kills all hooks and lesson writes. `/plugin uninstall midas@midas` removes.

## How It Works

Midas stores tiny per-session state in `$TMPDIR`. Hook logic is pure-function based for direct tests. All hook entrypoints silent-fail so a broken hook never breaks a session.

Claude and Codex use separate hook entrypoints. Claude keeps `hooks/midas_hook.py`; Codex uses `hooks/codex_hook.py`, which enables Codex-native guidance such as `rg --files`, `rg -n -C 3`, and bounded shell reads.

## Development

Run the test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```
