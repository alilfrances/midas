---
name: edit
description: Editing discipline — smallest correct diff, style match, one concern per edit. Use before non-trivial edits or multi-file changes.
---

# Edit

Before edit:
- Read the exact region you change plus enclosing function/class. Not whole file.
- Grep usages of anything you rename/re-sign — every caller updates in same task.

During edit:
- Smallest diff that is correct. No drive-by refactors, no formatting churn, no added comments for simple code.
- Match surrounding style exactly: naming, indent, idiom, comment density.
- One concern per edit call. Two unrelated fixes = two edits.
- Multi-file change: order edits so code compiles at each step where possible (types first, then users).

After edit:
- Each edit needs verify path (midas:verify). Batch edits, then verify once — not verify per keystroke.
- Edit failed to match? Re-Read the exact lines, don't guess at whitespace.
