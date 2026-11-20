import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/eval-intake/scripts'))
from input_framework import canonical, load_document, main, render_brief, validate

spec = importlib.util.spec_from_file_location('codex_agent_demo', ROOT/'scripts/codex_agent_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)/'support'
        shutil.copytree(ROOT/'examples/support-agent', self.base)
        self.doc = load_document(self.base/'input.json')

    def tearDown(self):
        self.tmp.cleanup()

    def check(self):
        return validate(self.doc, self.base, check_assets=True)

    def assertInvalid(self, fragment):
        result = self.check()
        self.assertFalse(result['valid'], result)
        self.assertTrue(any(fragment in e for e in result['errors']), result['errors'])

    def test_json_yaml_equivalent(self):
        self.assertEqual(canonical(self.doc), canonical(load_document(self.base/'input.yaml')))

    def test_valid_draft_is_not_ready(self):
        result = self.check()
        self.assertTrue(result['valid'], result)
        self.assertFalse(result['ready_for_builder'])
        self.assertFalse(result['execution_authorized'])
        self.assertTrue(result['assets'])

    def test_complete_input_still_needs_runtime_checks(self):
        self.doc['open_questions'] = []
        self.doc['execution']['budget_usd'] = 10
        for c in self.doc['success_criteria']:
            c.update(status='confirmed', confirmed_by='Example owner', threshold=0.9)
        result = self.check()
        self.assertTrue(result['ready_for_builder'], result)
        self.assertTrue(result['runtime_checks'])
        self.assertFalse(result['execution_authorized'])

    def test_explicit_unlimited_budget_is_not_missing_but_other_limits_are(self):
        base = ROOT.parent/'examples/customer-support'
        doc = load_document(base/'ade-live.yaml')
        self.assertTrue(validate(doc, base, check_assets=True)['ready_for_builder'])
        self.assertFalse(validate(doc, base)['execution_authorized'])
        doc['runner']['budget_policy'] = 'capped'
        self.assertIn('execution.budget_usd', validate(doc, base)['open_items'])
        del doc['runner']['budget_policy']
        self.assertIn('execution.budget_usd', validate(doc, base)['open_items'])
        doc['runner']['budget_policy'] = 'unlimited'
        doc['execution']['timeout_seconds'] = None
        self.assertIn('execution.timeout_seconds', validate(doc, base)['open_items'])

    def test_model_upgrade_runner_imports_normalized_intake_handoff(self):
        from customer_eval.contract import load
        source = ROOT.parent/'examples/customer-support/ade-live.yaml'
        output = Path(self.tmp.name)/'runner-handoff'
        self.assertEqual(main(['bundle', str(source), '--check-assets', '--require-complete',
                               '--output', str(output)]), 0)
        with patch.dict(os.environ, {'EVAL_BEDROCK_BASE_URL': 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1'}):
            original = load(source, live=True)
            handoff = load(output/'evaluation-input.json', live=True)
        self.assertEqual(original, handoff)
        self.assertTrue(handoff['datasets']['holdout'])

    def test_runner_grader_and_policy_are_frozen_for_handoff(self):
        from customer_eval.contract import load
        base = Path(self.tmp.name)/'retail'
        shutil.copytree(ROOT.parent/'examples/retail-returns', base)
        source = base/'input.yaml'
        doc = load_document(source)
        output = Path(self.tmp.name)/'retail-handoff'
        self.assertEqual(main(['bundle', str(source), '--check-assets', '--output', str(output)]), 0)
        manifest = load_document(output/'manifest.json')
        paths = [asset['path'] for asset in manifest['validation']['assets']]
        self.assertEqual(paths.count('policy.txt'), 1)
        self.assertIn('grading.json', paths)
        config = output/'evaluation-input.json'
        # Changing a still-parseable grader cannot silently change a frozen handoff.
        grader = base/'grading.json'
        grader.write_text(grader.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'Handoff asset changed.*grading.json'):
            load(config)
        self.assertNotEqual(main(['validate', str(config), '--check-assets', '--asset-base', str(base)]), 0)
        grader.unlink()
        result = validate(doc, base, check_assets=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('runner.grading_path' in error for error in result['errors']))
        doc['runner']['protected_instructions_path'] = '../outside.txt'
        self.assertTrue(any('runner.protected_instructions_path' in error for error in
                            validate(doc, base, check_assets=True)['errors']))

    def test_partial_assets_preserve_known_facts(self):
        self.doc['prompts'][0].update(path=None, version=None)
        self.doc['datasets'][0].update(path=None, version=None, split=None)
        result = self.check()
        self.assertTrue(result['valid'],result)
        self.assertFalse(result['ready_for_builder'])
        self.assertIn('prompts.p1.path',result['open_items'])
        text = render_brief(self.doc,result)
        self.assertIn('labels unreviewed',text)
        self.assertIn('threshold not agreed',text)
        self.assertNotIn('>= None',text)
        self.assertIn('Illustrative synthetic example',text)

    def test_eventual_runtime_does_not_change_current_runtime(self):
        self.doc['agents'][0]['planned_runtime'] = 'agentcore_runtime'
        result = self.check()
        self.assertTrue(result['valid'],result)
        self.assertEqual(self.doc['agents'][0]['runtime'],'local')
        self.assertIn('Planned deployment: agentcore_runtime',render_brief(self.doc,result))

    def test_partial_assets_cannot_enter_smoke_test(self):
        self.doc['prompts'][0]['path'] = None
        (self.base/'input.json').write_text(json.dumps(self.doc))
        with self.assertRaisesRegex(ValueError,'Supply dataset'):
            demo.prepare(self.base/'input.json','m1_p1','development','dev-unused')

    def test_unknown_field_rejected(self):
        self.doc['secret_api_key'] = 'not-a-real-secret'
        self.assertInvalid('unrecognized field')
        self.assertNotIn('not-a-real-secret', json.dumps(self.check()))

    def test_duplicate_ids(self):
        self.doc['models'].append(copy.deepcopy(self.doc['models'][0]))
        self.assertInvalid('duplicate id')

    def test_missing_model_reference(self):
        self.doc['variants'][0]['model_id'] = 'missing'
        self.assertInvalid('unknown models reference')

    def test_missing_asset(self):
        (self.base/'prompts/p1.txt').unlink()
        self.assertInvalid('asset missing')

    def test_path_traversal(self):
        self.doc['prompts'][0]['path'] = '../outside.txt'
        self.assertInvalid('inside the configuration directory')

    def test_model_id_and_env_are_exclusive(self):
        self.doc['models'][0]['model_id'] = 'example-model'
        self.assertInvalid('not both')

    def test_gateway_routing_must_be_explicit(self):
        self.doc['variants'][0]['route'] = 'gateway_model_tool'
        self.assertInvalid('requires gateway_id')

    def test_holdout_cannot_be_development(self):
        self.doc['optimization']['development_dataset_ids'] = ['holdout']
        self.assertInvalid('wrong split')

    def test_same_input_cannot_cross_splits(self):
        rows = [json.loads(x) for x in (self.base/'data/development.jsonl').read_text().splitlines()]
        row = copy.deepcopy(rows[0])
        row['id'] = 'distinct-id'
        (self.base/'data/holdout.jsonl').write_text(json.dumps(row)+'\n')
        self.assertInvalid('identical task input')

    def test_missing_prompt_variable(self):
        (self.base/'data/development.jsonl').write_text(json.dumps({'id':'no-variable','input':{}})+'\n')
        self.assertInvalid('missing prompt variables')

    def test_duplicate_yaml_key(self):
        file = self.base/'duplicate.yaml'
        for text in ('a: 1\na: 2\n', 'outer:\n  a: 1\n  a: 2\n',
                     'items:\n  - a: 1\n    a: 2\n'):
            with self.subTest(text=text):
                file.write_text(text)
                with self.assertRaisesRegex(ValueError, 'Duplicate field'):
                    load_document(file)

    def test_yaml_python_tags_are_rejected_without_execution(self):
        for suffix in ('yaml', 'yml'):
            file = self.base/f'untrusted.{suffix}'
            for payload in ('!!python/object/apply:os.system [PRIVATE_SENTINEL]',
                            '!!python/object/new:builtins.object {}',
                            '!!python/name:os.system',
                            'nested: !!python/object/apply:os.system [PRIVATE_SENTINEL]'):
                with self.subTest(suffix=suffix, payload=payload):
                    file.write_text(payload)
                    with patch('os.system') as system:
                        with self.assertRaisesRegex(ValueError, 'YAML syntax error') as error:
                            load_document(file)
                        system.assert_not_called()
                    self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))

    def test_yaml_aliases_and_single_document_limit(self):
        file = self.base/'aliases.yaml'
        file.write_text('original: &value {a: [1, true, null]}\ncopy: *value\n')
        expected = {'a': [1, True, None]}
        self.assertEqual(load_document(file), {'original': expected, 'copy': expected})
        file.write_text('a: 1\n---\na: 2\n')
        with self.assertRaisesRegex(ValueError, 'YAML syntax error'):
            load_document(file)

    def test_duplicate_json_key(self):
        file = self.base/'duplicate.json'
        file.write_text('{"a":1,"a":2}')
        with self.assertRaises(ValueError): load_document(file)

    def test_nonfinite_number(self):
        for suffix, text in [('json','{"a":NaN}'), ('yaml','a: .nan')]:
            file = self.base/f'nonfinite.{suffix}'
            file.write_text(text)
            with self.assertRaises(ValueError): load_document(file)

    def test_human_reviewed_requires_expected(self):
        self.doc['datasets'][0]['label_status'] = 'human_reviewed'
        (self.base/'data/development.jsonl').write_text(json.dumps({'id':'no-label','input':{'question':'hello'}})+'\n')
        self.assertInvalid('no expected label')

    def test_confirmed_threshold_needs_owner_and_value(self):
        self.doc['success_criteria'][0]['status'] = 'confirmed'
        self.assertInvalid('confirmed criterion needs')

    def test_rate_units(self):
        self.doc['success_criteria'][0]['threshold'] = 95
        self.assertInvalid('0..1')

    def test_negative_budget(self):
        self.doc['execution']['budget_usd'] = -1
        self.assertInvalid('/execution/budget_usd')

    def test_verified_model_needs_evidence(self):
        self.doc['models'][0]['compatibility'] = 'verified'
        self.assertInvalid('needs evidence')

    def test_credentials_not_allowed_in_parameters(self):
        self.doc['models'][0]['parameters']['api_key'] = 'dummy-secret'
        self.assertInvalid('credentials belong')

    def test_credential_reference_cannot_be_a_raw_token(self):
        self.doc['models'][0]['credential_ref'] = 'not-a-reference'
        self.assertInvalid('/models/0/credential_ref')

    def test_bundle_preserves_source_and_hashes(self):
        output = self.base/'handoff'
        with patch('builtins.print'):
            result = main(['bundle',str(self.base/'input.yaml'),'--check-assets','--output',str(output)])
        self.assertEqual(result,0)
        manifest = json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['asset_base'],str(self.base.resolve()))
        self.assertTrue(manifest['validation']['assets'])
        self.assertEqual(load_document(output/'evaluation-input.json'),self.doc)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT/'skills/eval-intake/scripts/input_framework.py'),
                               *map(str, args)], capture_output=True, text=True)

    def create_handoff(self):
        output = self.base/'handoff'
        result = self.run_cli('bundle', self.base/'input.yaml', '--check-assets', '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return output

    def test_nested_credentials_rejected_without_echoing_values(self):
        for parameters in ({'generation': {'api_key': 'PRIVATE_SENTINEL'}},
                           {'headers': [{'Authorization': 'PRIVATE_SENTINEL'}]}):
            with self.subTest(parameters=list(parameters)):
                self.doc['models'][0]['parameters'] = parameters
                self.assertInvalid('credentials belong')
                self.assertNotIn('PRIVATE_SENTINEL', json.dumps(self.check()))

    def test_human_reviewed_null_label_is_missing(self):
        self.doc['datasets'][0]['label_status'] = 'human_reviewed'
        file = self.base/'data/development.jsonl'
        rows = [json.loads(line) for line in file.read_text().splitlines()]
        rows[0]['expected'] = None
        file.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assertInvalid('no expected label')

    def test_false_zero_and_explicit_null_object_are_labels(self):
        self.doc['datasets'][0]['label_status'] = 'human_reviewed'
        file = self.base/'data/development.jsonl'
        rows = [json.loads(line) for line in file.read_text().splitlines()]
        for expected in (False, 0, {'answer': None}):
            with self.subTest(expected=expected):
                rows[0]['expected'] = expected
                file.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                result = self.check()
                self.assertTrue(result['valid'], result)

    def test_all_required_pass_rate_needs_required_check(self):
        for check in self.doc['checks']:
            check['required'] = False
        self.assertInvalid('needs at least one required check')

    def test_no_checks_can_be_saved_as_incomplete_draft(self):
        self.doc['checks'] = []
        result = self.check()
        self.assertTrue(result['valid'], result)
        self.assertFalse(result['ready_for_builder'])
        self.assertIn('Add at least one checks entry', result['open_items'])

    def test_negative_budget_error_explains_bound(self):
        self.doc['execution']['budget_usd'] = -1
        self.assertInvalid('/execution/budget_usd: must be > 0')

    def test_missing_config_has_recovery_guidance(self):
        result = self.run_cli('validate', self.base/'missing.yaml')
        self.assertEqual(result.returncode, 2)
        self.assertIn('not found', result.stderr)
        self.assertIn('Check the configuration path', result.stderr)
        self.assertFalse((self.base/'missing.yaml').exists())

    def test_malformed_input_has_location_without_echo(self):
        for suffix, value, syntax in [('yaml', 'secret: PRIVATE_SENTINEL\nmodels: [\n', 'YAML'),
                                      ('json', '{"secret":"PRIVATE_SENTINEL",}', 'JSON')]:
            with self.subTest(suffix=suffix):
                file = self.base/f'bad.{suffix}'
                file.write_text(value)
                result = self.run_cli('validate', file)
                self.assertEqual(result.returncode, 2)
                self.assertIn(f'{syntax} syntax error at line', result.stderr)
                self.assertNotIn('PRIVATE_SENTINEL', result.stderr)

    def test_duplicate_field_error_does_not_echo_value(self):
        file = self.base/'duplicate.yaml'
        file.write_text('key: PRIVATE_SENTINEL\nkey: another-value\n')
        result = self.run_cli('validate', file)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Duplicate field', result.stderr)
        self.assertNotIn('PRIVATE_SENTINEL', result.stderr)

    def test_existing_handoff_is_preserved(self):
        output = self.create_handoff()
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        result = self.run_cli('bundle', self.base/'input.yaml', '--check-assets', '--output', output)
        self.assertEqual(result.returncode, 2)
        self.assertIn('new revision directory', result.stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_reopen_handoff_uses_original_asset_directory(self):
        output = self.create_handoff()
        result = self.run_cli('validate', output/'evaluation-input.json', '--check-assets')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['assets'])

    def test_moved_assets_can_be_restored_without_rewriting_config(self):
        output = self.create_handoff()
        portable = Path(self.tmp.name)/'received'
        shutil.copytree(output, portable)
        shutil.rmtree(output)
        restored = Path(self.tmp.name)/'restored-assets'
        shutil.move(self.base, restored)
        original_bytes = (portable/'evaluation-input.json').read_bytes()
        result = self.run_cli('validate', portable/'evaluation-input.json', '--check-assets')
        self.assertEqual(result.returncode, 1)
        self.assertIn('original asset directory is unavailable', result.stdout)
        self.assertIn('--asset-base', result.stdout)
        result = self.run_cli('bundle', portable/'evaluation-input.json', '--check-assets',
                              '--asset-base', restored, '--output', Path(self.tmp.name)/'handoff-v2')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((portable/'evaluation-input.json').read_bytes(), original_bytes)
        manifest = load_document(Path(self.tmp.name)/'handoff-v2/manifest.json')
        self.assertEqual(manifest['asset_base'], str(restored.resolve()))

    def test_stale_manifest_requires_explicit_asset_base(self):
        output = self.create_handoff()
        original_manifest = (output/'manifest.json').read_bytes()
        config = output/'evaluation-input.json'
        self.doc['execution']['budget_usd'] = 25
        config.write_text(json.dumps(self.doc))
        result = self.run_cli('validate', config, '--check-assets')
        self.assertEqual(result.returncode, 2)
        self.assertIn('changed after this manifest', result.stderr)
        result = self.run_cli('validate', config, '--check-assets', '--asset-base', self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((output/'manifest.json').read_bytes(), original_manifest)

    def test_standalone_skill_has_actionable_dependency_setup(self):
        copied = Path(self.tmp.name)/'standalone-skill'
        shutil.copytree(ROOT/'skills/eval-intake', copied, ignore=shutil.ignore_patterns('__pycache__'))
        result = subprocess.run([sys.executable, '-S', str(copied/'scripts/input_framework.py'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('local virtual environment', result.stderr)
        self.assertIn(str(copied/'requirements.txt'), result.stderr)
        self.assertTrue((copied/'requirements.txt').is_file())

    def test_preview_does_not_invoke_codex(self):
        with patch('subprocess.Popen') as popen, patch('builtins.print'):
            code = demo.main([str(self.base/'input.yaml'),'--variant','m1_p1','--dataset','development','--case','dev-unused'])
        self.assertEqual(code,0)
        popen.assert_not_called()

    def test_expected_labels_are_not_sent(self):
        path = self.base/'data/development.jsonl'
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        rows[0]['expected'] = 'SECRET_EXPECTED_SENTINEL'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        plan = demo.prepare(self.base/'input.json','m1_p1','development','dev-unused')
        self.assertNotIn('SECRET_EXPECTED_SENTINEL',plan['task'])
        self.assertNotIn('expected',plan)

    def test_gateway_demo_not_silently_ignored(self):
        self.doc['agents'][0]['runtime'] = 'agentcore_runtime'
        (self.base/'input.json').write_text(json.dumps(self.doc))
        with self.assertRaisesRegex(ValueError,'supports only'):
            demo.prepare(self.base/'input.json','m1_p1','development','dev-unused')

    def test_invalid_final_output_not_completed(self):
        with patch.dict(os.environ, {'EVAL_MODEL_M1':'example-model'}):
            plan = demo.prepare(self.base/'input.json','m1_p1','development','dev-unused')
        class FakeProcess:
            returncode = 0
            def communicate(self, *args, **kwargs): return (None,None)
        with patch('shutil.which',return_value='/example/codex'), patch('subprocess.Popen',return_value=FakeProcess()):
            _, record = demo.execute(plan,self.base/'runs')
        self.assertEqual(record['status'],'invalid_output')
        self.assertIsNone(record['quality_passed'])
        self.assertIsNone(record['cost_usd'])


if __name__ == '__main__': unittest.main()
