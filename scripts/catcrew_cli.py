#!/usr/bin/env python
"""Convenience wrapper so you can call `python scripts/catcrew ...` without installing the CLI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.argv[0] = str(Path(__file__).relative_to(ROOT).as_posix())
    from backend.cli import main

    raise SystemExit(main())
