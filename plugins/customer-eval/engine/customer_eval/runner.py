"""Durable run records and bounded execution around Justin's hill-climb loop."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from hillclimb import run_hill_climb
from optimizer import make_mutate_fn
from .contract import cases, digest, trial_payload
from .scoring import grade, summarize, turn_metrics
from .transport import complete, reservation
from .worker import isolated
from .costs import run_costs
from .feedback import development_feedback, mutation_evidence


class BudgetExceeded(RuntimeError): pass
class InterruptedAttempt(RuntimeError): pass


class Store:
    def __init__(self, directory, identity, budget):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        # Only this process can wait for its own in-flight operation. A pending
        # database row from an earlier process still requires receipt recovery.
        self.inflight = {}
        self.db = sqlite3.connect(self.directory / 'run.sqlite', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, state TEXT NOT NULL, reserved REAL NOT NULL, value TEXT)')
        prior = self.db.execute('SELECT value FROM meta WHERE key="identity"').fetchone()
        if prior and prior[0] != identity:
            raise ValueError('Run directory belongs to different inputs, assets, backend or runner version. Use a new directory.')
        self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('identity', identity))
        self.db.commit()
        self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('execution_id', str(uuid.uuid4())))
        self.db.commit()
        self.execution_id = self.db.execute('SELECT value FROM meta WHERE key="execution_id"').fetchone()[0]
        self.budget = budget
        self.checkpoint = lambda: None
        self.checkpoint_event = 'progress'
        self.before_new_attempt = lambda: None

    def perform(self, ident, amount, operation, recover=None, chargeable=True):
        pending = False
        event = None
        owner = False
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT state,value FROM attempts WHERE id=?', (ident,)).fetchone()
            if row:
                self.db.rollback()
                if row[0] == 'complete': return json.loads(row[1])
                pending = True
                event = self.inflight.get(ident)
            else:
                if chargeable:
                    try:self.before_new_attempt()
                    except Exception:
                        self.db.rollback()
                        raise
                spent = self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM attempts').fetchone()[0]
                if spent + amount > self.budget + 1e-12:
                    self.db.rollback()
                    raise BudgetExceeded('Conservative model spend reservation exceeds execution.budget_usd')
                self.db.execute('INSERT INTO attempts VALUES (?,?,?,NULL)', (ident, 'started', amount))
                self.db.commit()
                self.checkpoint_event = 'reservation' if chargeable else 'progress'
                self.checkpoint()
                event = self.inflight[ident] = threading.Event()
                owner = True
        # A crash after reservation does not silently rerun a potentially charged call.
        if pending and event is not None:
            event.wait()
            with self.lock:
                row = self.db.execute('SELECT state,value FROM attempts WHERE id=?', (ident,)).fetchone()
                if row and row[0] == 'complete':
                    return json.loads(row[1])
            raise InterruptedAttempt('The shared in-flight attempt failed without a completed receipt; it will not be replayed.')
        try:
            if pending:
                value = recover() if recover else None
                if value is None:
                    raise InterruptedAttempt('An earlier attempt may have been charged and has no completed receipt. It will not be retried automatically.')
            else:
                value = operation()
            with self.lock:
                self.db.execute('UPDATE attempts SET state="complete",value=? WHERE id=?', (json.dumps(value), ident))
                self.db.commit()
                self.checkpoint_event = 'progress'
                self.checkpoint()
            return value
        finally:
            if owner:
                with self.lock:
                    self.inflight.pop(ident, None)
                    event.set()

    def reserved(self):
        return self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM attempts').fetchone()[0]


class Runner:
    def __init__(self, config, directory, *, backend='simulation', runtime_arn=None, profile=None, region=None):
        self.config, self.backend = config, backend
        self.runtime_arn, self.profile, self.region = runtime_arn, profile, region
        self.limits = config['document']['runner']['limits']
        self._cloud_pool = None
        self._cloud_lock = threading.Lock()
        from . import __version__
        identity = digest([config['fingerprint'], backend, runtime_arn, region, __version__])
        budget = config['document']['execution']['budget_usd']
        if config['document']['runner'].get('budget_policy') == 'unlimited': budget = float('inf')
        self.store = Store(directory, identity, budget)
        manifest = self.store.directory / 'run-input.json'
        if not manifest.exists():
            manifest.write_text(json.dumps(config, indent=2))

    def call(self, model_id, text, purpose='judge'):
        if self.backend == 'simulation':
            raise ValueError('Simulation does not call LLM judges; use deterministic grading in the offline examples')
        route = self.config['routes'][model_id]
        ident = digest(['completion', purpose, model_id, text])
        limits = dict(self.config['document']['runner'].get('optimizer_limits', self.limits)
                      if purpose == 'optimizer' else self.limits, max_requests=1)
        def invoke():
            receipt = {}
            output, usage = complete(route, text, limits, receipt=receipt)
            return dict(receipt, text=output, usage=usage, model_id=model_id, purpose=purpose,
                        request_text=text if purpose == 'optimizer' else None)
        record = self.store.perform(ident, reservation(route, limits), invoke)
        return record['text']

    def execute(self, payload):
        if self.backend == 'simulation':
            from .simulation import execute
            return execute(payload)
        if self.backend == 'local':
            return isolated(payload, self.config['document']['execution']['timeout_seconds'])
        if self.backend == 'agentcore':
            return self.cloud_pool().invoke(payload)
        raise ValueError('Unknown backend')

    def cloud_pool(self):
        with self._cloud_lock:
            if self._cloud_pool is None:
                from .cloud import AgentCorePool
                self._cloud_pool = AgentCorePool(self.runtime_arn,self.profile,self.region,
                    self.config['document']['execution']['timeout_seconds'],
                    self.config['document']['execution']['concurrency'],run_id=self.store.execution_id)
        return self._cloud_pool

    def close(self):
        if self._cloud_pool:
            self._cloud_pool.close()
            (self.store.directory/'session-cleanup.json').write_text(json.dumps(self._cloud_pool.cleanup,indent=2))

    def evaluate(self, variant, prompt, split):
        return self.evaluate_group([(variant, prompt)], split)[0]

    def evaluate_group(self, variants, split, phase='standard'):
        """Interleave configurations per case/repetition with balanced order.

        A new phase forces fresh attempts for matched comparisons; resume still
        reuses the exact same phase's completed trials without extra charges.
        """
        config = self.config
        jobs = []
        for case_index, case in enumerate(cases(config, split)):
            for repeat in range(config['document']['execution']['repetitions']):
                order = list(range(len(variants)))
                offset = (case_index + repeat) % len(order)
                for index in order[offset:] + order[:offset]:
                    jobs.append((case, repeat, index))

        def run_one(job):
            case, repeat, index = job
            variant, prompt = variants[index]
            ident = digest([config['fingerprint'], variant, prompt, case['id'], repeat, split, phase])
            payload = trial_payload(config, variant, prompt, case, ident)
            def invoke():
                try:
                    result = self.execute(payload)
                    if result.get('trial_id') != ident:
                        raise ValueError('Worker response does not match exact trial ID')
                    if result.get('status') in ('in_progress', 'not_found'):
                        raise InterruptedAttempt('The worker has not committed its result yet')
                    return result
                except Exception as exc:
                    if self.backend == 'agentcore':
                        # A lost response is not a failed model trial. Preserve the
                        # reservation and recover only a durable completed receipt.
                        try: recovered = self.cloud_pool().recover(payload)
                        except Exception: recovered = None
                        if recovered is not None: return recovered
                        raise InterruptedAttempt('Cloud outcome is uncertain. Resume this same run to read its receipt; paid work is not replayed.') from exc
                    return {'trial_id': ident, 'status': 'infrastructure_error', 'outputs': [], 'error_type': type(exc).__name__}
            amount = 0 if self.backend == 'simulation' else reservation(payload['route'], self.limits)
            recovery = (lambda: self.cloud_pool().recover(payload)) if self.backend=='agentcore' else None
            result = self.store.perform(ident, amount, invoke, recover=recovery)
            check_id = digest(['grade', ident])
            checks = self.store.perform(check_id, 0, lambda: grade(config, case, result, self.call),chargeable=False)
            return index, {'id': ident, 'case_id': case['id'], 'repeat': repeat, 'split': split,
                           'phase': phase, 'model_id': variant['model_id'], 'result': result, 'checks': checks}

        with ThreadPoolExecutor(max_workers=config['document']['execution']['concurrency']) as pool:
            futures = [pool.submit(run_one, job) for job in jobs]
            try: rows = [future.result() for future in futures]
            except BaseException:
                for future in futures: future.cancel()
                raise
        return [[row for i, row in rows if i == index] for index in range(len(variants))]

    def run(self, *, optimize=True):
        try: return self._run(optimize=optimize)
        finally: self.close()

    def _run(self, *, optimize=True):
        mode = 'optimize' if optimize else 'baseline'
        with self.store.lock:
            old = self.store.db.execute('SELECT value FROM meta WHERE key="workflow_mode"').fetchone()
            if old and old[0] != mode:
                raise ValueError('This run already has a different workflow mode. Use a new output directory; do not reopen holdout to tune a finished baseline run.')
            self.store.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('workflow_mode', mode))
            self.store.db.commit()
        cfg = self.config['document']['runner']
        prompt = self.config['prompt']
        source_validation, target_validation = self.evaluate_group(
            [(self.config['source'], prompt), (self.config['target'], prompt)], 'validation')
        comparisons = []
        development_evidence = {}

        def score(candidate):
            development = self.evaluate(self.config['target'], candidate, 'development')
            development_evidence[candidate] = development_feedback(self.config, candidate, development)
            feedback = [f"{r['case_id']} {key}: {check['reason']}" for r in development
                        for key, check in r['checks'].items() if r['result']['status'] == 'completed'
                        and not check['pass'] and not check.get('error')
                        and check.get('failure_category') not in ('trace', 'grading')]
            validation = self.evaluate(self.config['target'], candidate, 'validation')
            # Validation chooses candidates. Only development feedback reaches mutation.
            return summarize(self.config, candidate, validation, feedback)

        def compare(incumbent, candidates, generation):
            prompts = [incumbent.prompt] + [c.prompt for c in candidates]
            groups = self.evaluate_group([(self.config['target'], p) for p in prompts],
                                         'validation', phase=f'joint-comparison-{generation}')
            scores = [summarize(self.config, p, rows, previous.judge_reasoning)
                      for p, rows, previous in zip(prompts, groups, [incumbent] + candidates)]
            comparisons.append({'generation': generation, 'scores': [asdict(s) for s in scores],
                                'turn_quality': [turn_metrics(self.config, rows) for rows in groups],
                                'trials': groups, 'design': 'Fresh matched tasks/repetitions, interleaved configuration order; p95 is descriptive, not a significance test.'})
            return scores[0], scores[1:]

        result = None
        if optimize and self.config['document']['optimization']['enabled']:
            if self.backend == 'simulation':
                from .simulation import mutate
            else:
                context_data = {'objective': self.config['document']['experiment']['objective'],
                                      'checks': self.config['document']['checks'],
                                      'selection_policy': cfg.get('selection'),
                                      'efficiency_guidance': 'When joint optimization is selected, preserve all required tool calls and policy checks. Reduce unnecessary output and redundant instructions. The immutable policy is already supplied; do not repeat it. Never skip required work to appear faster.'}
                # Only general customer requirements belong in mutation context.
                # The rest of intake_context includes final split identities and
                # reference locations; it must never be forwarded wholesale.
                rules = (self.config.get('intake_context') or {}).get('quality', {}).get('rules')
                if rules:
                    context_data['customer_requirements'] = rules
                context = json.dumps(context_data)
                mutate = make_mutate_fn(lambda text: self.call(cfg['optimizer_model_id'], text, 'optimizer'),
                                        context=context, protected=self.config['policy'],
                                        feedback_provider=lambda p: mutation_evidence(p, development_evidence))
            result = run_hill_climb(seed_prompt=prompt, mutate_fn=mutate, fanout_score_fn=score,
                                   candidates_per_generation=cfg['candidates_per_generation'],
                                   max_generations=self.config['document']['optimization']['max_iterations'],
                                   min_accuracy_gain_to_continue=cfg['min_gain'],
                                   selection_policy=cfg.get('selection'), compare_fn=compare,
                                   patience=cfg.get('optimization_patience', 1),
                                   # judge_reasoning here already contains only development failures.
                                   # Validation criterion ranks must not reorder or truncate that evidence.
                                   mutation_reasons_fn=lambda candidate: candidate.judge_reasoning[:12])
        selected = result.best.prompt if result else prompt
        # Freeze the selected prompt before any final holdout case is executed.
        frozen = {'prompt': selected, 'sha256': digest(selected), 'selection': 'validation only',
                  'policy': cfg.get('selection', {'mode': 'accuracy'}),
                  'reason': result.stopped_reason if result else 'Baseline only'}
        challenger = None
        if cfg.get('report_challenger') and result and selected == prompt and result.generations:
            generation = result.generations[-1]
            accepted = [c for c, decision in zip(generation.candidates, generation.decisions)
                        if decision['accepted'] and c.prompt != prompt]
            if accepted:
                metric = cfg.get('selection', {}).get('cost_metric', 'cost_usd_per_session')
                challenger = min(accepted, key=lambda c: (getattr(c, metric), c.latency_p95_s))
                frozen['challenger'] = {'prompt': challenger.prompt, 'sha256': digest(challenger.prompt),
                    'selected': False, 'selection': 'Final validation generation only; no holdout outcomes used',
                    'reason': 'Lowest-cost quality-eligible candidate retained for tradeoff reporting; not promoted over the incumbent.'}
        (self.store.directory / 'selected-prompt.json').write_text(json.dumps(frozen, indent=2))
        final = {}
        configurations = [('source_original', self.config['source'], prompt),
                          ('target_original', self.config['target'], prompt)]
        if selected != prompt:
            configurations.append(('target_selected', self.config['target'], selected))
        elif challenger is not None:
            configurations.append(('target_challenger', self.config['target'], challenger.prompt))
        groups = self.evaluate_group([(v, p) for _, v, p in configurations], 'holdout')
        for (name, variant, candidate), rows in zip(configurations, groups):
            final[name] = {'score': asdict(summarize(self.config, candidate, rows)), 'trials': rows,
                           'turn_quality': turn_metrics(self.config, rows)}
        if selected == prompt:
            final['target_selected'] = dict(final['target_original'], reused_from='target_original')
        records = [json.loads(row[0]) for row in self.store.db.execute('SELECT value FROM attempts WHERE state="complete"')]
        unresolved = [{'id': ident, 'state': state} for ident, state in
                      self.store.db.execute('SELECT id,state FROM attempts WHERE state!="complete" ORDER BY id')]
        grading_errors = sum(any(isinstance(check, dict) and 'pass' in check and check.get('error')
                                 for check in record.values()) for record in records)
        report = {'backend': self.backend, 'simulated': self.backend == 'simulation',
                  'evaluation_status': 'needs_review' if unresolved or grading_errors else 'complete',
                  'unresolved_attempts': unresolved, 'grading_error_records': grading_errors,
                  'fingerprint': self.config['fingerprint'], 'selected_prompt': frozen,
                  'source_validation': asdict(summarize(self.config, prompt, source_validation)),
                  'target_validation': asdict(summarize(self.config, prompt, target_validation)),
                  'source_validation_turn_quality': turn_metrics(self.config, source_validation),
                  'target_validation_turn_quality': turn_metrics(self.config, target_validation),
                  'optimization': asdict(result) if result else None, 'holdout': final,
                  'joint_comparisons': comparisons,
                  'development_feedback': list(development_evidence.values()),
                  'optimizer_requests': [r for r in records if r.get('purpose') == 'optimizer'],
                  'model_costs': run_costs(self.config, records, self.backend == 'simulation',
                                          unresolved_attempts=len(unresolved)),
                  'reserved_model_spend_usd': self.store.reserved() if all(r['input_usd_per_million'] is not None and r['output_usd_per_million'] is not None for r in self.config['routes'].values()) else None,
                  'actual_total_cost_usd': None,
                  'cost_note': 'Model costs use observed Responses usage and configured route list prices, including cache reads/writes. They are estimates, not invoices. Missing usage remains unknown. Infrastructure and human review are excluded. Reservations are separate conservative caps.',
                  'latency_scope': 'Primary latency: ' + cfg.get('measurement', {}).get('primary_latency', 'worker') + '. Conversation is first user turn start to last answer, excluding setup/teardown. Worker includes setup/teardown. Provider request time includes network and provider processing. No metric here is pure model compute; outer AgentCore provisioning, coordinator queueing and grading are excluded.',
                  'claim': 'Simulation validates plumbing only; it measures no model quality.' if self.backend == 'simulation'
                           else 'Results apply to this workload, routes and settings; review judge calibration and sample size before adoption.'}
        output = self.store.directory / 'report.json'
        output.write_text(json.dumps(report, indent=2))
        cost_metric = cfg.get('selection', {}).get('cost_metric', 'cost_usd_per_session')
        cost_label = 'Model USD/success' if cost_metric == 'cost_usd_per_success' else 'Model USD/session'
        review_note = (f"Evaluation needs review: {grading_errors} grading records contain errors; "
                       f"{len(unresolved)} attempts have no completed receipt. Their usage remains unknown."
                       if unresolved or grading_errors else 'Evaluation complete.')
        lines = ['# Customer evaluation report', '', report['claim'], '', review_note, '',
                 f'| Holdout comparison | Required checks pass | p50 seconds | p95 seconds | {cost_label} | Eligible | Trials |', '|---|---:|---:|---:|---:|---|---:|']
        for name, value in final.items():
            s = value['score']
            cost = 'unknown' if s[cost_metric] is None else f"{s[cost_metric]:.6f}"
            p50 = 'unknown' if s['latency_p50_s'] is None else f"{s['latency_p50_s']:.3f}"
            p95 = 'unknown' if s['latency_p95_s'] is None else f"{s['latency_p95_s']:.3f}"
            lines.append(f"| {name} | {s['accuracy']:.1%} | {p50} | {p95} | {cost} | {s['eligible']} | {s['n_sessions']} |")
        lines += ['', 'Selection: ' + frozen['reason'], '', report['latency_scope'], '',
                  'Estimated unique-run model spend: ' + str(report['model_costs']['estimated_model_cost_usd']) + ' USD.']
        if any(v.get('turn_quality') for v in final.values()):
            lines += ['', '| Holdout configuration | Passing/planned turns | Pass rate |', '|---|---:|---:|']
            for name, value in final.items():
                q = value['turn_quality']
                lines.append(f"| {name} | {q['passing_turns']}/{q['planned_turns']} | {q['turn_pass_rate']:.1%} |")
            lines += ['', 'Every planned turn is in the denominator, including execution and trace failures. See turn_quality.by_case and failed_check_counts_by_category in report.json.']
        lines += ['', report['cost_note'], '', 'Full trial results and errors: `report.json`. Prompt frozen before holdout: `selected-prompt.json`.']
        (self.store.directory / 'report.md').write_text('\n'.join(lines) + '\n')
        from .decision_report import write as write_decision
        write_decision(self.config, report, self.store.directory)
        return report
