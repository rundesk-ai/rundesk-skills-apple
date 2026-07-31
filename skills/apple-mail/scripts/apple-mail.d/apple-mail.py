#!/usr/bin/env python3
"""Dispatch Apple Mail read and setup commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print("usage: apple-mail read <args...> | setup <args...> | write <args...>")
        return 0
    if not argv or argv[0] not in {"read", "setup", "write"}:
        print("usage: apple-mail read <args...> | setup <args...> | write <args...>", file=sys.stderr)
        return 2
    target = ROOT / f"apple-mail-{argv[0]}.py"
    return subprocess.run([sys.executable, str(target), *argv[1:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
