#!/usr/bin/env python3
"""Escalation watcher — checks for stuck work and shouts.

Reads watcher.rules.yml and checks:
  gate_red         a gate has been failing longer than its SLA
  contract_change  the contract changed without recorded approval
  milestone_risk   the due date is near and gates are not all green

Notifies a Slack webhook when SLACK_WEBHOOK_URL is set, otherwise prints to
stdout (pipe it to mail/cron output for the email fallback).

Usage:
    python3 tools/escalation-watcher.py            # run all checks once
    python3 tools/escalation-watcher.py --loop 30  # re-check every 30 minutes

Zero third-party dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from gdf import load_simple_yaml  # noqa: E402  (stdlib-only mini YAML parser)


def git_output(root: pathlib.Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def load_gate_status(root: pathlib.Path) -> dict:
    path = root / ".grounding" / "gate-status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def check_gate_red(root: pathlib.Path, rule: dict) -> list[str]:
    threshold_hours = float(rule.get("for_hours", 4))
    now = dt.datetime.now(dt.timezone.utc)
    alerts = []
    for gate_id, entry in sorted(load_gate_status(root).items()):
        if entry.get("status") != "fail":
            continue
        failed_since = entry.get("failed_since") or entry.get("updated_at")
        if not failed_since:
            continue
        try:
            since = dt.datetime.fromisoformat(failed_since)
        except ValueError:
            continue
        red_hours = (now - since).total_seconds() / 3600
        if red_hours >= threshold_hours:
            alerts.append(
                f"gate_red: {gate_id} has been red for {red_hours:.1f}h "
                f"(SLA {threshold_hours:g}h, since {failed_since})"
            )
    return alerts


def check_contract_change(root: pathlib.Path, rule: dict) -> list[str]:
    watch = rule.get("watch", "framework/contract.yml")
    trailer = rule.get("require_trailer", "Approved-by")
    alerts = []

    dirty = git_output(root, ["status", "--short", "--", watch]).strip()
    if dirty:
        alerts.append(f"contract_change: {watch} has uncommitted edits ({dirty})")

    last_message = git_output(root, ["log", "-1", "--format=%B", "--", watch])
    if last_message and f"{trailer}:" not in last_message:
        subject = last_message.strip().splitlines()[0]
        alerts.append(
            f"contract_change: last commit touching {watch} has no '{trailer}:' "
            f"trailer ({subject!r})"
        )
    return alerts


def check_milestone_risk(root: pathlib.Path, rule: dict) -> list[str]:
    due_raw = str(rule.get("due", "")).strip()
    if not due_raw:
        return []
    try:
        due = dt.datetime.fromisoformat(due_raw)
    except ValueError:
        return [f"milestone_risk: cannot parse due date {due_raw!r} (use YYYY-MM-DD)"]
    if due.tzinfo is None:
        due = due.replace(tzinfo=dt.timezone.utc)

    hours_before = float(rule.get("hours_before", 24))
    now = dt.datetime.now(dt.timezone.utc)
    hours_left = (due - now).total_seconds() / 3600
    if hours_left > hours_before:
        return []

    status = load_gate_status(root)
    not_green = [g for g, e in sorted(status.items()) if e.get("status") != "pass"]
    if not status:
        not_green = ["no gates have run"]
    if not_green:
        when = f"in {hours_left:.0f}h" if hours_left >= 0 else f"{-hours_left:.0f}h overdue"
        return [f"milestone_risk: milestone due {when} and gates not all green ({', '.join(not_green)})"]
    return []


def notify(alerts: list[str], targets: dict[str, str]) -> None:
    lines = []
    for alert in alerts:
        rule_name = alert.split(":", 1)[0]
        target = targets.get(rule_name, "")
        lines.append(f"{alert}" + (f"  → notify: {target}" if target else ""))
    text = "Escalation watcher:\n" + "\n".join(f"• {line}" for line in lines)

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        req = urllib.request.Request(
            webhook,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[watcher] sent {len(alerts)} alert(s) to Slack")
            return
        except OSError as exc:
            print(f"[watcher] Slack notify failed ({exc}); falling back to stdout")
    print(text)


def run_checks(root: pathlib.Path, rules: dict) -> int:
    alerts: list[str] = []
    targets: dict[str, str] = {}
    checks = {
        "gate_red": check_gate_red,
        "contract_change": check_contract_change,
        "milestone_risk": check_milestone_risk,
    }
    for name, check in checks.items():
        rule = rules.get(name)
        if not isinstance(rule, dict):
            continue
        targets[name] = str(rule.get("notify", ""))
        alerts.extend(check(root, rule))

    if alerts:
        notify(alerts, targets)
        return 1
    print("[watcher] all clear")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root (contains watcher.rules.yml)")
    parser.add_argument("--rules", default="watcher.rules.yml")
    parser.add_argument("--loop", type=float, metavar="MINUTES", help="re-check on an interval")
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    rules_path = root / args.rules
    if not rules_path.exists():
        print(f"[watcher] missing {rules_path}", file=sys.stderr)
        return 2
    rules = load_simple_yaml(rules_path.read_text(encoding="utf-8"))

    if not args.loop:
        return run_checks(root, rules)

    print(f"[watcher] checking every {args.loop:g} min (rules: {rules_path})")
    while True:
        run_checks(root, rules)
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    sys.exit(main())
