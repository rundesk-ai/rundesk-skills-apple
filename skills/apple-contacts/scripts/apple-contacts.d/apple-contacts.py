#!/usr/bin/env python3
"""
Dispatch to the Apple Contacts read or write CLI.

Usage:
  apple-contacts read <args...>
  apple-contacts write <args...>

Inputs:
  Delegates to apple-contacts-read.py or apple-contacts-write.py.

Outputs:
  Same output as the delegated CLI.

Write/mutation behavior:
  The read subcommand is read-only. The write subcommand keeps the write CLI's
  dry-run-by-default behavior and only mutates Contacts.framework with --confirm.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print("usage: apple-contacts read <args...> | write <args...>")
        return 0
    if not argv or argv[0] not in {"read", "write"}:
        print("usage: apple-contacts read <args...> | write <args...>", file=sys.stderr)
        return 2

    target = SCRIPT_DIR / ("apple-contacts-read.py" if argv[0] == "read" else "apple-contacts-write.py")
    os.execv(sys.executable, [sys.executable, str(target), *argv[1:]])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
