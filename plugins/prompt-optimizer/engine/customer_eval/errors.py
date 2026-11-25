"""Control-flow stops must never become model-quality or grading outcomes."""


class RecordedProviderError(RuntimeError):
    """A returned HTTP failure has a durable receipt, with unknown model usage.

    Store completion means the operation's outcome was saved, not that the model
    succeeded. Re-reading this error must never dispatch the paid request again.
    """
    def __init__(self, http_status, attempt_id):
        self.http_status = http_status
        self.attempt_id = attempt_id
        super().__init__(f'Provider returned HTTP {http_status}; failed completion receipt {attempt_id} is saved. No automatic retry.')


class EvaluationControlError(RuntimeError):
    """Pause or stop dispatch while preserving durable, potentially paid attempts."""
