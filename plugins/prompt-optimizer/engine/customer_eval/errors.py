"""Control-flow stops must never become model-quality or grading outcomes."""


class EvaluationControlError(RuntimeError):
    """Pause or stop dispatch while preserving durable, potentially paid attempts."""
