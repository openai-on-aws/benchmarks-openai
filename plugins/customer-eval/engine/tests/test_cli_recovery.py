from unittest.mock import Mock
import pytest
from customer_eval.runner import InterruptedAttempt
from scripts.run_optimization import run_with_recovery
import scripts.run_optimization as cli


def test_success_never_recovers():
    runner = Mock()
    runner.run.return_value = 'report'
    factory, recover = Mock(return_value=runner), Mock()
    assert run_with_recovery(factory, recover, optimize=True) == 'report'
    factory.assert_called_once_with()
    recover.assert_not_called()
    runner.run.assert_called_once_with(optimize=True)


def test_resume_only_follows_successful_durable_recovery():
    failed, resumed = Mock(), Mock()
    error = InterruptedAttempt('uncertain')
    failed.run.side_effect = error
    resumed.run.return_value = 'report'
    recover = Mock()
    assert run_with_recovery(Mock(side_effect=[failed,resumed]), recover, optimize=False) == 'report'
    recover.assert_called_once_with(error)
    resumed.run.assert_called_once_with(optimize=False)


def test_failed_recovery_never_restarts_controller():
    runner = Mock()
    runner.run.side_effect = InterruptedAttempt('uncertain')
    factory = Mock(return_value=runner)
    with pytest.raises(ValueError, match='no receipt'):
        run_with_recovery(factory, Mock(side_effect=ValueError('no receipt')), optimize=True)
    factory.assert_called_once_with()


def test_recovery_is_bounded():
    runner = Mock()
    runner.run.side_effect = InterruptedAttempt('uncertain')
    factory, recover = Mock(return_value=runner), Mock()
    with pytest.raises(InterruptedAttempt):
        run_with_recovery(factory, recover, optimize=True)
    assert factory.call_count == 3
    assert recover.call_count == 2


def test_non_transport_errors_are_not_retried():
    runner = Mock()
    runner.run.side_effect = ValueError('invalid input')
    recover = Mock()
    with pytest.raises(ValueError, match='invalid input'):
        run_with_recovery(Mock(return_value=runner), recover, optimize=True)
    recover.assert_not_called()


def test_local_runs_do_not_recover():
    runner = Mock()
    runner.run.side_effect = InterruptedAttempt('uncertain')
    recover = Mock()
    with pytest.raises(InterruptedAttempt):
        run_with_recovery(Mock(return_value=runner), recover, optimize=True, max_recoveries=0)
    recover.assert_not_called()


@pytest.mark.parametrize('extra', [['--region','us-east-1'],['--runtime-arn','example-arn']])
def test_missing_runtime_configuration_fails_before_loading_or_reserving(monkeypatch, extra):
    loader = Mock()
    monkeypatch.setattr(cli, 'load', loader)
    with pytest.raises(SystemExit) as error:
        cli.main(['all','--backend','agentcore','--live','--config','unused'] + extra)
    assert error.value.code == 2
    loader.assert_not_called()
