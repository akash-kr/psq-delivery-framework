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
import re
import subprocess
import sys
from typing import Any


def find_root() -> pathlib.Path:
    """Walk up from cwd to the nearest directory containing framework/harness.json."""
    cur = pathlib.Path.cwd()
    for candidate in (cur, *cur.parents):
        if (candidate / "framework" / "harness.json").exists():
            return candidate
    return cur


ROOT = find_root()
HARNESS_PATH = ROOT / "framework" / "harness.json"
ADAPTERS_DIR = ROOT / "adapters"

# A command set to this literal value is a recorded human decision that the
# step does not apply to the stack (e.g. a static site has no build). Unlike
# an empty string ("" = not configured yet), an explicit skip can never make
# a gate pass vacuously.
SKIP = "skip"

DEFAULT_PATHS = {
    "proof_json": ".grounding/proof-report.json",
    "gate_status": ".grounding/gate-status.json",
}

# Matches a line whose value is the placeholder `unset` (mapping value or
# list item), without flagging prose that merely contains the word.
UNSET_LINE = re.compile(r"^\s*(?:-\s+)?(?:[\w.-]+:\s*)?unset\s*(?:#.*)?$")


def load_harness() -> dict[str, Any]:
    if not HARNESS_PATH.exists():
        raise SystemExit(f"Missing {HARNESS_PATH}")
    try:
        return json.loads(HARNESS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {HARNESS_PATH}: {exc}") from exc


def path_from_config(harness: dict[str, Any], key: str) -> pathlib.Path:
    raw = harness.get("paths", {}).get(key) or DEFAULT_PATHS.get(key)
    if not raw:
        raise SystemExit(f"Missing paths.{key} in {HARNESS_PATH}")
    return ROOT / raw


def ensure_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _yaml_scalar(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] in "\"'" and token.endswith(token[0]):
        return token[1:-1]
    return token


def load_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by adapters and watcher rules:
    nested string-keyed maps, lists of scalars, scalar values, and
    full-line comments. Not a general YAML parser."""
    root: dict[str, Any] = {}
    # Stack entries: [indent, container, parent, key_in_parent]
    stack: list[list[Any]] = [[-1, root, None, None]]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        top = stack[-1]
        container = top[1]
        if line.startswith("- "):
            if isinstance(container, dict):
                if container or top[2] is None:
                    raise SystemExit(f"Cannot parse YAML line: {raw!r}")
                container = []
                top[2][top[3]] = container
                top[1] = container
            container.append(_yaml_scalar(line[2:]))
        elif line.endswith(":"):
            key = _yaml_scalar(line[:-1])
            child: dict[str, Any] = {}
            container[key] = child
            stack.append([indent, child, container, key])
        else:
            key, _, value = line.partition(":")
            container[_yaml_scalar(key)] = _yaml_scalar(value)
    return root


def load_adapter(name: str) -> dict[str, Any]:
    path = ADAPTERS_DIR / f"{name}.yml"
    if not path.exists():
        known = sorted(p.stem for p in ADAPTERS_DIR.glob("*.yml")) if ADAPTERS_DIR.is_dir() else []
        raise SystemExit(f"Unknown adapter: {name}. Known adapters: {', '.join(known) or 'none'}")
    return load_simple_yaml(path.read_text(encoding="utf-8"))


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
        contract_path = path_from_config(harness, "contract")
        if contract_path.exists():
            for lineno, line in enumerate(contract_path.read_text(encoding="utf-8").splitlines(), 1):
                if UNSET_LINE.match(line):
                    failures.append(f"strict mode: placeholder 'unset' at contract line {lineno}")

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
    if step == "validate_proof":
        return validate_proof()
    return None


def record_gate_status(
    harness: dict[str, Any], gate_id: str, status: str, steps: dict[str, str]
) -> None:
    path = path_from_config(harness, "gate_status")
    ensure_dir(path.parent)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    entry: dict[str, Any] = {"status": status, "steps": steps, "updated_at": now}
    if status == "fail":
        previous = data.get(gate_id, {})
        entry["failed_since"] = (
            previous.get("failed_since", now) if previous.get("status") == "fail" else now
        )
    data[gate_id] = entry
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
    steps_status: dict[str, str] = {}
    executed = 0
    unconfigured = 0
    for step in gates[gate_id]:
        builtin_result = run_builtin(step)
        if builtin_result is not None:
            executed += 1
            steps_status[step] = "pass" if builtin_result == 0 else "fail"
            print(f"- {step}: {'pass' if builtin_result == 0 else 'fail'}")
            if builtin_result != 0:
                record_gate_status(harness, gate_id, "fail", steps_status)
                return builtin_result
            continue

        command = command_for_step(harness, step)
        if command == SKIP:
            steps_status[step] = "skipped"
            print(f"- {step}: skipped (explicit '{SKIP}' in harness)")
            continue
        if not command:
            unconfigured += 1
            steps_status[step] = "skipped"
            print(f"- {step}: skipped (no command configured)")
            continue

        executed += 1
        log_path = logs_dir / f"{gate_id}-{step}.log"
        result = run_shell(command, log_path)
        steps_status[step] = "pass" if result == 0 else "fail"
        print(f"- {step}: {'pass' if result == 0 else 'fail'} ({log_path.relative_to(ROOT)})")
        if result != 0:
            record_gate_status(harness, gate_id, "fail", steps_status)
            return result

    if executed == 0 and unconfigured > 0:
        print(f"{gate_id} FAILED: 0 checks ran ({unconfigured} step(s) have no command configured).")
        print("A gate that verifies nothing must not pass. Either configure commands in")
        print(f"framework/harness.json (try: scripts/gdf.py apply-adapter <name>) or mark")
        print(f"steps that genuinely do not apply with the explicit value \"{SKIP}\".")
        record_gate_status(harness, gate_id, "fail", steps_status)
        return 3

    record_gate_status(harness, gate_id, "pass", steps_status)
    if executed == 0:
        print(f"{gate_id} passed (all steps explicitly skipped).")
    else:
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
    if not ADAPTERS_DIR.is_dir():
        print("unknown")
        print("No adapters/ directory found; copy adapters from the framework template.", file=sys.stderr)
        return 1

    matches: list[str] = []
    for path in sorted(ADAPTERS_DIR.glob("*.yml")):
        adapter = load_simple_yaml(path.read_text(encoding="utf-8"))
        candidates = adapter.get("detect", {}).get("any", [])
        if any((ROOT / candidate).exists() for candidate in candidates):
            matches.append(path.stem)

    if matches:
        print("\n".join(matches))
        return 0

    print("unknown")
    return 1


def apply_adapter(name: str) -> int:
    harness = load_harness()
    adapter = load_adapter(name)
    adapter_commands = adapter.get("commands", {})
    if not isinstance(adapter_commands, dict) or not adapter_commands:
        raise SystemExit(f"Adapter {name} has no commands section")

    harness_commands = harness.setdefault("commands", {})
    applied: list[str] = []
    kept: list[str] = []
    for step, command in adapter_commands.items():
        current = harness_commands.get(step, "")
        if current and current != command:
            kept.append(step)
            continue
        harness_commands[step] = command
        if current != command:
            applied.append(step)

    harness.setdefault("project", {})["adapter"] = name
    HARNESS_PATH.write_text(json.dumps(harness, indent=2) + "\n", encoding="utf-8")

    print(f"Applied adapter '{name}' to framework/harness.json")
    if applied:
        print("- set: " + ", ".join(applied))
    if kept:
        print("- kept existing (customized) commands: " + ", ".join(kept))
    return 0


def collect_proof() -> int:
    harness = load_harness()
    proof_report = path_from_config(harness, "proof_report")
    proof_json_path = path_from_config(harness, "proof_json")
    logs_dir = path_from_config(harness, "logs_dir")
    screenshots_dir = path_from_config(harness, "screenshots_dir")
    gate_status_path = path_from_config(harness, "gate_status")
    ensure_dir(proof_report.parent)
    ensure_dir(logs_dir)
    ensure_dir(screenshots_dir)

    changed = git_lines(["status", "--short"])
    logs = sorted(str(path.relative_to(ROOT)) for path in logs_dir.glob("*.log"))
    screenshots = sorted(str(path.relative_to(ROOT)) for path in screenshots_dir.glob("*"))

    gate_status: dict[str, Any] = {}
    if gate_status_path.exists():
        try:
            gate_status = json.loads(gate_status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gate_status = {}

    gate_results = [
        {
            "gate": gate_id,
            "status": entry.get("status", "skipped"),
            "evidence": [log for log in logs if pathlib.Path(log).name.startswith(f"{gate_id}-")],
        }
        for gate_id, entry in sorted(gate_status.items())
    ]

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    project_name = harness.get("project", {}).get("name", "unset")

    proof_data = {
        "schema_version": harness.get("schema_version", "0.1.0"),
        "project": project_name,
        "timestamp": timestamp,
        "output_delivered": changed,
        "proof": logs + screenshots,
        "gate_results": gate_results,
        "risks": [],
        "decision_needed": "approve / rework / choose option",
        "recommended_next_issues": [],
    }
    proof_json_path.write_text(json.dumps(proof_data, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Proof Report",
        "",
        f"- Timestamp: {timestamp}",
        f"- Project: {project_name}",
        "",
        "## Gate Results",
        "",
    ]
    if gate_results:
        for result in gate_results:
            entry = gate_status.get(result["gate"], {})
            steps = entry.get("steps", {})
            step_summary = ", ".join(f"{step}={status}" for step, status in steps.items())
            lines.append(f"- {result['gate']}: {result['status']}" + (f" ({step_summary})" if step_summary else ""))
    else:
        lines.append("- No gates have run. Run `scripts/run-gate all` before collecting proof.")

    lines.extend(["", "## Output Delivered", ""])
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
            "- None recorded. Add project-specific risks before requesting review.",
            "",
            "## Decision Needed",
            "",
            "approve / rework / choose option",
            "",
        ]
    )

    proof_report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {proof_report.relative_to(ROOT)}")
    print(f"Wrote {proof_json_path.relative_to(ROOT)}")
    return 0


def validate_proof() -> int:
    """Validate the JSON proof report against the shape required by
    framework/proof-schema.json (required keys and types, hand-checked so the
    framework stays stdlib-only)."""
    harness = load_harness()
    proof_json_path = path_from_config(harness, "proof_json")
    if not proof_json_path.exists():
        print(f"Proof validation failed: missing {proof_json_path.relative_to(ROOT)}")
        return 1
    try:
        data = json.loads(proof_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Proof validation failed: invalid JSON: {exc}")
        return 1

    errors: list[str] = []

    def expect(key: str, kind: type, item_kind: type | None = None) -> None:
        if key not in data:
            errors.append(f"missing required key: {key}")
            return
        if not isinstance(data[key], kind):
            errors.append(f"{key}: expected {kind.__name__}")
            return
        if item_kind is not None and not all(isinstance(item, item_kind) for item in data[key]):
            errors.append(f"{key}: items must be {item_kind.__name__}")

    expect("schema_version", str)
    expect("project", str)
    expect("timestamp", str)
    expect("output_delivered", list, str)
    expect("proof", list, str)
    expect("decision_needed", str)
    expect("recommended_next_issues", list, dict)

    for issue in data.get("recommended_next_issues", []) or []:
        if isinstance(issue, dict) and not ("title" in issue and "lane" in issue):
            errors.append("recommended_next_issues: items require title and lane")

    valid_statuses = {"pass", "fail", "blocked", "skipped"}
    for result in data.get("gate_results", []) or []:
        if not isinstance(result, dict) or "gate" not in result or "status" not in result:
            errors.append("gate_results: items require gate and status")
        elif result["status"] not in valid_statuses:
            errors.append(f"gate_results: invalid status {result['status']!r}")

    if errors:
        print("Proof validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Proof validation passed.")
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

    adapter_parser = subparsers.add_parser("apply-adapter")
    adapter_parser.add_argument("name")

    subparsers.add_parser("collect-proof")
    subparsers.add_parser("validate-proof")
    subparsers.add_parser("write-report")
    subparsers.add_parser("detect-stack")

    args = parser.parse_args()

    if args.command == "verify-contract":
        return verify_contract(strict=args.strict or os.environ.get("GDF_STRICT") == "1")
    if args.command == "run-gate":
        return run_gate(args.gate)
    if args.command == "apply-adapter":
        return apply_adapter(args.name)
    if args.command == "collect-proof":
        return collect_proof()
    if args.command == "validate-proof":
        return validate_proof()
    if args.command == "write-report":
        return write_report()
    if args.command == "detect-stack":
        return detect_stack()

    return 2


if __name__ == "__main__":
    sys.exit(main())
