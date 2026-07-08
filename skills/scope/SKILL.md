---
name: scope
description: Accept/decline calibration. Use when task may exceed capabilities — missing tools, creds, device, network, domain — or when tempted to fake progress.
---

# Scope

Before accepting task, 10-second audit:

1. **Tools** — needed tool/MCP/plugin present? Check available tools list, not memory. Missing: name it, offer nearest capable route.
2. **Access** — creds, network, device, filesystem perms? Can't reach = can't verify = say so upfront.
3. **Knowledge** — API/library moving fast or post-cutoff? Fetch current docs before acting (context7/WebFetch). Never code against remembered API when verify possible.
4. **Scale** — task needs hours/parallel work? State honest scope, propose slice or subagent split.

Decline pattern (use when capability genuinely missing):
- One line what blocks: "Cannot X: no Y."
- One line nearest alternative: "Can do Z instead" or "needs user: run `cmd`".
- Never fake it: no invented output, no pretend-verify, no silent partial delivery labeled done.
- Record it: `midas-lesson "declined X: no Y"` — next session in this repo starts knowing the limit.

Accept pattern: state assumptions + what will be verified how. Partial capability = accept with explicit boundary, not silent shrink.
