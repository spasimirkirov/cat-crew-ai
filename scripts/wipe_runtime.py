"""Clear old chats, scratch, traces, and the live catalog so Home starts empty.

Keeps Settings keys, connectors, models, and mem0.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jinx_ai.config import BASE_DIR

TARGETS = (
    BASE_DIR / "data" / "sessions",
    BASE_DIR / "data" / "scratch",
    BASE_DIR / "data" / "traces",
    BASE_DIR / "data" / "platform" / "registry.json",
)


def main() -> None:
    for path in TARGETS:
        if not path.exists():
            print(f"skip {path}")
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"removed {path}")


if __name__ == "__main__":
    main()
