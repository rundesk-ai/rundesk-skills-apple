#!/usr/bin/env python3
"""
Dispatch Apple Calendar source-of-truth commands.

Usage:
  apple-calendar read <args...>
  apple-calendar write <args...>

Inputs:
  Forwards all arguments to the read or write CLI in this directory.

Outputs:
  Prints the selected child command output and returns its exit code. This
  dispatcher does not read, write, or mutate Calendar data directly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print("usage: apple-calendar read <args...> | write <args...>")
        return 0
    if not argv or argv[0] not in {"read", "write"}:
        print("usage: apple-calendar read <args...> | write <args...>", file=sys.stderr)
        return 2
    target = ROOT / f"apple-calendar-{argv[0]}.py"
    return subprocess.run([sys.executable, str(target), *argv[1:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
