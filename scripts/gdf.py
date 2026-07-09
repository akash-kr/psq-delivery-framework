#!/usr/bin/env python3
"""Grounded Delivery Framework harness.

This script intentionally uses only the Python standard library so the
framework can run before a project-specific toolchain is installed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path.cwd()
HARNESS_PATH = ROOT / "framework" / "harness.json"


def load_harness() -> dict[str, Any]:
    if not HARNESS_PATH.exists():
        raise SystemExit(f"Missing {HARNESS_PATH}")
    try:
        return json.loads(HARNESS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {HARNESS_PATH}: {exc}") from exc


def path_from_config(harness: dict[str, Any], key: str) -> pathlib.Path:
    raw = harness.get("paths", {}).get(key)
    if not raw:
        raise SystemExit(f"Missing paths.{key} in {HARNESS_PATH}")
    return ROOT / raw


def ensure_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_shell(command: str, log_path: pathlib.Path) -> int:
    ensure_dir(log_path.parent)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {command}\n\n")
        log.flush()
        proc = subprocess.run(
            command,
            shell=True,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log.write(f"\nexit_code={proc.returncode}\n")
        return proc.returncode


def verify_contract(strict: bool = False) -> int:
    harness = load_harness()
    required_paths = [
        path_from_config(harness, "contract"),
        path_from_config(harness, "gates"),
        path_from_config(harness, "lanes"),
        ROOT / "framework" / "proof-schema.json",
    ]

    failures: list[str] = []
    for path in required_paths:
        if not path.exists():
            failures.append(f"missing: {path.relative_to(ROOT)}")

    schema_path = ROOT / "framework" / "proof-schema.json"
    if schema_path.exists():
        try:
            json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"invalid JSON: {schema_path.relative_to(ROOT)}: {exc}")

    if strict:
        contract_text = path_from_config(harness, "contract").read_text(encoding="utf-8")
        if "unset" in contract_text:
            failures.append("strict mode: contract still contains placeholder value 'unset'")

    if failures:
        print("Contract verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Contract verification passed.")
    return 0


def command_for_step(harness: dict[str, Any], step: str) -> str | None:
    commands = harness.get("commands", {})
    if step in commands:
        return commands[step] or None
    return None


def run_builtin(step: str) -> int | None:
    if step == "verify_contract":
        return verify_contract(strict=os.environ.get("GDF_STRICT") == "1")
    if step == "collect_proof":
        return collect_proof()
    if step == "write_report":
        return write_report()
    return None


def run_gate(gate_id: str) -> int:
    harness = load_harness()
    gates = harness.get("gates", {})

    if gate_id == "all":
        exit_code = 0
        for gid in gates:
            result = run_gate(gid)
            if result != 0:
                exit_code = result
                break
        return exit_code

    if gate_id not in gates:
        print(f"Unknown gate: {gate_id}")
        print("Known gates: " + ", ".join(gates.keys()))
        return 2

    logs_dir = path_from_config(harness, "logs_dir")
    ensure_dir(logs_dir)

    print(f"Running {gate_id}")
    for step in gates[gate_id]:
        builtin_result = run_builtin(step)
        if builtin_result is not None:
            print(f"- {step}: {'pass' if builtin_result == 0 else 'fail'}")
            if builtin_result != 0:
                return builtin_result
            continue

        command = command_for_step(harness, step)
        if not command:
            print(f"- {step}: skipped (no command configured)")
            continue

        log_path = logs_dir / f"{gate_id}-{step}.log"
        result = run_shell(command, log_path)
        print(f"- {step}: {'pass' if result == 0 else 'fail'} ({log_path.relative_to(ROOT)})")
        if result != 0:
            return result

    print(f"{gate_id} passed.")
    return 0


def git_lines(args: list[str]) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def detect_stack() -> int:
    checks = [
        ("nextjs", ["next.config.js", "next.config.mjs", "next.config.ts"]),
        ("strapi", ["config/admin.js", "config/database.js", "src/api"]),
        ("php-symfony", ["symfony.lock", "config/bundles.php"]),
        ("python", ["pyproject.toml", "requirements.txt", "setup.py"]),
        ("node", ["package.json"]),
        ("static-site", ["index.html", "public/index.html", "site/index.html"]),
    ]

    matches: list[str] = []
    for name, paths in checks:
        if any((ROOT / path).exists() for path in paths):
            matches.append(name)

    if matches:
        print("\n".join(matches))
        return 0

    print("unknown")
    return 1


def collect_proof() -> int:
    harness = load_harness()
    proof_report = path_from_config(harness, "proof_report")
    logs_dir = path_from_config(harness, "logs_dir")
    screenshots_dir = path_from_config(harness, "screenshots_dir")
    ensure_dir(proof_report.parent)
    ensure_dir(logs_dir)
    ensure_dir(screenshots_dir)

    changed = git_lines(["status", "--short"])
    logs = sorted(str(path.relative_to(ROOT)) for path in logs_dir.glob("*.log"))
    screenshots = sorted(str(path.relative_to(ROOT)) for path in screenshots_dir.glob("*"))

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    project_name = harness.get("project", {}).get("name", "unset")

    lines = [
        "# Proof Report",
        "",
        f"- Timestamp: {timestamp}",
        f"- Project: {project_name}",
        "",
        "## Output Delivered",
        "",
    ]
    if changed:
        lines.extend(f"- {line}" for line in changed)
    else:
        lines.append("- No git changes detected.")

    lines.extend(["", "## Proof", ""])
    if logs:
        lines.extend(f"- Log: {path}" for path in logs)
    else:
        lines.append("- No command logs recorded.")
    if screenshots:
        lines.extend(f"- Screenshot: {path}" for path in screenshots)

    lines.extend(
        [
            "",
            "## Risks",
            "",
            "- Review project-specific skipped commands in `framework/harness.json`.",
            "",
            "## Decision Needed",
            "",
            "approve / rework / choose option",
            "",
            "## Recommended Next Issues",
            "",
            "1. Fill project contract and adapter commands - lane:spec",
            "",
        ]
    )

    proof_report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {proof_report.relative_to(ROOT)}")
    return 0


def write_report() -> int:
    harness = load_harness()
    proof_report = path_from_config(harness, "proof_report")
    if not proof_report.exists():
        return collect_proof()
    print(f"Report ready: {proof_report.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gdf")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify-contract")
    verify_parser.add_argument("--strict", action="store_true")

    gate_parser = subparsers.add_parser("run-gate")
    gate_parser.add_argument("gate")

    subparsers.add_parser("collect-proof")
    subparsers.add_parser("write-report")
    subparsers.add_parser("detect-stack")

    args = parser.parse_args()

    if args.command == "verify-contract":
        return verify_contract(strict=args.strict or os.environ.get("GDF_STRICT") == "1")
    if args.command == "run-gate":
        return run_gate(args.gate)
    if args.command == "collect-proof":
        return collect_proof()
    if args.command == "write-report":
        return write_report()
    if args.command == "detect-stack":
        return detect_stack()

    return 2


if __name__ == "__main__":
    sys.exit(main())

