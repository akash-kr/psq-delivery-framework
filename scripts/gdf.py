#!/usr/bin/env python3
"""Grounded Delivery Framework harness.

This script intentionally uses only the Python standard library so the
framework can run before a project-specific toolchain is installed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
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
    "uat_approval": ".grounding/uat-approval.json",
    "milestone_release": ".grounding/milestone-release.json",
}

# Matches a line whose value is the placeholder `unset` (mapping value or
# list item), without flagging prose that merely contains the word.
UNSET_LINE = re.compile(r"^\s*(?:-\s+)?(?:[\w.-]+:\s*)?unset\s*(?:#.*)?$")


def source_revision() -> str:
    """Return a revision identifier that makes stale local gate results visible."""
    head = git_lines(["rev-parse", "HEAD"])
    revision = head[0] if head else "no-git-revision"
    changes = git_lines(["status", "--porcelain", "--untracked-files=all"])
    if changes:
        proc = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        material = "\n".join(changes).encode() + proc.stdout
        digest = hashlib.sha256(material).hexdigest()[:12]
        revision += f"-dirty-{digest}"
    return revision


def contract_metadata(harness: dict[str, Any]) -> tuple[str, str]:
    path = path_from_config(harness, "contract")
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    def value_for(key: str) -> str:
        match = re.search(rf"^\s{{2}}{re.escape(key)}:\s*['\"]?([^'\"#\n]+)", text, re.MULTILINE)
        return match.group(1).strip() if match else ""

    return value_for("id"), value_for("version")


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


def write_json_atomic(path: pathlib.Path, data: Any) -> None:
    ensure_dir(path.parent)
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


def run_shell(command: str, log_path: pathlib.Path, timeout_seconds: float) -> int:
    ensure_dir(log_path.parent)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {command}\n\n")
        log.flush()
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            return_code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            log.write(f"\nTIMEOUT after {timeout_seconds:g}s\nexit_code=124\n")
            return 124
        log.write(f"\nexit_code={return_code}\n")
        return return_code


def verify_contract(strict: bool = False) -> int:
    harness = load_harness()
    required_paths = [
        path_from_config(harness, "contract"),
        path_from_config(harness, "gates"),
        ROOT / "framework" / "proof-schema.json",
        ROOT / "watcher.rules.yml",
        ROOT / "tools" / "escalation-watcher.py",
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
            contract_text = contract_path.read_text(encoding="utf-8")
            for lineno, line in enumerate(contract_text.splitlines(), 1):
                if UNSET_LINE.match(line):
                    failures.append(f"strict mode: placeholder 'unset' at contract line {lineno}")
            for section in ("contract:", "project:", "objective:", "scope:", "acceptance:", "proof:"):
                if section not in contract_text:
                    failures.append(f"strict mode: missing contract section {section}")
            for role in ("design", "engineering", "qa"):
                if not re.search(rf"^\s{{4}}{role}:\s*\S+", contract_text, re.MULTILINE):
                    failures.append(f"strict mode: missing {role} contract approver")
            acceptance_ids = re.findall(r"^\s{2}- id:\s*\S+", contract_text, re.MULTILINE)
            acceptance_proofs = re.findall(r"^\s{4}proof:\s*\S+", contract_text, re.MULTILINE)
            if not acceptance_ids:
                failures.append("strict mode: at least one acceptance criterion is required")
            elif len(acceptance_ids) != len(acceptance_proofs):
                failures.append("strict mode: every acceptance criterion requires named proof")

        configured_gates = list(harness.get("gates", {}))
        if configured_gates != ["G1", "G2", "G3", "G4"]:
            failures.append("strict mode: harness gates must be ordered G1, G2, G3, G4")

        watcher = ROOT / "tools" / "escalation-watcher.py"
        rules = ROOT / "watcher.rules.yml"
        if watcher.exists() and rules.exists():
            escalation_check = subprocess.run(
                [sys.executable, str(watcher), "--root", str(ROOT), "--validate"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if escalation_check.returncode != 0:
                details = escalation_check.stdout.strip().replace("\n", "; ")
                failures.append(f"strict mode: invalid escalation contract: {details}")

    if failures:
        print("Contract verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Contract verification passed.")
    return 0


def verify_uat_approval() -> int:
    harness = load_harness()
    path = path_from_config(harness, "uat_approval")
    if not path.exists():
        print(f"UAT blocked: missing {path.relative_to(ROOT)}")
        return 4
    try:
        approval = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"UAT blocked: invalid JSON in {path.relative_to(ROOT)}: {exc}")
        return 4

    contract_id, contract_version = contract_metadata(harness)
    failures = []
    if approval.get("status") != "approved":
        failures.append("status must be 'approved'")
    if approval.get("contract_id") != contract_id:
        failures.append(f"contract_id must match {contract_id!r}")
    if approval.get("contract_version") != contract_version:
        failures.append(f"contract_version must match {contract_version!r}")
    if approval.get("source_revision") != source_revision():
        failures.append("source_revision must match the revision that passed G1-G3")
    if not str(approval.get("approved_by", "")).strip():
        failures.append("approved_by is required")
    approved_at = str(approval.get("approved_at", "")).strip()
    try:
        if approved_at:
            dt.datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        else:
            raise ValueError
    except ValueError:
        failures.append("approved_at must be an ISO-8601 timestamp")

    if failures:
        print("UAT blocked:")
        for failure in failures:
            print(f"- {failure}")
        return 4
    print(f"UAT approval verified for {contract_id} v{contract_version}.")
    return 0


def approve_uat(approved_by: str, notes: str = "") -> int:
    if not approved_by.strip():
        print("UAT approval requires a non-empty approver identity.")
        return 2
    harness = load_harness()
    contract_id, contract_version = contract_metadata(harness)
    path = path_from_config(harness, "uat_approval")
    ensure_dir(path.parent)
    approval = {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "source_revision": source_revision(),
        "status": "approved",
        "approved_by": approved_by.strip(),
        "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "notes": notes.strip(),
    }
    write_json_atomic(path, approval)
    print(f"Recorded UAT approval: {path.relative_to(ROOT)}")
    return 0


def command_for_step(harness: dict[str, Any], step: str) -> str | None:
    commands = harness.get("commands", {})
    if step in commands:
        return commands[step] or None
    return None


def run_builtin(step: str) -> int | None:
    if step == "verify_contract":
        return verify_contract(strict=True)
    if step == "collect_proof":
        return collect_proof()
    if step == "write_report":
        return write_report()
    if step == "validate_proof":
        return validate_proof()
    if step == "verify_uat_approval":
        return verify_uat_approval()
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
    entry: dict[str, Any] = {
        "status": status,
        "steps": steps,
        "updated_at": now,
        "source_revision": source_revision(),
    }
    if status in {"fail", "blocked"}:
        previous = data.get(gate_id, {})
        entry["failed_since"] = (
            previous.get("failed_since", now)
            if previous.get("status") in {"fail", "blocked"}
            else now
        )
    data[gate_id] = entry
    write_json_atomic(path, data)


def run_gate(gate_id: str) -> int:
    harness = load_harness()
    gates = harness.get("gates", {})

    if gate_id in {"all", "internal"}:
        selected = list(gates) if gate_id == "all" else [gid for gid in gates if gid != "G4"]
        exit_code = 0
        for gid in selected:
            result = run_gate(gid)
            if result != 0:
                exit_code = result
                break
        return exit_code

    if gate_id not in gates:
        print(f"Unknown gate: {gate_id}")
        print("Known gates: " + ", ".join(gates.keys()))
        return 2

    path_from_config(harness, "milestone_release").unlink(missing_ok=True)

    current_revision = source_revision()
    status_path = path_from_config(harness, "gate_status")
    status_data: dict[str, Any] = {}
    if status_path.exists():
        try:
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status_data = {}
    unmet = []
    for required_gate in harness.get("requires", {}).get(gate_id, []):
        prior = status_data.get(required_gate, {})
        if prior.get("status") != "pass":
            unmet.append(f"{required_gate} is not green")
        elif prior.get("source_revision") != current_revision:
            unmet.append(f"{required_gate} is stale for the current revision")
    if unmet:
        steps = {"prerequisites": "blocked"}
        print(f"{gate_id} BLOCKED:")
        for reason in unmet:
            print(f"- {reason}")
        record_gate_status(harness, gate_id, "blocked", steps)
        collect_proof()
        return 4

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
            builtin_status = "pass" if builtin_result == 0 else ("blocked" if builtin_result == 4 else "fail")
            steps_status[step] = builtin_status
            print(f"- {step}: {builtin_status}")
            if builtin_result != 0:
                status = "blocked" if builtin_result == 4 else "fail"
                record_gate_status(harness, gate_id, status, steps_status)
                collect_proof()
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
        timeout_seconds = float(harness.get("command_timeout_seconds", 1800))
        result = run_shell(command, log_path, timeout_seconds)
        steps_status[step] = "pass" if result == 0 else "fail"
        print(f"- {step}: {'pass' if result == 0 else 'fail'} ({log_path.relative_to(ROOT)})")
        if result != 0:
            record_gate_status(harness, gate_id, "fail", steps_status)
            collect_proof()
            return result

    if unconfigured > 0:
        print(f"{gate_id} FAILED: {unconfigured} required step(s) have no command configured.")
        print("A gate with an unconfigured requirement must not pass. Configure commands in")
        print(f"framework/harness.json (try: scripts/gdf.py apply-adapter <name>) or mark")
        print(f"steps that genuinely do not apply with the explicit value \"{SKIP}\".")
        record_gate_status(harness, gate_id, "fail", steps_status)
        collect_proof()
        return 3

    record_gate_status(harness, gate_id, "pass", steps_status)
    if gate_id == "G4":
        write_milestone_release(harness)
    collect_proof()
    if gate_id == "G4" and validate_proof() != 0:
        path_from_config(harness, "milestone_release").unlink(missing_ok=True)
        steps_status["validate_proof"] = "fail"
        record_gate_status(harness, gate_id, "fail", steps_status)
        collect_proof()
        return 1
    if executed == 0:
        print(f"{gate_id} passed (all steps explicitly skipped).")
    else:
        print(f"{gate_id} passed.")
    return 0


def write_milestone_release(harness: dict[str, Any]) -> None:
    contract_id, contract_version = contract_metadata(harness)
    path = path_from_config(harness, "milestone_release")
    ensure_dir(path.parent)
    data = {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "source_revision": source_revision(),
        "released_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "ready_for_invoice",
    }
    write_json_atomic(path, data)
    print(f"Milestone released: {path.relative_to(ROOT)}")


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

    matches: list[tuple[int, int, str]] = []
    for path in sorted(ADAPTERS_DIR.glob("*.yml")):
        adapter = load_simple_yaml(path.read_text(encoding="utf-8"))
        candidates = adapter.get("detect", {}).get("any", [])
        score = sum(1 for candidate in candidates if (ROOT / candidate).exists())
        if score:
            raw_priority = adapter.get("adapter", {}).get("priority", "0")
            try:
                priority = int(raw_priority)
            except (TypeError, ValueError):
                priority = 0
            matches.append((score, priority, path.stem))

    if matches:
        matches.sort(reverse=True)
        print("\n".join(name for _, _, name in matches))
        return 0

    print("unknown")
    return 1


def apply_adapter(name: str, force: bool = False) -> int:
    harness = load_harness()
    adapter = load_adapter(name)
    adapter_commands = adapter.get("commands", {})
    if not isinstance(adapter_commands, dict) or not adapter_commands:
        raise SystemExit(f"Adapter {name} has no commands section")

    harness_commands = harness.setdefault("commands", {})
    previous_adapter = harness.get("project", {}).get("adapter")
    applied: list[str] = []
    kept: list[str] = []
    for step, command in adapter_commands.items():
        current = harness_commands.get(step, "")
        if not force and previous_adapter == name and current and current != command:
            kept.append(step)
            continue
        harness_commands[step] = command
        if current != command:
            applied.append(step)

    harness.setdefault("project", {})["adapter"] = name
    write_json_atomic(HARNESS_PATH, harness)

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
    approval_path = path_from_config(harness, "uat_approval")
    release_path = path_from_config(harness, "milestone_release")
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

    revision = source_revision()
    def effective_status(entry: dict[str, Any]) -> str:
        if entry.get("source_revision") != revision:
            return "blocked"
        status = entry.get("status", "blocked")
        return status if status in {"pass", "fail", "blocked"} else "blocked"

    gate_results = [
        {
            "gate": gate_id,
            "status": effective_status(entry),
            "evidence": [log for log in logs if pathlib.Path(log).name.startswith(f"{gate_id}-")],
        }
        for gate_id, entry in sorted(gate_status.items())
        if gate_id in harness.get("gates", {})
    ]

    expected_gates = list(harness.get("gates", {}))
    milestone_ready = bool(expected_gates) and all(
        gate_status.get(gate_id, {}).get("status") == "pass"
        and gate_status.get(gate_id, {}).get("source_revision") == revision
        for gate_id in expected_gates
    )

    approval = None
    if approval_path.exists():
        try:
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            approval = None

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    project_name = harness.get("project", {}).get("name", "unset")
    contract_id, contract_version = contract_metadata(harness)
    proof_files = logs + screenshots
    if approval_path.exists():
        proof_files.append(str(approval_path.relative_to(ROOT)))
    if release_path.exists():
        proof_files.append(str(release_path.relative_to(ROOT)))

    proof_data = {
        "schema_version": harness.get("schema_version", "0.1.0"),
        "project": project_name,
        "contract_id": contract_id,
        "contract_version": contract_version,
        "source_revision": revision,
        "timestamp": timestamp,
        "output_delivered": changed,
        "proof": proof_files,
        "gate_results": gate_results,
        "risks": [
            (
                f"{gate_id} is stale for the current source revision"
                if entry.get("source_revision") != revision
                else f"{gate_id} is {entry.get('status', 'unknown')}"
            )
            for gate_id, entry in sorted(gate_status.items())
            if gate_id in harness.get("gates", {}) and effective_status(entry) != "pass"
        ],
        "milestone_ready": milestone_ready,
        "uat_approval": approval,
    }
    write_json_atomic(proof_json_path, proof_data)

    lines = [
        "# Proof Report",
        "",
        f"- Timestamp: {timestamp}",
        f"- Project: {project_name}",
        f"- Contract: {contract_id} v{contract_version}",
        f"- Source revision: {revision}",
        f"- Milestone ready: {'yes' if milestone_ready else 'no'}",
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
    if approval_path.exists():
        lines.append(f"- UAT approval: {approval_path.relative_to(ROOT)}")
    if release_path.exists():
        lines.append(f"- Milestone release: {release_path.relative_to(ROOT)}")

    lines.extend(["", "## Risks", ""])
    if proof_data["risks"]:
        lines.extend(f"- {risk}" for risk in proof_data["risks"])
    else:
        lines.append("- None recorded.")
    lines.append("")

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
    expect("contract_id", str)
    expect("contract_version", str)
    expect("source_revision", str)
    expect("timestamp", str)
    expect("output_delivered", list, str)
    expect("proof", list, str)
    expect("gate_results", list, dict)
    expect("risks", list, str)
    expect("milestone_ready", bool)
    if isinstance(data.get("gate_results"), list) and not data["gate_results"]:
        errors.append("gate_results: at least one gate result is required")

    for key in ("project", "contract_id", "contract_version", "source_revision", "timestamp"):
        if isinstance(data.get(key), str) and not data[key].strip():
            errors.append(f"{key}: must not be empty")
    if isinstance(data.get("timestamp"), str):
        try:
            dt.datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        except ValueError:
            errors.append("timestamp: must be ISO-8601")

    valid_statuses = {"pass", "fail", "blocked"}
    for result in data.get("gate_results", []) or []:
        if not isinstance(result, dict) or "gate" not in result or "status" not in result:
            errors.append("gate_results: items require gate and status")
        elif result["status"] not in valid_statuses:
            errors.append(f"gate_results: invalid status {result['status']!r}")

    if data.get("milestone_ready"):
        expected = set(load_harness().get("gates", {}))
        green = {
            result.get("gate")
            for result in data.get("gate_results", [])
            if isinstance(result, dict) and result.get("status") == "pass"
        }
        if green != expected:
            errors.append("milestone_ready requires every configured gate to be present and green")
        approval = data.get("uat_approval")
        if not isinstance(approval, dict) or approval.get("status") != "approved":
            errors.append("milestone_ready requires an approved UAT record")
        release_path = path_from_config(load_harness(), "milestone_release")
        try:
            release = json.loads(release_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append("milestone_ready requires a valid milestone release record")
        else:
            if release.get("source_revision") != data.get("source_revision"):
                errors.append("milestone release revision does not match proof")

    if errors:
        print("Proof validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Proof validation passed.")
    return 0


def write_report() -> int:
    return collect_proof()


def main() -> int:
    parser = argparse.ArgumentParser(prog="gdf")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify-contract")
    verify_parser.add_argument("--strict", action="store_true")

    gate_parser = subparsers.add_parser("run-gate")
    gate_parser.add_argument("gate")

    adapter_parser = subparsers.add_parser("apply-adapter")
    adapter_parser.add_argument("name")
    adapter_parser.add_argument("--force", action="store_true")

    subparsers.add_parser("collect-proof")
    subparsers.add_parser("validate-proof")
    subparsers.add_parser("write-report")
    subparsers.add_parser("detect-stack")
    uat_parser = subparsers.add_parser("approve-uat")
    uat_parser.add_argument("--approved-by", required=True)
    uat_parser.add_argument("--notes", default="")

    args = parser.parse_args()

    if args.command == "verify-contract":
        return verify_contract(strict=args.strict or os.environ.get("GDF_STRICT") == "1")
    if args.command == "run-gate":
        return run_gate(args.gate)
    if args.command == "apply-adapter":
        return apply_adapter(args.name, force=args.force)
    if args.command == "collect-proof":
        return collect_proof()
    if args.command == "validate-proof":
        return validate_proof()
    if args.command == "write-report":
        return write_report()
    if args.command == "detect-stack":
        return detect_stack()
    if args.command == "approve-uat":
        return approve_uat(args.approved_by, args.notes)

    return 2


if __name__ == "__main__":
    sys.exit(main())
