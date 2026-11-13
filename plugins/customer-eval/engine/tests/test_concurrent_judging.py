"""Concurrent identical judge requests share one charged attempt; crashes do not replay it."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from customer_eval.runner import Store, InterruptedAttempt


def test_concurrent_duplicate_waits_for_one_receipt_and_one_charge(tmp_path):
    store=Store(tmp_path,'same',10)
    entered=threading.Event();release=threading.Event();calls=[]
    def operation():
        calls.append('called');entered.set()
        assert release.wait(5)
        return {'text':'same verdict','usage':{'input_tokens':1}}
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(store.perform,'judge',.5,operation)
        assert entered.wait(5)
        waiting=threading.Event();event=store.inflight['judge'];wait=event.wait
        def observed_wait(timeout=None):
            waiting.set();return wait(timeout)
        event.wait=observed_wait
        second=pool.submit(store.perform,'judge',.5,operation)
        assert waiting.wait(5)
        release.set()
        assert first.result()==second.result()
    assert calls==['called'] and store.reserved()==.5


def test_shared_failure_wakes_waiter_and_never_replays(tmp_path):
    store=Store(tmp_path,'same',10)
    entered=threading.Event();release=threading.Event();calls=[]
    def operation():
        calls.append('called');entered.set()
        assert release.wait(5)
        raise RuntimeError('Outcome unknown after request')
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(store.perform,'judge',.5,operation)
        assert entered.wait(5)
        waiting=threading.Event();event=store.inflight['judge'];wait=event.wait
        def observed_wait(timeout=None):
            waiting.set();return wait(timeout)
        event.wait=observed_wait
        second=pool.submit(store.perform,'judge',.5,operation)
        assert waiting.wait(5)
        release.set()
        with pytest.raises(RuntimeError):first.result()
        with pytest.raises(InterruptedAttempt):second.result()
    reopened=Store(tmp_path,'same',10)
    with pytest.raises(InterruptedAttempt):reopened.perform('judge',.5,lambda:pytest.fail('Never repeat an uncertain paid attempt'))
    assert calls==['called'] and reopened.reserved()==.5
