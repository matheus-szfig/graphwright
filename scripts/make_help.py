#!/usr/bin/env python
"""Print the `## description` of every Makefile target (what `make help` shows)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = re.compile(r"^([a-zA-Z_-]+):.*?## (.*)$")
SECTION = re.compile(r"^# --- (.+?) -+$")


def main() -> int:
    # Windows consoles default to cp1252; never die on a character the Makefile uses.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    color = sys.stdout.isatty()
    for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines():
        if s := SECTION.match(line):
            print(f"\n\033[1m{s[1]}\033[0m" if color else f"\n{s[1]}")
            continue
        m = TARGET.match(line)
        if not m:
            continue
        name = f"{m[1]:<22}"
        print(f"  \033[36m{name}\033[0m {m[2]}" if color else f"  {name} {m[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
