import pytest
from scripts.recover_agentcore_trial import recover_trial


class Pool:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def _call(self, action, payload):
        self.calls.append((action, payload))
        return next(self.responses)


PAYLOAD = {'trial_id': 'same-trial'}


def test_existing_receipt_never_dispatches_paid_work():
    pool = Pool(dict(trial_id='same-trial', status='completed'))
    result = recover_trial(pool, PAYLOAD, dispatch_if_unclaimed=True)
    assert [a for a, _ in pool.calls] == ['trial_result']
    assert result['transport_recovery']['original_invocation_timing'] == 'unavailable'


@pytest.mark.parametrize('status', ['in_progress', 'not_found'])
def test_default_is_read_only(status):
    pool = Pool(dict(trial_id='same-trial', status=status))
    assert recover_trial(pool, PAYLOAD) is None
    assert [a for a, _ in pool.calls] == ['trial_result']


def test_existing_claim_is_never_dispatched_again():
    pool = Pool(dict(trial_id='same-trial', status='in_progress'))
    assert recover_trial(pool, PAYLOAD, dispatch_if_unclaimed=True) is None
    assert [a for a, _ in pool.calls] == ['trial_result']


def test_absent_claim_uses_identical_trial_and_preserves_missing_original_timing():
    pool = Pool(dict(trial_id='same-trial', status='not_found'), dict(trial_id='same-trial', status='completed'))
    result = recover_trial(pool, PAYLOAD, dispatch_if_unclaimed=True)
    assert pool.calls == [('trial_result', PAYLOAD), ('trial', PAYLOAD)]
    assert result['transport_recovery']['method'] == 'dispatch_after_absent_claim'


def test_concurrent_claim_winner_is_not_replayed():
    pool = Pool(dict(trial_id='same-trial', status='not_found'), dict(trial_id='same-trial', status='in_progress'))
    assert recover_trial(pool, PAYLOAD, dispatch_if_unclaimed=True) is None
    assert len(pool.calls) == 2


def test_last_dispatch_time_is_not_mislabeled_as_complete_invocation_latency():
    pool = Pool(dict(trial_id='same-trial', status='not_found'),
                dict(trial_id='same-trial', status='completed', dispatch_timing={'action':'trial','client_ms':20}))
    result = recover_trial(pool, PAYLOAD, dispatch_if_unclaimed=True)
    assert result['dispatch_timing']['client_ms'] is None
    assert result['transport_recovery']['recovery_dispatch_timing']['client_ms'] == 20


@pytest.mark.parametrize('first', [True, False])
def test_mismatched_receipt_identity_is_rejected(first):
    responses = [dict(trial_id='wrong', status='completed')]
    if not first:
        responses.insert(0, dict(trial_id='same-trial', status='not_found'))
    pool = Pool(*responses)
    with pytest.raises(ValueError, match='identity mismatch'):
        recover_trial(pool, PAYLOAD, dispatch_if_unclaimed=True)


@pytest.mark.parametrize('status', ['infrastructure_error', 'worker_error'])
def test_recorded_failure_is_preserved(status):
    pool = Pool(dict(trial_id='same-trial', status=status, error_type='KnownFailure'))
    result = recover_trial(pool, PAYLOAD)
    assert result['status'] == status
    assert result['error_type'] == 'KnownFailure'


def test_unknown_status_is_not_recorded_as_a_completed_attempt():
    pool = Pool(dict(trial_id='same-trial', status='unexpected'))
    with pytest.raises(ValueError, match='Unexpected receipt status'):
        recover_trial(pool, PAYLOAD)
