"""Tests for the gdf harness. Run with: python3 -m unittest discover -s tests"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
GDF = REPO / "scripts" / "gdf.py"

MINIMAL_CONTRACT = """contract:
  id: test-contract
project:
  name: test-project
"""


def make_project(tmp: pathlib.Path, commands=None, gates=None, contract=MINIMAL_CONTRACT):
    framework = tmp / "framework"
    framework.mkdir(parents=True, exist_ok=True)
    harness = {
        "schema_version": "0.1.0",
        "project": {"name": "test-project", "type": "test", "adapter": "unset"},
        "paths": {
            "contract": "framework/contract.yml",
            "gates": "framework/gates.yml",
            "lanes": "framework/lanes.yml",
            "proof_report": ".grounding/proof-report.md",
            "logs_dir": ".grounding/logs",
            "screenshots_dir": ".grounding/screenshots",
        },
        "commands": commands or {},
        "gates": gates or {"G0": ["verify_contract"]},
    }
    (framework / "harness.json").write_text(json.dumps(harness, indent=2))
    (framework / "contract.yml").write_text(contract)
    shutil.copy(REPO / "framework" / "gates.yml", framework / "gates.yml")
    shutil.copy(REPO / "framework" / "lanes.yml", framework / "lanes.yml")
    shutil.copy(REPO / "framework" / "proof-schema.json", framework / "proof-schema.json")
    return tmp


def run_gdf(cwd: pathlib.Path, *args: str):
    return subprocess.run(
        [sys.executable, str(GDF), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
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
        self.assertIn("0 checks ran", result.stdout)

    def test_explicit_skip_passes(self):
        make_project(self.tmp, commands={"lint": "skip"}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("explicitly skipped", result.stdout)

    def test_executed_step_passes_and_records_status(self):
        make_project(self.tmp, commands={"lint": "echo ok"}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.tmp / ".grounding" / "logs" / "G1-lint.log").exists())
        status = json.loads((self.tmp / ".grounding" / "gate-status.json").read_text())
        self.assertEqual(status["G1"]["status"], "pass")
        self.assertEqual(status["G1"]["steps"]["lint"], "pass")

    def test_failing_command_fails_gate_with_failed_since(self):
        make_project(self.tmp, commands={"lint": "exit 1"}, gates={"G1": ["lint"]})
        result = run_gdf(self.tmp, "run-gate", "G1")
        self.assertNotEqual(result.returncode, 0)
        status = json.loads((self.tmp / ".grounding" / "gate-status.json").read_text())
        self.assertEqual(status["G1"]["status"], "fail")
        self.assertIn("failed_since", status["G1"])


class ContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_strict_flags_unset_values(self):
        make_project(self.tmp, contract="project:\n  name: unset\n")
        result = run_gdf(self.tmp, "verify-contract", "--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("placeholder 'unset'", result.stdout)

    def test_strict_ignores_unset_in_prose(self):
        make_project(
            self.tmp,
            contract='project:\n  name: demo\nobjective:\n  summary: "handles unset browser flags"\n',
        )
        result = run_gdf(self.tmp, "verify-contract", "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        make_project(self.tmp)
        shutil.copytree(REPO / "adapters", self.tmp / "adapters")

    def test_apply_adapter_fills_empty_commands(self):
        result = run_gdf(self.tmp, "apply-adapter", "nextjs")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        harness = json.loads((self.tmp / "framework" / "harness.json").read_text())
        self.assertEqual(harness["commands"]["install"], "npm install")
        self.assertEqual(harness["project"]["adapter"], "nextjs")

    def test_apply_adapter_keeps_customized_commands(self):
        harness_path = self.tmp / "framework" / "harness.json"
        harness = json.loads(harness_path.read_text())
        harness["commands"] = {"install": "pnpm install"}
        harness_path.write_text(json.dumps(harness, indent=2))
        result = run_gdf(self.tmp, "apply-adapter", "nextjs")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        harness = json.loads(harness_path.read_text())
        self.assertEqual(harness["commands"]["install"], "pnpm install")
        self.assertEqual(harness["commands"]["build"], "npm run build")

    def test_detect_stack_reads_adapter_detect_blocks(self):
        (self.tmp / "index.html").write_text("<!doctype html><title>x</title>")
        result = run_gdf(self.tmp, "detect-stack")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("static-site", result.stdout)


class ProofTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        make_project(
            self.tmp,
            commands={"lint": "echo ok"},
            gates={"G1": ["lint"], "G3": ["collect_proof"], "G4": ["write_report", "validate_proof"]},
        )

    def test_collect_proof_emits_valid_json_with_gate_results(self):
        run_gdf(self.tmp, "run-gate", "G1")
        result = run_gdf(self.tmp, "run-gate", "G3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proof = json.loads((self.tmp / ".grounding" / "proof-report.json").read_text())
        gates = {entry["gate"]: entry["status"] for entry in proof["gate_results"]}
        self.assertEqual(gates.get("G1"), "pass")

        result = run_gdf(self.tmp, "run-gate", "G4")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Proof validation passed", result.stdout)

    def test_validate_proof_fails_without_report(self):
        result = run_gdf(self.tmp, "validate-proof")
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
