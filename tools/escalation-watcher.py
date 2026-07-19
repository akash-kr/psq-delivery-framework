#!/usr/bin/env python3
"""Evaluate the delivery escalation contract and notify configured channels.

The watcher is intentionally stdlib-only. Rules live in watcher.rules.yml and
are validated before any checks or notifications run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from gdf import load_simple_yaml  # noqa: E402


SCHEMA_VERSION = "1.0.0"
SEVERITIES = {"info", "warning", "high", "critical"}
STATUSES = {"pass", "fail", "blocked"}
RULE_TYPES = {
    "artifact_missing",
    "contract_approval",
    "gate_deadline",
    "gate_status",
    "milestone_risk",
    "uat_pending",
}


@dataclass(frozen=True)
class Alert:
    rule_id: str
    event_id: str
    severity: str
    summary: str
    detail: str
    channel: str
    notify: str

    @property
    def key(self) -> str:
        return f"{self.rule_id}:{self.event_id}"


def git_output(root: pathlib.Path, args: list[str], *, binary: bool = False) -> str | bytes:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=not binary,
            check=False,
        )
    except FileNotFoundError:
        return b"" if binary else ""
    return proc.stdout if proc.returncode == 0 else (b"" if binary else "")


def source_revision(root: pathlib.Path) -> str:
    """Match the gate engine's revision, including a stable dirty-tree digest."""
    head = str(git_output(root, ["rev-parse", "HEAD"])).strip() or "no-git-revision"
    status_output = str(git_output(root, ["status", "--porcelain", "--untracked-files=all"]))
    changes = [line for line in status_output.splitlines() if line.strip()]
    if not changes:
        return head
    diff = git_output(root, ["diff", "--binary", "HEAD"], binary=True)
    digest = hashlib.sha256("\n".join(changes).encode() + bytes(diff)).hexdigest()[:12]
    return f"{head}-dirty-{digest}"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def as_float(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time(value: Any) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def expected_gates(root: pathlib.Path) -> list[str]:
    harness = read_json(root / "framework" / "harness.json")
    gates = harness.get("gates", {})
    return list(gates) if isinstance(gates, dict) else ["G1", "G2", "G3", "G4"]


def validate_rules(config: dict[str, Any], root: pathlib.Path) -> list[str]:
    errors: list[str] = []
    if config.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")

    channels = config.get("channels")
    if not isinstance(channels, dict) or not channels:
        errors.append("channels must define at least one named channel")
        channels = {}
    for name, channel in channels.items():
        prefix = f"channels.{name}"
        if not isinstance(channel, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        if channel.get("type") != "slack":
            errors.append(f"{prefix}.type must be 'slack'")
        if not str(channel.get("webhook_env", "")).strip():
            errors.append(f"{prefix}.webhook_env is required")
        if channel.get("fallback", "stdout") not in {"stdout", "none"}:
            errors.append(f"{prefix}.fallback must be 'stdout' or 'none'")

    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        errors.append("defaults must be a mapping")
        defaults = {}
    if as_float(defaults.get("repeat_hours", 4), -1) < 0:
        errors.append("defaults.repeat_hours must be zero or greater")

    rules = config.get("rules")
    if not isinstance(rules, dict) or not rules:
        errors.append("rules must define at least one named rule")
        return errors

    gates = set(expected_gates(root))
    for rule_id, rule in rules.items():
        prefix = f"rules.{rule_id}"
        if not isinstance(rule, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        rule_type = str(rule.get("type", ""))
        if rule_type not in RULE_TYPES:
            errors.append(f"{prefix}.type must be one of {', '.join(sorted(RULE_TYPES))}")
        if rule.get("severity", "warning") not in SEVERITIES:
            errors.append(f"{prefix}.severity is invalid")
        if str(rule.get("channel", "")) not in channels:
            errors.append(f"{prefix}.channel does not name a configured channel")
        if not str(rule.get("notify", "")).strip():
            errors.append(f"{prefix}.notify is required")
        if "repeat_hours" in rule and as_float(rule.get("repeat_hours"), -1) < 0:
            errors.append(f"{prefix}.repeat_hours must be zero or greater")

        if rule_type == "gate_status":
            statuses = rule.get("statuses", [])
            if not isinstance(statuses, list) or not statuses or not set(statuses) <= STATUSES:
                errors.append(f"{prefix}.statuses must contain valid gate statuses")
            if as_float(rule.get("after_hours"), -1) < 0:
                errors.append(f"{prefix}.after_hours must be zero or greater")
            if "critical_after_hours" in rule and as_float(
                rule.get("critical_after_hours"), -1
            ) < as_float(rule.get("after_hours"), 0):
                errors.append(f"{prefix}.critical_after_hours must be at least after_hours")
            overrides = rule.get("per_gate", {})
            if overrides and not isinstance(overrides, dict):
                errors.append(f"{prefix}.per_gate must be a mapping")
            elif isinstance(overrides, dict):
                for gate, hours in overrides.items():
                    if gate not in gates or as_float(hours, -1) < 0:
                        errors.append(f"{prefix}.per_gate.{gate} must name a gate and non-negative hours")

        if rule_type == "gate_deadline":
            deadlines = rule.get("deadlines", {})
            if not isinstance(deadlines, dict) or not deadlines:
                errors.append(f"{prefix}.deadlines must map gates to ISO-8601 times")
            elif as_bool(rule.get("enabled"), True):
                for gate, deadline in deadlines.items():
                    if gate not in gates:
                        errors.append(f"{prefix}.deadlines.{gate} is not a configured gate")
                    if parse_time(deadline) is None:
                        errors.append(f"{prefix}.deadlines.{gate} must be an ISO-8601 time")

        if rule_type == "contract_approval":
            if not str(rule.get("watch", "")).strip():
                errors.append(f"{prefix}.watch is required")
            required = rule.get("required_approvers", [])
            if not isinstance(required, list) or not required:
                errors.append(f"{prefix}.required_approvers must be a non-empty list")

        if rule_type == "milestone_risk" and as_bool(rule.get("enabled"), True):
            if parse_time(rule.get("due")) is None:
                errors.append(f"{prefix}.due must be an ISO-8601 time")
            warning = as_float(rule.get("warning_hours"), -1)
            critical = as_float(rule.get("critical_hours"), -1)
            if warning < 0 or critical < 0 or critical > warning:
                errors.append(f"{prefix} requires warning_hours >= critical_hours >= 0")

        if rule_type == "uat_pending":
            if as_float(rule.get("after_hours"), -1) < 0:
                errors.append(f"{prefix}.after_hours must be zero or greater")
            if "critical_after_hours" in rule and as_float(
                rule.get("critical_after_hours"), -1
            ) < as_float(rule.get("after_hours"), 0):
                errors.append(f"{prefix}.critical_after_hours must be at least after_hours")

        if rule_type == "artifact_missing":
            if rule.get("after_gate") not in gates:
                errors.append(f"{prefix}.after_gate must name a configured gate")
            paths = rule.get("paths", [])
            if not isinstance(paths, list) or not paths:
                errors.append(f"{prefix}.paths must be a non-empty list")
    return errors


def gate_is_green(entry: Any, revision: str) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("status") == "pass"
        and entry.get("source_revision") == revision
    )


def make_alert(rule_id: str, event_id: str, rule: dict[str, Any], summary: str, detail: str,
               *, severity: str | None = None) -> Alert:
    return Alert(
        rule_id=rule_id,
        event_id=event_id,
        severity=severity or str(rule.get("severity", "warning")),
        summary=summary,
        detail=detail,
        channel=str(rule.get("channel")),
        notify=str(rule.get("notify")),
    )


def check_gate_status(root: pathlib.Path, rule_id: str, rule: dict[str, Any], now: dt.datetime) -> list[Alert]:
    status = read_json(root / ".grounding" / "gate-status.json")
    tracked = set(rule.get("statuses", []))
    default_hours = as_float(rule.get("after_hours"), 4)
    overrides = rule.get("per_gate", {})
    alerts: list[Alert] = []
    for gate, entry in sorted(status.items()):
        if not isinstance(entry, dict) or entry.get("status") not in tracked:
            continue
        since_raw = entry.get("failed_since") or entry.get("updated_at")
        since = parse_time(since_raw)
        if since is None:
            continue
        threshold = as_float(overrides.get(gate), default_hours) if isinstance(overrides, dict) else default_hours
        elapsed = (now - since).total_seconds() / 3600
        if elapsed >= threshold:
            critical_after = as_float(rule.get("critical_after_hours"), float("inf"))
            severity = "critical" if elapsed >= critical_after else None
            alerts.append(make_alert(
                rule_id, gate, rule,
                f"{gate} has been {entry.get('status')} for {elapsed:.1f}h",
                f"SLA: {threshold:g}h; failing since {since_raw}",
                severity=severity,
            ))
    return alerts


def check_gate_deadline(root: pathlib.Path, rule_id: str, rule: dict[str, Any], now: dt.datetime) -> list[Alert]:
    status = read_json(root / ".grounding" / "gate-status.json")
    revision = source_revision(root)
    alerts: list[Alert] = []
    for gate, raw_deadline in rule.get("deadlines", {}).items():
        deadline = parse_time(raw_deadline)
        if deadline is None or now < deadline or gate_is_green(status.get(gate), revision):
            continue
        overdue = (now - deadline).total_seconds() / 3600
        state = status.get(gate, {}).get("status", "not started")
        alerts.append(make_alert(
            rule_id, gate, rule,
            f"{gate} missed its delivery deadline by {overdue:.1f}h",
            f"Expected green by {raw_deadline}; current state: {state}",
        ))
    return alerts


def check_contract_approval(root: pathlib.Path, rule_id: str, rule: dict[str, Any], now: dt.datetime) -> list[Alert]:
    del now
    watch = str(rule.get("watch"))
    trailer = str(rule.get("approval_trailer", "Approved-by"))
    required = {str(item).strip().lower() for item in rule.get("required_approvers", [])}
    alerts: list[Alert] = []
    dirty = str(git_output(root, ["status", "--short", "--", watch])).strip()
    if dirty:
        alerts.append(make_alert(
            rule_id, "uncommitted", rule,
            f"Delivery contract has uncommitted edits",
            f"{watch}: {dirty}",
        ))

    commit = str(git_output(root, ["log", "-1", "--format=%H", "--", watch])).strip()
    message = str(git_output(root, ["log", "-1", "--format=%B", "--", watch]))
    if not commit or not message:
        return alerts
    prefix = f"{trailer}:".lower()
    actual = {
        line.split(":", 1)[1].strip().lower()
        for line in message.splitlines()
        if line.strip().lower().startswith(prefix) and ":" in line
    }
    missing = sorted(required - actual)
    if missing:
        alerts.append(make_alert(
            rule_id, commit[:12], rule,
            "Delivery contract changed without required approvals",
            f"Commit {commit[:12]} is missing {trailer} trailers for: {', '.join(missing)}",
        ))
    return alerts


def check_milestone_risk(root: pathlib.Path, rule_id: str, rule: dict[str, Any], now: dt.datetime) -> list[Alert]:
    due = parse_time(rule.get("due"))
    if due is None:
        return []
    hours_left = (due - now).total_seconds() / 3600
    if hours_left > as_float(rule.get("warning_hours"), 72):
        return []
    status = read_json(root / ".grounding" / "gate-status.json")
    revision = source_revision(root)
    missing = [gate for gate in expected_gates(root) if not gate_is_green(status.get(gate), revision)]
    if not missing:
        return []
    critical_at = as_float(rule.get("critical_hours"), 24)
    severity = "critical" if hours_left <= critical_at else str(rule.get("severity", "warning"))
    timing = f"{abs(hours_left):.1f}h overdue" if hours_left < 0 else f"due in {hours_left:.1f}h"
    return [make_alert(
        rule_id, "milestone", rule,
        f"Milestone is {timing} with incomplete gates",
        f"Not green for the current revision: {', '.join(missing)}",
        severity=severity,
    )]


def check_uat_pending(root: pathlib.Path, rule_id: str, rule: dict[str, Any], now: dt.datetime) -> list[Alert]:
    gates = expected_gates(root)
    if "G4" not in gates:
        return []
    status = read_json(root / ".grounding" / "gate-status.json")
    revision = source_revision(root)
    internal = [gate for gate in gates if gate != "G4"]
    if not internal or not all(gate_is_green(status.get(gate), revision) for gate in internal):
        return []
    if gate_is_green(status.get("G4"), revision):
        return []
    ready_at = max(
        (parse_time(status[gate].get("updated_at")) for gate in internal),
        default=None,
        key=lambda value: value or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
    )
    if ready_at is None:
        return []
    elapsed = (now - ready_at).total_seconds() / 3600
    threshold = as_float(rule.get("after_hours"), 24)
    if elapsed < threshold:
        return []
    critical_after = as_float(rule.get("critical_after_hours"), float("inf"))
    severity = "critical" if elapsed >= critical_after else None
    return [make_alert(
        rule_id, "G4", rule,
        f"Client UAT has been pending for {elapsed:.1f}h",
        f"Internal gates are green; UAT SLA: {threshold:g}h",
        severity=severity,
    )]


def check_artifact_missing(root: pathlib.Path, rule_id: str, rule: dict[str, Any], now: dt.datetime) -> list[Alert]:
    del now
    gate = str(rule.get("after_gate"))
    status = read_json(root / ".grounding" / "gate-status.json")
    if not gate_is_green(status.get(gate), source_revision(root)):
        return []
    missing = [str(path) for path in rule.get("paths", []) if not (root / str(path)).exists()]
    if not missing:
        return []
    return [make_alert(
        rule_id, gate, rule,
        f"Required delivery evidence is missing after {gate}",
        f"Missing: {', '.join(missing)}",
    )]


CHECKS = {
    "artifact_missing": check_artifact_missing,
    "contract_approval": check_contract_approval,
    "gate_deadline": check_gate_deadline,
    "gate_status": check_gate_status,
    "milestone_risk": check_milestone_risk,
    "uat_pending": check_uat_pending,
}


def evaluate(root: pathlib.Path, config: dict[str, Any], now: dt.datetime) -> list[Alert]:
    alerts: list[Alert] = []
    for rule_id, rule in config.get("rules", {}).items():
        if not as_bool(rule.get("enabled"), True):
            continue
        alerts.extend(CHECKS[str(rule["type"])](root, str(rule_id), rule, now))
    return alerts


def render_alert(project: str, alert: Alert) -> str:
    return (
        f"[{alert.severity.upper()}] {project}: {alert.summary}\n"
        f"Rule: {alert.rule_id} | Notify: {alert.notify}\n{alert.detail}"
    )


def deliver(channel_name: str, channel: dict[str, Any], messages: list[str], *, dry_run: bool) -> bool:
    text = "PSQ delivery escalation\n\n" + "\n\n".join(messages)
    if dry_run:
        print(f"[watcher] DRY RUN channel={channel_name}\n{text}")
        return True
    webhook_env = str(channel.get("webhook_env"))
    webhook = os.environ.get(webhook_env, "").strip()
    if webhook:
        request = urllib.request.Request(
            webhook,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise OSError(f"HTTP {response.status}")
            print(f"[watcher] sent {len(messages)} notification(s) via {channel_name}")
            return True
        except OSError as exc:
            print(f"[watcher] {channel_name} delivery failed ({exc})", file=sys.stderr)
    if channel.get("fallback", "stdout") == "stdout":
        print(text)
        return True
    return False


def run(root: pathlib.Path, config: dict[str, Any], *, dry_run: bool = False,
        now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    alerts = evaluate(root, config, now)
    active = {alert.key: alert for alert in alerts}
    state_config = config.get("state", {})
    state_path = root / str(state_config.get("path", ".grounding/escalation-state.json"))
    state = read_json(state_path)
    previous = state.get("active", {}) if isinstance(state.get("active"), dict) else {}
    defaults = config.get("defaults", {})
    repeat_default = as_float(defaults.get("repeat_hours"), 4)
    send_resolved = as_bool(defaults.get("send_resolved"), True)

    outgoing: dict[str, list[tuple[str, str]]] = {}
    new_state: dict[str, Any] = {}
    now_text = now.isoformat()
    for key, alert in active.items():
        old = previous.get(key, {}) if isinstance(previous.get(key), dict) else {}
        last_notified = parse_time(old.get("last_notified"))
        repeat = as_float(config["rules"][alert.rule_id].get("repeat_hours"), repeat_default)
        severity_changed = old.get("severity") not in {None, alert.severity}
        due = not last_notified or severity_changed or (now - last_notified).total_seconds() >= repeat * 3600
        entry = {
            **asdict(alert),
            "first_seen": old.get("first_seen", now_text),
            "last_seen": now_text,
            "last_notified": old.get("last_notified"),
            "notifications": int(old.get("notifications", 0)),
        }
        if due:
            outgoing.setdefault(alert.channel, []).append((key, render_alert(root.name, alert)))
            entry["last_notified"] = now_text
            entry["notifications"] += 1
        new_state[key] = entry

    if send_resolved:
        for key, old in previous.items():
            if key in active or not isinstance(old, dict):
                continue
            channel_name = str(old.get("channel", ""))
            message = (
                f"[RESOLVED] {root.name}: {old.get('summary', key)}\n"
                f"Rule: {old.get('rule_id', 'unknown')} | Notify: {old.get('notify', '')}"
            )
            if channel_name in config.get("channels", {}):
                outgoing.setdefault(channel_name, []).append((f"resolved:{key}", message))

    delivered_keys: set[str] = set()
    for channel_name, items in outgoing.items():
        channel = config["channels"][channel_name]
        if deliver(channel_name, channel, [message for _, message in items], dry_run=dry_run):
            delivered_keys.update(key for key, _ in items)

    if not dry_run:
        for key, entry in new_state.items():
            if key not in delivered_keys and entry.get("last_notified") == now_text:
                entry["last_notified"] = previous.get(key, {}).get("last_notified")
                entry["notifications"] = int(previous.get(key, {}).get("notifications", 0))
        for key, old in previous.items():
            if key not in active and f"resolved:{key}" not in delivered_keys:
                new_state[key] = old
        write_json_atomic(state_path, {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now_text,
            "active": new_state,
        })

    if active:
        if not outgoing:
            print(f"[watcher] {len(active)} active escalation(s); notifications suppressed until repeat interval")
        return 1
    if outgoing:
        print("[watcher] all clear; resolution notification sent")
    else:
        print("[watcher] all clear")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root")
    parser.add_argument("--rules", default="watcher.rules.yml", help="rules path relative to root")
    parser.add_argument("--validate", action="store_true", help="validate the escalation contract and exit")
    parser.add_argument("--dry-run", action="store_true", help="evaluate and print without sending or writing state")
    parser.add_argument("--loop", type=float, metavar="MINUTES", help="re-check on an interval")
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    rules_path = root / args.rules
    if not rules_path.exists():
        print(f"Escalation contract invalid:\n- missing {rules_path}", file=sys.stderr)
        return 2
    try:
        config = load_simple_yaml(rules_path.read_text(encoding="utf-8"))
    except SystemExit as exc:
        print(f"Escalation contract invalid:\n- {exc}", file=sys.stderr)
        return 2
    errors = validate_rules(config, root)
    if errors:
        print("Escalation contract invalid:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    if args.validate:
        print(f"Escalation contract valid: {rules_path}")
        return 0

    if not args.loop:
        return run(root, config, dry_run=args.dry_run)
    if args.loop <= 0:
        print("--loop must be greater than zero", file=sys.stderr)
        return 2
    print(f"[watcher] checking every {args.loop:g} min (rules: {rules_path})")
    while True:
        run(root, config, dry_run=args.dry_run)
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    sys.exit(main())
