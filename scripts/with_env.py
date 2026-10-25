#!/usr/bin/env python
"""Run a command with extra environment variables — portable across sh and cmd.exe.

    uv run --no-project scripts/with_env.py GW_BROWSER_TESTS=1 FOO=bar -- uv run pytest
"""

from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: with_env.py NAME=value ... -- command [args]", file=sys.stderr)
        return 2
    sep = argv.index("--")
    env = dict(os.environ)
    for pair in argv[:sep]:
        name, _, value = pair.partition("=")
        env[name] = value
    return subprocess.call(argv[sep + 1 :], env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
