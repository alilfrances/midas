---
name: explore
description: Token-cheap codebase search strategy. Use before editing unfamiliar code, when locating symbols/usages/config, or after Midas edit gate fires.
---

# Explore

Funnel, cheapest first:

0. **Repo-context MCP tools first if installed** (graph query, symbol search, impact analysis — e.g. Cortex). Purpose-built retrieval beats raw grep; counts as exploration.
1. **Candidate files**: Claude Glob (`**/*auth*`, `src/**/*.ts`) or Codex `rg --files -g '*auth*'`.
2. **Shortlist text hits**: Claude Grep -l or Codex `rg -l`.
3. **Context hits**: Claude Grep -n -C 3 or Codex `rg -n -C 3`. Search declaration patterns (`def X`, `class X`, `func X`, `X =`) not bare name.
4. **Read narrow**: Claude Read offset/limit or Codex bounded shell reads (`sed -n 'START,ENDp'`). Full read only when file <200 lines.

Rules:
- Batch independent searches in one tool block — parallel, one round-trip.
- Before changing symbol: grep ALL usages, not just definition. Callers break silently otherwise.
- Wrong-looking grep results: widen pattern once, then try Glob on filenames. Two dead ends = rethink term, don't brute-force variants.
- Never read a whole file to "get context" when search can locate. Full-file reads are the #1 token leak.
- Record what you learned in one line before moving on (file:line of target, usage count).
