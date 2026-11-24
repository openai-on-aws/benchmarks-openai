"""Public preflight is a shell guard as well as a machine-readable report."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


PLUGIN = Path(__file__).resolve().parents[1]


class PreflightExitTests(unittest.TestCase):
    def invoke(self, report):
        spec = importlib.util.spec_from_file_location('preflight_cli_exit_test', PLUGIN / 'scripts/workflow_eval.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        package = ModuleType('customer_eval')
        package.project = ModuleType('customer_eval.project')
        checks = ModuleType('customer_eval.preflight')
        checks.inspect = Mock(return_value=report)
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cli, 'require_study_python'), \
                patch.object(sys, 'path', list(sys.path)), \
                patch.dict(sys.modules, {'customer_eval': package, 'customer_eval.preflight': checks}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(['preflight', '--project', directory, '--backend', 'agentcore'])
        self.assertEqual(checks.inspect.call_count, 1)
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(json.loads(stdout.getvalue()), report)
        return code

    def test_ready_with_nonblocking_inference_unknown_exits_zero(self):
        report = {'ready_for_qualification': True, 'read_only': True, 'model_calls': 0,
                  'blockers': [], 'checks': [{'check': 'inference_access', 'status': 'unknown', 'blocking': False}]}
        self.assertEqual(self.invoke(report), 0)

    def test_blocking_deployment_receipt_preserves_report_and_exits_one(self):
        report = {'ready_for_qualification': False, 'read_only': True, 'model_calls': 0,
                  'blockers': ['deployment_receipt'],
                  'checks': [{'check': 'deployment_receipt', 'status': 'fail', 'blocking': True}]}
        self.assertEqual(self.invoke(report), 1)

    def test_unprepared_draft_does_not_pass_even_without_blockers(self):
        report = {'ready_for_qualification': False, 'read_only': True, 'model_calls': 0,
                  'blockers': [], 'checks': [{'check': 'frozen_inputs', 'status': 'unknown', 'blocking': False}]}
        self.assertEqual(self.invoke(report), 1)

    def test_missing_or_nonboolean_readiness_fails_closed(self):
        for value in (None, 'true', 1):
            with self.subTest(value=value):
                report = {} if value is None else {'ready_for_qualification': value}
                self.assertEqual(self.invoke(report), 1)


if __name__ == '__main__':
    unittest.main()
