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
| Six on-demand skills | User/model loads playbook | 0 until loaded |

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

Worst-case unconditional cost is protocol ~150 tokens + skill descriptions ~120 tokens ~= 270 tokens/session. Every other injection is failure-triggered and once-only, <= ~100 tokens combined worst case. These are design targets, not measured benchmarks; the explore discipline (grep-first, offset/limit reads) typically saves thousands of tokens per session on any non-trivial codebase, so expected net is negative.

## Disable

`MIDAS_DISABLE=1` env var kills all hooks. `/plugin uninstall midas@midas` removes.

## How It Works

Midas stores tiny per-session state in `$TMPDIR`. Hook logic is pure-function based for direct tests. All hook entrypoints silent-fail so a broken hook never breaks a session.

## Development

Run the test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```
