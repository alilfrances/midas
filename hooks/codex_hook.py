#!/usr/bin/env python3
"""Codex-specific Midas hook entrypoint."""
import os
import sys

import midas_hook


if __name__ == "__main__":
    os.environ["MIDAS_RUNTIME"] = "codex"
    try:
        midas_hook.main()
    except Exception:
        pass
    sys.exit(0)
