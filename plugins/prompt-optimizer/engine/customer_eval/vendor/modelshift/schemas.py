"""Core data contracts for ModelShift.

Mirrors the spec set:
  - NormalizedSample        (Doc 2 §5)
  - CandidateResult         (Doc 3 §3.3)
  - PairScore               (Doc 3 §4)
  - CandidateVerdict        (Doc 3 §4.3)
  - Remediation             (Doc 3 §6.4)
  - IngestionReport         (Doc 2 §6.3 / Doc 5 §4.2)

These are the internal contracts AND the API payload shapes (Doc 5 §3).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums / vocabularies
# --------------------------------------------------------------------------- #
class Shape(str, Enum):
    """Recognized LiteLLM row shapes (Doc 2 §3)."""

    SPENDLOGS = "spendlogs"
    WRAPPED = "wrapped"
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    UNKNOWN = "unknown"  # Shape E — never evaluable


class Container(str, Enum):
    """Container formats the parser auto-detects (Doc 2 §2.2)."""

    JSONL = "jsonl"
    JSON_ARRAY = "json_array"
    WRAPPED_DATA = "wrapped_data"


class GoldenKind(str, Enum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    REFUSAL = "refusal"
    JSON = "json"
    EMPTY = "empty"


class Modality(str, Enum):
    TEXT = "text"
    CONTAINS_IMAGE = "contains_image"
    CONTAINS_AUDIO = "contains_audio"


class DropReason(str, Enum):
    """Mutually-exclusive drop/block reasons (Doc 2 §6.2)."""

    REDACTED = "redacted"
    EMPTY_RESPONSE = "empty_response"
    NO_PROMPT = "no_prompt"
    TOOL_ONLY_NO_TEXT = "tool_only_no_text"
    TOOL_CALLS_UNSUPPORTED = "tool_calls_unsupported"
    UNSUPPORTED_MODALITY = "unsupported_modality"
    STREAM_FRAGMENT = "stream_fragment"
    NON_EVAL_CALL_TYPE = "non_eval_call_type"
    PARSE_ERROR = "parse_error"
    UNKNOWN_SHAPE = "unknown_shape"


class ReasoningEffort(str, Enum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CompatLabel(str, Enum):
    """Per-sample compatibility label (Doc 3 §4.2)."""

    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_DRIFT = "compatible_with_drift"
    PARTIAL = "partial"
    INCOMPATIBLE = "incompatible"


class VerdictBand(str, Enum):
    """Aggregate per-candidate verdict band (Doc 3 §4.3)."""

    SAFE_DROP_IN = "safe_drop_in"
    DROP_IN_WITH_TUNING = "drop_in_with_prompt_tuning"
    NEEDS_WORK = "needs_work"
    NOT_A_DROP_IN = "not_a_drop_in"


class CallPath(str, Enum):
    BEDROCK = "bedrock"
    LITELLM = "litellm"


class BedrockEndpoint(str, Enum):
    """Which Bedrock OpenAI-compatible endpoint to call (AWS model cards).

    RUNTIME: https://bedrock-runtime.{region}.amazonaws.com/openai/v1 with a
             us.openai.* cross-Region profile (Luna/Terra/Sol).
    MANTLE:  https://bedrock-mantle.{region}.api.aws/openai/v1 with the plain
             openai.* id — REQUIRED for GPT-5.4 (not on runtime).
    """

    RUNTIME = "runtime"
    MANTLE = "mantle"


# --------------------------------------------------------------------------- #
# NormalizedSample (Doc 2 §5)
# --------------------------------------------------------------------------- #
class SampleSource(BaseModel):
    shape: Shape
    container: Optional[Container] = None
    object_uri: str = "upload:unknown"
    row_index: int = 0


class Message(BaseModel):
    role: str
    content: str


class ToolCall(BaseModel):
    name: str
    arguments: Any = None


class SampleRequest(BaseModel):
    instructions: Optional[str] = None
    input_messages: List[Message] = Field(default_factory=list)
    tools: Optional[List[Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    modality: Modality = Modality.TEXT
    had_tool_calls: bool = False


class Golden(BaseModel):
    kind: GoldenKind = GoldenKind.EMPTY
    text: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    raw: Optional[Dict[str, Any]] = None


class Usage(BaseModel):
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    spend: Optional[float] = None


class NormalizedSample(BaseModel):
    id: str
    source: SampleSource
    legacy_model: Optional[str] = None
    request: SampleRequest
    golden: Golden
    usage: Usage = Field(default_factory=Usage)
    original_latency_ms: Optional[int] = None
    tags: Dict[str, Any] = Field(default_factory=dict)
    evaluable: bool = False
    eval_block_reason: Optional[DropReason] = None
    flags: List[str] = Field(default_factory=list)


class DroppedRow(BaseModel):
    """A row that failed evaluability — kept for accounting (Doc 2 §6.3)."""

    id: str
    source: SampleSource
    eval_block_reason: DropReason


# --------------------------------------------------------------------------- #
# CandidateResult (Doc 3 §3.3)
# --------------------------------------------------------------------------- #
class CandidateOutput(BaseModel):
    kind: GoldenKind = GoldenKind.TEXT
    text: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None


class CandidateSettings(BaseModel):
    reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    dropped_params: List[str] = Field(default_factory=list)


class CandidateResult(BaseModel):
    sample_id: str
    model: str
    settings: CandidateSettings = Field(default_factory=CandidateSettings)
    output: CandidateOutput = Field(default_factory=CandidateOutput)
    usage: Usage = Field(default_factory=Usage)
    cost_estimate: float = 0.0
    latency_ms: Optional[int] = None
    status: str = "ok"  # ok | error
    error: Optional[str] = None
    cache_key: Optional[str] = None


# --------------------------------------------------------------------------- #
# PairScore (Doc 3 §4)
# --------------------------------------------------------------------------- #
class DimensionScore(BaseModel):
    score: float = 0.0  # 0..1
    reason: str = ""


class PairScore(BaseModel):
    sample_id: str
    model: str
    label: CompatLabel
    d1_semantic: DimensionScore = Field(default_factory=DimensionScore)
    d2_format: DimensionScore = Field(default_factory=DimensionScore)
    d3_factual: DimensionScore = Field(default_factory=DimensionScore)
    d4_verbosity: DimensionScore = Field(default_factory=DimensionScore)
    d5_instruction: DimensionScore = Field(default_factory=DimensionScore)
    d6_tool_call: Optional[DimensionScore] = None
    judge_reason: Optional[str] = None
    judge_rationale: Optional[str] = None
    hard_break: bool = False
    overridden: bool = False
    override_label: Optional[CompatLabel] = None
    override_note: Optional[str] = None


# --------------------------------------------------------------------------- #
# CandidateVerdict (Doc 3 §4.3)
# --------------------------------------------------------------------------- #
class CandidateVerdict(BaseModel):
    model: str
    migration_confidence: float = 0.0  # 0..100
    verdict_band: VerdictBand = VerdictBand.NOT_A_DROP_IN
    # Which transport/endpoint this candidate actually used for the run:
    # "bedrock-runtime" | "bedrock-mantle" | "litellm" | "" (unknown/not run).
    endpoint: str = ""
    distribution: Dict[CompatLabel, int] = Field(default_factory=dict)
    dimension_averages: Dict[str, float] = Field(default_factory=dict)
    top_failure_reasons: List[Dict[str, Any]] = Field(default_factory=list)
    hard_break_rate: float = 0.0
    cost_total: float = 0.0
    latency_avg_ms: Optional[float] = None
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    scored: int = 0
    errored: int = 0


# --------------------------------------------------------------------------- #
# Remediation (Doc 3 §6.4)
# --------------------------------------------------------------------------- #
class RemediationChange(BaseModel):
    type: str  # prompt_edit | reasoning_effort | format_schema | swap_candidate
    # reasoning_effort: before/after are effort values (e.g. "medium" -> "low").
    # prompt_edit / format_schema: after is the instruction added to each request's
    # system prompt (before is None - every request keeps its own system prompt).
    before: Optional[str] = None
    after: Optional[str] = None
    placement: Optional[str] = None  # append | prepend (instruction changes only)


class RemediationTarget(BaseModel):
    scope: str = "cluster"  # cluster | run
    reason: Optional[str] = None
    sample_ids: List[str] = Field(default_factory=list)


class RemediationResult(BaseModel):
    confidence_before: float = 0.0
    confidence_after: float = 0.0
    moved: Dict[str, int] = Field(default_factory=dict)
    # Whole-candidate view: the run's confidence for this candidate, and the projection
    # with re-tested evaluations replaced by their new scores.
    confidence_run_before: Optional[float] = None
    confidence_run_after: Optional[float] = None
    regression_checked: int = 0  # Compatible evaluations re-run to catch regressions
    regression_sample: int = 0   # how many were requested (-1 = all)
    # Re-test bookkeeping (set by Orchestrator.retest_remediation):
    status: str = "done"  # running | done | failed | cancelled | interrupted
    done: int = 0
    total: int = 0
    change: Optional[RemediationChange] = None  # exactly what was applied
    items: List[Dict[str, Any]] = Field(default_factory=list)  # per-evaluation before/after
    cost: float = 0.0
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class Remediation(BaseModel):
    remediation_id: str
    run_id: str
    candidate: str
    target: RemediationTarget
    change: RemediationChange
    result: Optional[RemediationResult] = None
    # Recommender-only fields (a suggestion not yet applied):
    evidence_count: int = 0
    expected_effect: Optional[str] = None
    applied: bool = False


# --------------------------------------------------------------------------- #
# IngestionReport (Doc 2 §6.3 / Doc 5 §4.2)
# --------------------------------------------------------------------------- #
class DetectedModel(BaseModel):
    model: str
    rows: int


class IngestionReport(BaseModel):
    found: int = 0
    evaluable: int = 0
    dropped: Dict[DropReason, int] = Field(default_factory=dict)
    skipped_objects: int = 0
    detected_models: List[DetectedModel] = Field(default_factory=list)
    shape_mix: Dict[Shape, float] = Field(default_factory=dict)
    redaction_rate: float = 0.0
    # Evaluable-row counts per team / per user (for targeting a subset).
    teams: Dict[str, int] = Field(default_factory=dict)
    users: Dict[str, int] = Field(default_factory=dict)

    def reconciles(self) -> bool:
        """found = evaluable + Σ dropped + skipped_objects (Doc 2 §6.3)."""
        return self.found == self.evaluable + sum(self.dropped.values()) + self.skipped_objects
