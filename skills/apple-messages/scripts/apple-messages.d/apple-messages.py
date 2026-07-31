#!/usr/bin/env python3
"""
Dispatch Apple Messages source-of-truth commands.

Usage:
  apple-messages read <args...>
  apple-messages send <args...>

Inputs:
  Forwards all arguments to the read or send CLI in this directory.

Outputs:
  Prints the selected child command output and returns its exit code. This
  dispatcher does not read, write, or send anything directly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print("usage: apple-messages read <args...> | send <args...>")
        return 0
    if not argv or argv[0] not in {"read", "send"}:
        print("usage: apple-messages read <args...> | send <args...>", file=sys.stderr)
        return 2
    target = ROOT / f"apple-messages-{argv[0]}.py"
    return subprocess.run([sys.executable, str(target), *argv[1:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
