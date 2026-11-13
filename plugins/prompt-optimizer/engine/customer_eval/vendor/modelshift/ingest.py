"""Ingestion & normalization (Doc 2).

Turns heterogeneous LiteLLM logs (file or S3) into a uniform set of
NormalizedSample records with full drop-reason accounting.

Container detection:      JSONL | JSON array | {data:[...]} / single-array-key wrapper
Recognized row shapes:    SpendLogs (A) | wrapped SDK (B) | Chat Completions (C) | Responses (D)
                          + Shape E (unknown / non-eval)
Canonical request:        system/developer -> instructions; turns -> input_messages (multi-turn kept)
Evaluability (E1-E4):     non-empty prompt AND usable golden AND text modality AND shape A-D
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .schemas import (
    Container,
    DetectedModel,
    DropReason,
    DroppedRow,
    Golden,
    GoldenKind,
    IngestionReport,
    Message,
    Modality,
    NormalizedSample,
    SampleRequest,
    SampleSource,
    Shape,
    ToolCall,
    Usage,
)

REDACTED_TOKENS = {"REDACTED", "[REDACTED]", "***REDACTED***"}
NON_EVAL_CALL_TYPES = {"embedding", "embeddings", "moderation", "image_generation", "transcription"}


# --------------------------------------------------------------------------- #
# Raw row + source loading
# --------------------------------------------------------------------------- #
@dataclass
class RawRow:
    data: Dict[str, Any]
    object_uri: str
    row_index: int


def _maybe_gunzip(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def detect_container(text: str) -> Container:
    """Sniff container: {data:[...]}/single-array-key -> array -> JSONL (Doc 2 §2.2)."""
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                array_keys = [k for k, v in obj.items() if isinstance(v, list)]
                if array_keys:
                    return Container.WRAPPED_DATA
        except json.JSONDecodeError:
            pass
        # object-per-line JSONL also starts with '{'
        return Container.JSONL
    if stripped.startswith("["):
        try:
            json.loads(stripped)
            return Container.JSON_ARRAY
        except json.JSONDecodeError:
            return Container.JSONL
    return Container.JSONL


def _rows_from_wrapped(obj: Dict[str, Any]) -> List[Any]:
    if "data" in obj and isinstance(obj["data"], list):
        return obj["data"]
    for _k, v in obj.items():
        if isinstance(v, list):
            return v
    return []


def iter_rows(raw_bytes: bytes, object_uri: str) -> Iterator[RawRow]:
    """Yield RawRow items from a single log object, streaming JSONL where possible."""
    text = _maybe_gunzip(raw_bytes).decode("utf-8", errors="replace")
    container = detect_container(text)
    if container in (Container.WRAPPED_DATA, Container.JSON_ARRAY):
        obj = json.loads(text.lstrip("\ufeff \t\r\n"))
        rows = _rows_from_wrapped(obj) if isinstance(obj, dict) else obj
        for i, row in enumerate(rows):
            if isinstance(row, dict):
                yield RawRow(data=row, object_uri=object_uri, row_index=i)
            else:
                yield RawRow(data={"__parse_error__": True}, object_uri=object_uri, row_index=i)
        return
    # JSONL
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            yield RawRow(data=row, object_uri=object_uri, row_index=i)
        except json.JSONDecodeError:
            yield RawRow(data={"__parse_error__": True}, object_uri=object_uri, row_index=i)


# --------------------------------------------------------------------------- #
# Shape classification
# --------------------------------------------------------------------------- #
def classify(row: Dict[str, Any]) -> Shape:
    if row.get("__parse_error__"):
        return Shape.UNKNOWN
    call_type = row.get("call_type")
    resp = row.get("response") or {}
    resp_obj = resp.get("object") if isinstance(resp, dict) else None

    # Non-eval call types / stream fragments / embeddings -> unknown (Shape E)
    if call_type in NON_EVAL_CALL_TYPES:
        return Shape.UNKNOWN
    if resp_obj in {"list", "chat.completion.chunk"}:
        return Shape.UNKNOWN

    # Shape A — SpendLogs: proxy_server_request present, or spend+request_id with empty top messages
    top_messages = row.get("messages")
    if "proxy_server_request" in row:
        return Shape.SPENDLOGS
    if "spend" in row and "request_id" in row and (top_messages in (None, {}, [])):
        return Shape.SPENDLOGS

    # Shape D — Responses
    if call_type == "responses" or resp_obj == "response" or (isinstance(resp, dict) and "output" in resp):
        return Shape.RESPONSES

    # Shape B — wrapped SDK: rich metadata (usage_object / model_group / cost_breakdown)
    meta = row.get("metadata") or {}
    if isinstance(meta, dict) and (
        "usage_object" in meta or "cost_breakdown" in meta
    ) or "model_group" in row:
        if isinstance(top_messages, list):
            return Shape.WRAPPED

    # Shape C — Chat Completions
    if isinstance(top_messages, list) and isinstance(resp, dict) and "choices" in resp:
        return Shape.CHAT_COMPLETIONS
    if isinstance(top_messages, list):
        return Shape.CHAT_COMPLETIONS

    return Shape.UNKNOWN


# --------------------------------------------------------------------------- #
# Helpers for reading messages / responses
# --------------------------------------------------------------------------- #
def _text_of_content(content: Any) -> Tuple[Optional[str], Modality]:
    """Extract plain text + modality from a Chat/Responses content field."""
    if content is None:
        return None, Modality.TEXT
    if isinstance(content, str):
        return content, Modality.TEXT
    if isinstance(content, list):
        parts: List[str] = []
        modality = Modality.TEXT
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("text", "input_text", "output_text"):
                if part.get("text"):
                    parts.append(part["text"])
            elif ptype in ("image_url", "input_image", "image"):
                modality = Modality.CONTAINS_IMAGE
            elif ptype in ("input_audio", "audio"):
                modality = Modality.CONTAINS_AUDIO
        return ("\n".join(parts) if parts else None), modality
    return None, Modality.TEXT


def _canonical_request_from_chat(messages: List[Dict[str, Any]],
                                 tools: Optional[Any] = None,
                                 response_format: Optional[Any] = None) -> SampleRequest:
    """Chat Completions messages -> canonical request (Doc 2 §4)."""
    instructions_parts: List[str] = []
    input_messages: List[Message] = []
    modality = Modality.TEXT
    had_tool_calls = False
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        # Detect tool/function-call turns. For now these rows are dropped as
        # non-evaluable (see evaluability()): a `tool`/`function` result message
        # references a preceding assistant `tool_calls` stub, and replaying only
        # the text turns would score the candidate on a truncated prompt vs a
        # golden that had the tool result in context — not apples-to-apples.
        if role in ("tool", "function"):
            had_tool_calls = True
            continue
        text, mod = _text_of_content(m.get("content"))
        if mod != Modality.TEXT:
            modality = mod
        if role == "assistant" and m.get("tool_calls"):
            had_tool_calls = True
            if not text:
                continue
        if role in ("system", "developer"):
            if text:
                instructions_parts.append(text)
        else:
            if text:
                input_messages.append(Message(role=role, content=text))
    return SampleRequest(
        instructions="\n".join(instructions_parts) if instructions_parts else None,
        input_messages=input_messages,
        tools=tools if isinstance(tools, list) else None,
        response_format=response_format if isinstance(response_format, dict) else None,
        modality=modality,
        had_tool_calls=had_tool_calls,
    )


def _golden_from_chat_response(resp: Dict[str, Any]) -> Golden:
    choices = resp.get("choices") or []
    if not choices:
        return Golden(kind=GoldenKind.EMPTY, raw=resp)
    msg = (choices[0] or {}).get("message") or {}
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")
    refusal = msg.get("refusal")
    if content:
        kind = GoldenKind.JSON if _looks_json(content) else GoldenKind.TEXT
        return Golden(kind=kind, text=content, raw=resp)
    if tool_calls:
        tcs = [
            ToolCall(name=(tc.get("function") or {}).get("name", ""),
                     arguments=(tc.get("function") or {}).get("arguments"))
            for tc in tool_calls if isinstance(tc, dict)
        ]
        return Golden(kind=GoldenKind.TOOL_CALL, tool_calls=tcs, raw=resp)
    if refusal:
        return Golden(kind=GoldenKind.REFUSAL, text=refusal, raw=resp)
    return Golden(kind=GoldenKind.EMPTY, raw=resp)


def _golden_from_responses(resp: Dict[str, Any]) -> Golden:
    if resp.get("output_text"):
        txt = resp["output_text"]
        return Golden(kind=GoldenKind.JSON if _looks_json(txt) else GoldenKind.TEXT, text=txt, raw=resp)
    outputs = resp.get("output") or []
    texts: List[str] = []
    tool_calls: List[ToolCall] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            t, _ = _text_of_content(item.get("content"))
            if t:
                texts.append(t)
        elif itype == "function_call":
            tool_calls.append(ToolCall(name=item.get("name", ""), arguments=item.get("arguments")))
    if texts:
        joined = "\n".join(texts)
        return Golden(kind=GoldenKind.JSON if _looks_json(joined) else GoldenKind.TEXT, text=joined, raw=resp)
    if tool_calls:
        return Golden(kind=GoldenKind.TOOL_CALL, tool_calls=tool_calls, raw=resp)
    return Golden(kind=GoldenKind.EMPTY, raw=resp)


def _canonical_request_from_responses(row: Dict[str, Any]) -> SampleRequest:
    """Responses-shaped request -> canonical request."""
    instructions_parts: List[str] = []
    input_messages: List[Message] = []
    modality = Modality.TEXT

    if row.get("instructions"):
        instructions_parts.append(row["instructions"])

    messages = row.get("messages")
    if isinstance(messages, str):
        input_messages.append(Message(role="user", content=messages))
    elif isinstance(messages, dict):
        if messages.get("instructions"):
            instructions_parts.append(messages["instructions"])
        for item in messages.get("input", []) or []:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            text, mod = _text_of_content(item.get("content"))
            if mod != Modality.TEXT:
                modality = mod
            if role in ("system", "developer"):
                if text:
                    instructions_parts.append(text)
            elif text:
                input_messages.append(Message(role=role, content=text))
    return SampleRequest(
        instructions="\n".join(instructions_parts) if instructions_parts else None,
        input_messages=input_messages,
        modality=modality,
    )


def _looks_json(text: str) -> bool:
    s = (text or "").strip()
    if not (s.startswith("{") and s.endswith("}")) and not (s.startswith("[") and s.endswith("]")):
        return False
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


def _is_redacted(text: Optional[str]) -> bool:
    return bool(text) and text.strip() in REDACTED_TOKENS


def _original_latency_of(row: Dict[str, Any]) -> Optional[int]:
    """Original request latency from the legacy log, if present (wrapped SDK shape)."""
    dur = row.get("request_duration_ms")
    if isinstance(dur, (int, float)):
        return int(dur)
    # derive from startTime/endTime ISO timestamps if available
    start, end = row.get("startTime"), row.get("endTime")
    if isinstance(start, str) and isinstance(end, str):
        try:
            from datetime import datetime
            s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            return int((e - s).total_seconds() * 1000)
        except ValueError:
            return None
    return None


def _usage_of(row: Dict[str, Any]) -> Usage:
    meta = row.get("metadata") or {}
    uobj = meta.get("usage_object") if isinstance(meta, dict) else None
    reasoning = None
    if isinstance(uobj, dict):
        ctd = uobj.get("completion_tokens_details") or {}
        reasoning = ctd.get("reasoning_tokens")
    return Usage(
        prompt_tokens=row.get("prompt_tokens"),
        completion_tokens=row.get("completion_tokens"),
        total_tokens=row.get("total_tokens"),
        reasoning_tokens=reasoning,
        spend=row.get("spend"),
    )


def _tags_of(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata") or {}
    tags: Dict[str, Any] = {}
    if isinstance(meta, dict):
        team = meta.get("user_api_key_team_alias")
        if team:
            tags["team"] = team
        user = meta.get("user_api_key_alias") or meta.get("user_api_key_user_id")
        if user:
            tags["user"] = user
    # top-level end-user id (SpendLogs / wrapped shapes)
    if not tags.get("user") and row.get("user") and row.get("user") != "default_user_id":
        tags["user"] = row["user"]
    return tags


# --------------------------------------------------------------------------- #
# Per-shape adapters
# --------------------------------------------------------------------------- #
def adapt_spendlogs(row: Dict[str, Any]) -> Tuple[SampleRequest, Golden]:
    psr = row.get("proxy_server_request") or {}
    messages = psr.get("messages") or []
    req = _canonical_request_from_chat(messages,
                                       tools=psr.get("tools"),
                                       response_format=psr.get("response_format"))
    golden = _golden_from_chat_response(row.get("response") or {})
    return req, golden


def adapt_wrapped(row: Dict[str, Any]) -> Tuple[SampleRequest, Golden]:
    messages = row.get("messages") or []
    req = _canonical_request_from_chat(messages)
    resp = row.get("response") or {}
    if resp.get("object") == "response" or "output" in resp:
        golden = _golden_from_responses(resp)
    else:
        golden = _golden_from_chat_response(resp)
    return req, golden


def adapt_chat_completions(row: Dict[str, Any]) -> Tuple[SampleRequest, Golden]:
    messages = row.get("messages") or []
    req = _canonical_request_from_chat(messages,
                                       tools=row.get("tools"),
                                       response_format=row.get("response_format"))
    golden = _golden_from_chat_response(row.get("response") or {})
    return req, golden


def adapt_responses(row: Dict[str, Any]) -> Tuple[SampleRequest, Golden]:
    req = _canonical_request_from_responses(row)
    golden = _golden_from_responses(row.get("response") or {})
    return req, golden


ADAPTERS = {
    Shape.SPENDLOGS: adapt_spendlogs,
    Shape.WRAPPED: adapt_wrapped,
    Shape.CHAT_COMPLETIONS: adapt_chat_completions,
    Shape.RESPONSES: adapt_responses,
}


# --------------------------------------------------------------------------- #
# Evaluability (E1-E4, Doc 2 §6.1) + drop reason
# --------------------------------------------------------------------------- #
def _prompt_text(req: SampleRequest) -> str:
    parts = [m.content for m in req.input_messages if m.content]
    return "\n".join(parts).strip()


def evaluability(req: SampleRequest, golden: Golden) -> Optional[DropReason]:
    """Return None if evaluable, else the DropReason (E1-E4)."""
    prompt = _prompt_text(req)
    golden_text = (golden.text or "").strip() if golden.text else ""

    # redaction detection first
    if _is_redacted(golden.text) or any(_is_redacted(m.content) for m in req.input_messages):
        return DropReason.REDACTED

    # Tool/function calls not supported for replay yet — skip these rows.
    # Either the request had a tool round-trip, or the golden was itself a tool
    # call (no text to compare against a replayed text response).
    if req.had_tool_calls or golden.kind == GoldenKind.TOOL_CALL:
        return DropReason.TOOL_CALLS_UNSUPPORTED

    # E3 modality
    if req.modality != Modality.TEXT:
        return DropReason.UNSUPPORTED_MODALITY

    # E1 non-empty prompt
    if not prompt:
        return DropReason.NO_PROMPT

    # E2 usable golden
    if golden.kind == GoldenKind.EMPTY:
        return DropReason.EMPTY_RESPONSE
    if golden.kind in (GoldenKind.TEXT, GoldenKind.JSON, GoldenKind.REFUSAL) and not golden_text:
        return DropReason.EMPTY_RESPONSE
    if golden.kind == GoldenKind.TOOL_CALL and not golden.tool_calls:
        return DropReason.TOOL_ONLY_NO_TEXT

    return None


# --------------------------------------------------------------------------- #
# Normalize a single row
# --------------------------------------------------------------------------- #
def normalize_row(raw: RawRow) -> Tuple[Optional[NormalizedSample], Optional[DroppedRow], Shape]:
    row = raw.data
    shape = classify(row)
    sample_id = str(row.get("request_id") or row.get("id") or f"{raw.object_uri}#{raw.row_index}")
    source = SampleSource(shape=shape, object_uri=raw.object_uri, row_index=raw.row_index)

    if row.get("__parse_error__"):
        return None, DroppedRow(id=sample_id, source=source, eval_block_reason=DropReason.PARSE_ERROR), shape

    if shape == Shape.UNKNOWN:
        resp = row.get("response") or {}
        resp_obj = resp.get("object") if isinstance(resp, dict) else None
        if resp_obj == "chat.completion.chunk":
            reason = DropReason.STREAM_FRAGMENT
        elif row.get("call_type") in NON_EVAL_CALL_TYPES or resp_obj == "list":
            reason = DropReason.NON_EVAL_CALL_TYPE
        else:
            reason = DropReason.UNKNOWN_SHAPE
        return None, DroppedRow(id=sample_id, source=source, eval_block_reason=reason), shape

    req, golden = ADAPTERS[shape](row)
    legacy_model = row.get("model_group") or row.get("model")
    reason = evaluability(req, golden)

    sample = NormalizedSample(
        id=sample_id,
        source=source,
        legacy_model=legacy_model,
        request=req,
        golden=golden,
        usage=_usage_of(row),
        original_latency_ms=_original_latency_of(row),
        tags=_tags_of(row),
        evaluable=reason is None,
        eval_block_reason=reason,
    )
    if reason is not None:
        return None, DroppedRow(id=sample_id, source=source, eval_block_reason=reason), shape
    return sample, None, shape


# --------------------------------------------------------------------------- #
# Full ingestion over one or more objects
# --------------------------------------------------------------------------- #
@dataclass
class IngestionResult:
    samples: List[NormalizedSample] = field(default_factory=list)
    dropped: List[DroppedRow] = field(default_factory=list)
    report: IngestionReport = field(default_factory=IngestionReport)


def ingest(objects: Iterable[Tuple[bytes, str]], skipped_objects: int = 0) -> IngestionResult:
    """Ingest an iterable of (raw_bytes, object_uri) into samples + report.

    `skipped_objects` counts non-log objects the caller already filtered out
    (e.g. from an S3 prefix listing), for the reconciliation identity.
    """
    result = IngestionResult()
    dropped_counts: Dict[DropReason, int] = {}
    model_counts: Dict[str, int] = {}
    shape_counts: Dict[Shape, int] = {}
    team_counts: Dict[str, int] = {}
    user_counts: Dict[str, int] = {}
    found = 0
    redacted = 0

    for raw_bytes, uri in objects:
        for raw in iter_rows(raw_bytes, uri):
            found += 1
            sample, drop, shape = normalize_row(raw)
            shape_counts[shape] = shape_counts.get(shape, 0) + 1
            if sample is not None:
                result.samples.append(sample)
                m = sample.legacy_model or "unknown"
                model_counts[m] = model_counts.get(m, 0) + 1
                t = sample.tags.get("team")
                if t:
                    team_counts[str(t)] = team_counts.get(str(t), 0) + 1
                u = sample.tags.get("user")
                if u:
                    user_counts[str(u)] = user_counts.get(str(u), 0) + 1
            else:
                result.dropped.append(drop)
                dropped_counts[drop.eval_block_reason] = dropped_counts.get(drop.eval_block_reason, 0) + 1
                if drop.eval_block_reason == DropReason.REDACTED:
                    redacted += 1

    total_rows = max(found, 1)
    result.report = IngestionReport(
        found=found,
        evaluable=len(result.samples),
        dropped=dropped_counts,
        skipped_objects=skipped_objects,
        detected_models=[DetectedModel(model=m, rows=c) for m, c in sorted(model_counts.items())],
        shape_mix={s: round(c / total_rows, 4) for s, c in shape_counts.items()},
        redaction_rate=round(redacted / total_rows, 4),
        teams=dict(sorted(team_counts.items())),
        users=dict(sorted(user_counts.items())),
    )
    return result
