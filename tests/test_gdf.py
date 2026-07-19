"""Regression tests for the PSQ delivery harness."""

import json
import datetime as dt
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
GDF = REPO / "scripts" / "gdf.py"
WATCHER = REPO / "tools" / "escalation-watcher.py"

MINIMAL_CONTRACT = """contract:
  id: test-contract
  version: 1.0.0
  approvers:
    design: design-owner
    engineering: engineering-owner
    qa: qa-owner
project:
  name: test-project
objective:
  summary: test outcome
scope:
  in:
    - test
acceptance:
  - id: AC-001
    statement: test
    proof: test output
proof:
  required_files:
    - proof
"""

DECK_GATES = {"G1": [], "G2": [], "G3": [], "G4": []}


def make_project(tmp: pathlib.Path, commands=None, gates=None, requires=None, contract=MINIMAL_CONTRACT):
    framework = tmp / "framework"
    framework.mkdir(parents=True, exist_ok=True)
    (tmp / "scripts").mkdir(exist_ok=True)
    (tmp / "tools").mkdir(exist_ok=True)
    harness = {
        "schema_version": "1.0.0",
        "project": {"name": "test-project", "type": "test", "adapter": "unset"},
        "paths": {
            "contract": "framework/contract.yml",
            "gates": "framework/gates.yml",
            "proof_report": ".grounding/proof-report.md",
            "proof_json": ".grounding/proof-report.json",
            "gate_status": ".grounding/gate-status.json",
            "uat_approval": ".grounding/uat-approval.json",
            "milestone_release": ".grounding/milestone-release.json",
            "logs_dir": ".grounding/logs",
            "screenshots_dir": ".grounding/screenshots",
        },
        "commands": commands or {},
        "gates": gates or {"G1": ["verify_contract"]},
        "requires": requires or {},
    }
    (framework / "harness.json").write_text(json.dumps(harness, indent=2))
    (framework / "contract.yml").write_text(contract)
    shutil.copy(REPO / "framework" / "gates.yml", framework / "gates.yml")
    shutil.copy(REPO / "framework" / "proof-schema.json", framework / "proof-schema.json")
    shutil.copy(GDF, tmp / "scripts" / "gdf.py")
    shutil.copy(WATCHER, tmp / "tools" / "escalation-watcher.py")
    shutil.copy(REPO / "watcher.rules.yml", tmp / "watcher.rules.yml")
    (tmp / ".gitignore").write_text(".grounding/\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp, check=True)
    return tmp


def run_gdf(cwd: pathlib.Path, *args: str):
    return subprocess.run(
        [sys.executable, str(GDF), *args], cwd=cwd, capture_output=True, text=True
    )


class GateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_unconfigured_gate_fails_instead_of_vacuous_green(self):
        make_project(self.tmp, commands={"lint": ""}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("required step(s)", result.stdout)

    def test_one_green_step_cannot_hide_an_unconfigured_requirement(self):
        make_project(
            self.tmp,
            commands={"lint": "echo ok", "build": ""},
            gates={"G1": ["lint", "build"]},
        )
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("build: skipped (no command configured)", result.stdout)

    def test_explicit_skip_is_a_recorded_not_applicable_decision(self):
        make_project(self.tmp, commands={"lint": "skip"}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("explicitly skipped", result.stdout)

    def test_executed_step_records_log_revision_and_status(self):
        make_project(self.tmp, commands={"lint": "echo ok"}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.tmp / ".grounding/logs/G1-lint.log").exists())
        status = json.loads((self.tmp / ".grounding/gate-status.json").read_text())
        self.assertEqual(status["G1"]["status"], "pass")
        self.assertTrue(status["G1"]["source_revision"])

    def test_failing_command_records_failure_age(self):
        make_project(self.tmp, commands={"lint": "exit 1"}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertNotEqual(result.returncode, 0)
        status = json.loads((self.tmp / ".grounding/gate-status.json").read_text())
        self.assertEqual(status["G1"]["status"], "fail")
        self.assertIn("failed_since", status["G1"])

    def test_command_timeout_fails_and_kills_the_gate(self):
        make_project(self.tmp, commands={"check": "sleep 2"}, gates={"G1": ["check"]})
        harness_path = self.tmp / "framework/harness.json"
        harness = json.loads(harness_path.read_text())
        harness["command_timeout_seconds"] = 0.05
        harness_path.write_text(json.dumps(harness, indent=2))
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        log = (self.tmp / ".grounding/logs/G1-check.log").read_text()
        self.assertIn("TIMEOUT", log)

    def test_later_gate_requires_fresh_green_prerequisite(self):
        make_project(
            self.tmp,
            commands={"check": "echo ok"},
            gates={"G1": ["check"], "G2": ["check"]},
            requires={"G2": ["G1"]},
        )
        blocked = run_gdf(self.tmp, "run-gate", "G2")
        self.assertEqual(blocked.returncode, 4, blocked.stdout + blocked.stderr)
        self.assertIn("G1 is not green", blocked.stdout)

        self.assertEqual(run_gdf(self.tmp, "run-gate", "G1").returncode, 0)
        self.assertEqual(run_gdf(self.tmp, "run-gate", "G2").returncode, 0)

        (self.tmp / "framework/contract.yml").write_text(MINIMAL_CONTRACT + "# changed\n")
        stale = run_gdf(self.tmp, "run-gate", "G2")
        self.assertEqual(stale.returncode, 4, stale.stdout + stale.stderr)
        self.assertIn("stale", stale.stdout)
        proof = json.loads((self.tmp / ".grounding/proof-report.json").read_text())
        effective = {entry["gate"]: entry["status"] for entry in proof["gate_results"]}
        self.assertEqual(effective["G1"], "blocked")
        self.assertTrue(any("stale" in risk for risk in proof["risks"]))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_strict_flags_unset_values_and_missing_sections(self):
        make_project(self.tmp, gates=DECK_GATES, contract="project:\n  name: unset\n")
        result = run_gdf(self.tmp, "verify-contract", "--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("placeholder 'unset'", result.stdout)
        self.assertIn("missing contract section", result.stdout)

    def test_strict_ignores_unset_in_prose(self):
        contract = MINIMAL_CONTRACT.replace("test outcome", "handles unset browser flags")
        make_project(self.tmp, gates=DECK_GATES, contract=contract)
        result = run_gdf(self.tmp, "verify-contract", "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        make_project(self.tmp)
        shutil.copytree(REPO / "adapters", self.tmp / "adapters")

    def test_switching_adapter_replaces_previous_defaults(self):
        harness_path = self.tmp / "framework/harness.json"
        harness = json.loads(harness_path.read_text())
        harness["commands"] = {"install": "skip"}
        harness_path.write_text(json.dumps(harness, indent=2))
        result = run_gdf(self.tmp, "apply-adapter", "nextjs")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        harness = json.loads(harness_path.read_text())
        self.assertIn("npm ci", harness["commands"]["install"])
        self.assertEqual(harness["commands"]["visual_test"], "npm run test:visual")
        self.assertEqual(harness["commands"]["ai_review"], "")
        self.assertEqual(harness["project"]["adapter"], "nextjs")

    def test_reapplying_same_adapter_keeps_customized_commands(self):
        harness_path = self.tmp / "framework/harness.json"
        self.assertEqual(run_gdf(self.tmp, "apply-adapter", "nextjs").returncode, 0)
        harness = json.loads(harness_path.read_text())
        harness["commands"]["install"] = "pnpm install --frozen-lockfile"
        harness_path.write_text(json.dumps(harness, indent=2))
        result = run_gdf(self.tmp, "apply-adapter", "nextjs")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        harness = json.loads(harness_path.read_text())
        self.assertEqual(harness["commands"]["install"], "pnpm install --frozen-lockfile")

    def test_detect_stack_reads_adapter_detect_blocks(self):
        (self.tmp / "index.html").write_text("<!doctype html><title>x</title>")
        result = run_gdf(self.tmp, "detect-stack")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("static-site", result.stdout)


class ProofAndUatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        make_project(
            self.tmp,
            commands={"check": "echo ok"},
            gates={
                "G1": ["check"],
                "G2": ["check"],
                "G3": ["check"],
                "G4": ["verify_uat_approval"],
            },
            requires={"G2": ["G1"], "G3": ["G1", "G2"], "G4": ["G1", "G2", "G3"]},
        )

    def test_pending_uat_blocks_milestone(self):
        self.assertEqual(run_gdf(self.tmp, "run-gate", "internal").returncode, 0)
        result = run_gdf(self.tmp, "run-gate", "G4")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("UAT blocked", result.stdout)
        self.assertFalse((self.tmp / ".grounding/milestone-release.json").exists())

    def test_approved_uat_releases_milestone_and_validates_proof(self):
        self.assertEqual(run_gdf(self.tmp, "run-gate", "internal").returncode, 0)
        approval = run_gdf(self.tmp, "approve-uat", "--approved-by", "client@example.com")
        self.assertEqual(approval.returncode, 0, approval.stdout + approval.stderr)
        result = run_gdf(self.tmp, "run-gate", "G4")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        release = json.loads((self.tmp / ".grounding/milestone-release.json").read_text())
        self.assertEqual(release["status"], "ready_for_invoice")
        proof = json.loads((self.tmp / ".grounding/proof-report.json").read_text())
        self.assertTrue(proof["milestone_ready"])
        self.assertEqual(run_gdf(self.tmp, "validate-proof").returncode, 0)

    def test_validate_proof_fails_without_report(self):
        result = run_gdf(self.tmp, "validate-proof")
        self.assertEqual(result.returncode, 1)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        make_project(
            self.tmp,
            commands={"check": "echo ok"},
            gates={"G1": ["check"], "G2": ["check"], "G3": ["check"], "G4": ["check"]},
        )

    def write_rules(self, rules: str, defaults: str = "repeat_hours: 4\n  send_resolved: true"):
        (self.tmp / "watcher.rules.yml").write_text(
            "schema_version: 1.0.0\n"
            "state:\n  path: .grounding/escalation-state.json\n"
            f"defaults:\n  {defaults}\n"
            "channels:\n"
            "  delivery:\n"
            "    type: slack\n"
            "    webhook_env: TEST_SLACK_WEBHOOK\n"
            "    fallback: stdout\n"
            f"rules:\n{rules}"
        )

    def run_watcher(self, *args: str):
        return subprocess.run(
            [sys.executable, str(WATCHER), "--root", str(self.tmp), *args],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def test_invalid_escalation_contract_fails_closed(self):
        (self.tmp / "watcher.rules.yml").write_text("schema_version: 0.1\n")
        result = self.run_watcher("--validate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Escalation contract invalid", result.stderr)

    def test_gate_deadline_catches_a_gate_that_never_started(self):
        self.write_rules(
            "  missed-gate:\n"
            "    type: gate_deadline\n"
            "    enabled: true\n"
            "    deadlines:\n"
            "      G2: 2000-01-01\n"
            "    severity: high\n"
            "    channel: delivery\n"
            "    notify: delivery owner\n"
        )
        result = self.run_watcher("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("G2 missed its delivery deadline", result.stdout)
        self.assertIn("current state: not started", result.stdout)

    def test_watcher_and_gate_engine_share_revision_freshness(self):
        self.write_rules(
            "  evidence:\n"
            "    type: artifact_missing\n"
            "    enabled: true\n"
            "    after_gate: G1\n"
            "    paths:\n"
            "      - expected-evidence.json\n"
            "    severity: high\n"
            "    channel: delivery\n"
            "    notify: QA owner\n"
        )
        gate = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        result = self.run_watcher("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Required delivery evidence is missing after G1", result.stdout)

    def test_active_alerts_are_deduplicated_and_resolution_is_sent(self):
        self.write_rules(
            "  stuck:\n"
            "    type: gate_status\n"
            "    enabled: true\n"
            "    statuses:\n"
            "      - fail\n"
            "    after_hours: 0\n"
            "    critical_after_hours: 1\n"
            "    severity: high\n"
            "    channel: delivery\n"
            "    notify: gate owner\n"
        )
        grounding = self.tmp / ".grounding"
        grounding.mkdir(exist_ok=True)
        recent = dt.datetime.now(dt.timezone.utc).isoformat()
        (grounding / "gate-status.json").write_text(json.dumps({
            "G1": {
                "status": "fail",
                "failed_since": recent,
                "updated_at": recent,
            }
        }))

        first = self.run_watcher()
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertIn("G1 has been fail", first.stdout)
        second = self.run_watcher()
        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertIn("notifications suppressed", second.stdout)
        self.assertNotIn("PSQ delivery escalation", second.stdout)

        status = json.loads((grounding / "gate-status.json").read_text())
        status["G1"]["failed_since"] = "2000-01-01T00:00:00+00:00"
        (grounding / "gate-status.json").write_text(json.dumps(status))
        escalated = self.run_watcher()
        self.assertEqual(escalated.returncode, 1, escalated.stdout + escalated.stderr)
        self.assertIn("[CRITICAL]", escalated.stdout)

        status["G1"]["status"] = "pass"
        (grounding / "gate-status.json").write_text(json.dumps(status))
        resolved = self.run_watcher()
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertIn("[RESOLVED]", resolved.stdout)

    def test_milestone_risk_names_missing_and_stale_gates(self):
        self.write_rules(
            "  milestone:\n"
            "    type: milestone_risk\n"
            "    enabled: true\n"
            "    due: 2000-01-01\n"
            "    warning_hours: 72\n"
            "    critical_hours: 24\n"
            "    severity: warning\n"
            "    channel: delivery\n"
            "    notify: owner\n"
        )
        result = self.run_watcher("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("G1, G2, G3, G4", result.stdout)
        self.assertIn("[CRITICAL]", result.stdout)


if __name__ == "__main__":
    unittest.main()
