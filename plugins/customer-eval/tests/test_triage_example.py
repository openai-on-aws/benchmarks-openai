"""Keep the published triage starter usable through the public offline workflow."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
EXAMPLE = PLUGIN / "examples/support-ticket-triage"


class TriageStarterTests(unittest.TestCase):
    def test_starter_prepares_without_calls_and_preserves_reference_separation(self):
        import jsonschema
        import yaml

        records = [json.loads(line) for line in (EXAMPLE / "tickets.jsonl").read_text().splitlines()]
        schema = json.loads((EXAMPLE / "output.schema.json").read_text())
        prompt = (EXAMPLE / "prompt.txt").read_text().rstrip("\n")
        self.assertEqual(len(records), 60)
        self.assertEqual(len({r["id"] for r in records}), 60)
        for record in records:
            jsonschema.validate(record["expected"], schema)
            self.assertEqual(record["prompt"], prompt)
            self.assertEqual(record["settings"]["response_format"]["json_schema"]["schema"], schema)

        with tempfile.TemporaryDirectory(prefix="triage-user-workspace-") as scratch:
            workspace = Path(scratch)
            project = workspace / "triage-study"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", AWS_EC2_METADATA_DISABLED="true")
            env.pop("PYTHONPATH", None)

            def cli(*args):
                result = subprocess.run(
                    [sys.executable, "-B", str(PLUGIN / "scripts/invoice_eval.py"), *map(str, args)],
                    cwd=workspace, env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)

            cli("start", "--project", project, "--goal", "Compare support-ticket triage decisions.")
            inspected = cli("inspect", "--project", project, "--input", EXAMPLE / "tickets.jsonl")
            self.assertEqual(inspected["coverage"]["accepted"], 60)
            self.assertEqual(inspected["coverage"]["dropped"], 0)
            self.assertEqual(len(inspected["prompt_groups"]), 1)

            decisions = json.loads((EXAMPLE / "decisions.template.json").read_text())
            answers = workspace / "decisions.json"
            answers.write_text(json.dumps(decisions))
            draft = cli("configure", "--project", project, "--decisions", answers)
            self.assertFalse(draft["ready_to_prepare"])

            connection = json.loads((PLUGIN / "connection.template.json").read_text())
            connection["cloud"]["runtime_arn"] = None
            for entry in connection["models"].values():
                entry["model"]["credential_ref"] = "aws_profile:offline-test-unused"
                entry["route"].update(
                    input_usd_per_million=1, output_usd_per_million=2,
                    cached_input_usd_per_million=1,
                    cache_write_usd_per_million=1,
                    pricing_source="Offline test fixture, not a price quotation",
                    pricing_verified_at="2026-11-13",
                    pricing_max_input_tokens=272000,
                )
            connection_path = workspace / "connection.json"
            connection_path.write_text(json.dumps(connection))
            decisions.update(owner="Offline fixture test", connection=str(connection_path),
                             budget_policy="capped", budget_usd=10)
            answers.write_text(json.dumps(decisions))
            configured = cli("configure", "--project", project, "--decisions", answers)
            self.assertTrue(configured["ready_to_prepare"], configured)
            prepared = cli("prepare", "--project", project)
            self.assertEqual(prepared["split_counts"], {"development": 20, "validation": 20, "holdout": 20})
            plan = cli("plan", "--project", project)
            self.assertEqual(plan["calls_made"], 0)
            self.assertEqual(plan["fixed_prompt_evaluation_trials_per_backend"], 80)
            self.assertFalse(plan["prompt_optimization"])
            document = yaml.safe_load((project / "prepared/input.yaml").read_text())
            self.assertEqual({d["label_status"] for d in document["datasets"]}, {"unreviewed"})
            self.assertEqual({d["provenance"] for d in document["datasets"]}, {"synthetic"})
            self.assertEqual(len(document["checks"]), 4)
            self.assertEqual(document["success_criteria"], [])
            for split in ("development", "validation", "holdout"):
                cases = [json.loads(line) for line in (project / "prepared" / f"{split}.jsonl").read_text().splitlines()]
                for case in cases:
                    self.assertEqual(case["metadata"]["reference_kind"], "supplied_expected")
                    self.assertNotIn("expected", case["replay"]["request"])
                    self.assertEqual([m["role"] for m in case["replay"]["request"]["input"]], ["system", "user"])
                    self.assertEqual(case["expected"]["answer"], next(r["expected"] for r in records if r["id"] == case["id"]))
            self.assertFalse((project / "local").exists())
            self.assertFalse((project / "submission").exists())


if __name__ == "__main__":
    unittest.main()
