#!/usr/bin/env python3
"""Shared configuration and Mail.app bridge helpers for Apple Mail integration."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


def default_config() -> Path:
    explicit = os.environ.get("APPLE_MAIL_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    current = xdg / "rundesk" / "integrations" / "apple-mail" / "accounts.json"
    legacy = Path.home() / ".config" / "workspace" / "apple-mail.json"
    return legacy if not current.exists() and legacy.exists() else current


DEFAULT_CONFIG = default_config()
BRIDGE = Path(__file__).resolve().parent / "AppleMailBridge.js"
AUTOMATION_TIMEOUT_SECONDS = 60


class AppleMailError(RuntimeError):
    pass


def generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    cleaned = (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\u2028", " ")
        .replace("\u2029", " ")
        .strip()
    )
    return cleaned if cleaned else fallback


def truncate(value: Any, limit: int = 220) -> str:
    cleaned = text(value)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3].rstrip() + "..."


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        return {"schema_version": SCHEMA_VERSION, "allowed_account_ids": []}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppleMailError(f"Unable to read Apple Mail config: {config_path}: {exc}") from exc
    account_ids = payload.get("allowed_account_ids")
    if not isinstance(account_ids, list) or not all(isinstance(item, str) and item.strip() for item in account_ids):
        raise AppleMailError("Apple Mail config must contain an allowed_account_ids string list.")
    return {"schema_version": SCHEMA_VERSION, "allowed_account_ids": sorted(set(account_ids))}


def save_config(path: str | Path, allowed_account_ids: list[str]) -> None:
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "allowed_account_ids": sorted(set(allowed_account_ids)),
    }
    temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(config_path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AppleMailError(f"Unable to write Apple Mail config: {config_path}: {exc}") from exc


def run_bridge(command: str, args: list[str] | None = None) -> Any:
    invocation = ["/usr/bin/osascript", "-l", "JavaScript", str(BRIDGE), command, *(args or [])]
    try:
        result = subprocess.run(
            invocation,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=AUTOMATION_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise AppleMailError(f"Mail.app automation failed: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppleMailError(f"Mail.app automation timed out after {AUTOMATION_TIMEOUT_SECONDS} seconds.") from exc
    except OSError as exc:
        raise AppleMailError(f"Unable to start Mail.app automation: {exc}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppleMailError("Mail.app automation returned invalid JSON.") from exc


def live_accounts() -> list[dict[str, Any]]:
    payload = run_bridge("accounts")
    if not isinstance(payload, list):
        raise AppleMailError("Mail.app account response was not a list.")
    return payload


def account_map(accounts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(account.get("id", "")): account for account in accounts if account.get("id")}


def validate_account_ids(requested: list[str], accounts: list[dict[str, Any]]) -> list[str]:
    available = account_map(accounts)
    missing = [account_id for account_id in requested if account_id not in available]
    if missing:
        raise AppleMailError(f"Unknown Apple Mail account id(s): {', '.join(missing)}")
    return sorted(set(requested))


def allowed_accounts(config_path: str | Path, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_ids = set(load_config(config_path)["allowed_account_ids"])
    return [account for account in accounts if account.get("id") in allowed_ids and account.get("enabled", True)]


def select_allowed_accounts(
    config_path: str | Path,
    accounts: list[dict[str, Any]],
    requested_ids: list[str] | None,
) -> list[dict[str, Any]]:
    configured = allowed_accounts(config_path, accounts)
    if not configured:
        raise AppleMailError(
            "No Apple Mail accounts are allowed. Review `apple-mail-setup.py accounts`, then allow exact account IDs."
        )
    if not requested_ids:
        return configured
    configured_by_id = account_map(configured)
    denied = [account_id for account_id in requested_ids if account_id not in configured_by_id]
    if denied:
        raise AppleMailError(f"Apple Mail account id(s) are not allowed: {', '.join(denied)}")
    return [configured_by_id[account_id] for account_id in dict.fromkeys(requested_ids)]


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
