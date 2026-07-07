---
name: verify
description: Evidence-before-claims checklist. Load before saying done/fixed/passing, or when Midas verify gate blocks a stop.
---

# Verify

Claim requires evidence. Before "done" / "fixed" / "works":

1. Pick narrowest real check: the one failing test, the touched module's tests, targeted build. Full suite only when change is broad or project demands.
2. Run it. Read output — exit code AND content. "Command ran" is not "check passed".
3. Behavior change: exercise changed path (run app/script/curl), not just compile.
4. State evidence in report: command + result, one line. "Tests pass" alone = unverified claim.

Failures:
- Check fails: report it as failing with exact output. Never claim partial success as success.
- Can't verify (needs device/network/credentials): say exactly what wasn't verified and why. That honest, acceptable.
- Skipping verification for trivial change (docs, comment): state "verification skipped: docs-only" — one line satisfies gate.
