#!/usr/bin/env python3
"""Configure the local account allowlist for the Apple Mail read integration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apple_mail_lib import (
    DEFAULT_CONFIG,
    AppleMailError,
    account_map,
    live_accounts,
    load_config,
    print_json,
    save_config,
    validate_account_ids,
)


def account_payload(accounts, allowed_ids):
    return [dict(account, allowed=account.get("id") in allowed_ids) for account in accounts]


def print_accounts(accounts, allowed_ids):
    for account in account_payload(accounts, allowed_ids):
        emails = ",".join(account.get("email_addresses", [])) or "-"
        print(
            f"account_id={account['id']} | name={account.get('name') or '-'} | "
            f"enabled={str(bool(account.get('enabled'))).lower()} | allowed={str(account['allowed']).lower()} | "
            f"emails={emails}"
        )


def command_status(args):
    accounts = live_accounts()
    allowed_ids = set(load_config(args.config)["allowed_account_ids"])
    live_ids = set(account_map(accounts))
    payload = {
        "status": "ok",
        "config": str(Path(args.config).expanduser()),
        "config_exists": Path(args.config).expanduser().exists(),
        "accounts": len(accounts),
        "allowed_accounts": len(live_ids & allowed_ids),
        "stale_allowed_account_ids": sorted(allowed_ids - live_ids),
    }
    if args.json:
        print_json(payload)
    else:
        print(
            f"Apple Mail setup access ok | accounts={payload['accounts']} | "
            f"allowed_accounts={payload['allowed_accounts']} | config_exists={str(payload['config_exists']).lower()}"
        )
    return 0


def command_accounts(args):
    accounts = live_accounts()
    allowed_ids = set(load_config(args.config)["allowed_account_ids"])
    payload = account_payload(accounts, allowed_ids)
    print_json(payload) if args.json else print_accounts(accounts, allowed_ids)
    return 0


def command_change(args, operation):
    accounts = live_accounts()
    requested = validate_account_ids(args.account_id, accounts)
    current = set(load_config(args.config)["allowed_account_ids"])
    updated = current | set(requested) if operation == "allow" else current - set(requested)
    payload = {
        "operation": operation,
        "dry_run": not args.confirm,
        "account_ids": requested,
        "allowed_account_ids": sorted(updated),
        "config": str(Path(args.config).expanduser()),
    }
    if args.confirm:
        save_config(args.config, sorted(updated))
    if args.json:
        print_json(payload)
    else:
        prefix = "dry-run: would update" if not args.confirm else "updated"
        print(f"{prefix} Apple Mail allowlist | operation={operation} | account_ids={','.join(requested)}")
        if not args.confirm:
            print("Pass --confirm only after the owner approves these exact Apple Mail account IDs.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Configure allowed Apple Mail accounts.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Local allowlist config path.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "accounts"):
        child = subparsers.add_parser(name)
        child.add_argument("--json", action="store_true")
    for name in ("allow", "revoke"):
        child = subparsers.add_parser(name)
        child.add_argument("--account-id", action="append", required=True)
        child.add_argument("--confirm", action="store_true")
        child.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return command_status(args)
        if args.command == "accounts":
            return command_accounts(args)
        return command_change(args, args.command)
    except AppleMailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
