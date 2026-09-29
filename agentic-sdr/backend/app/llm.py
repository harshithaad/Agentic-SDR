"""LLM access with the observability the spec demands: every call returns
token usage + latency and the caller logs them. Retries are bounded and
exponential (tenacity); JSON is parsed defensively; there is deliberately no
'one more call for luck' path — the old code made up to 9 API calls per draft.

Provider is swappable (Anthropic Claude or Google Gemini) behind one interface
— every stage handler calls complete_json/complete_text and never sees which
provider answered. LLM_PROVIDER in config picks the backend; only this module
knows both SDKs exist."""
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.metrics import LLM_CALLS, LLM_TOKENS


class LLMNotConfigured(Exception):
    pass


class LLMOutputInvalid(Exception):
    """Model returned text we could not parse into the requested schema."""


class LLMTransientError(Exception):
    """Provider-agnostic wrapper for retryable errors (rate limit, connection)."""


@dataclass
class LLMResult:
    parsed: Any                 # dict for JSON prompts, str for text prompts
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


_anthropic_client = None
_gemini_client = None


def _get_anthropic_client():
    global _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=0)
    return _anthropic_client


def _get_gemini_client():
    global _gemini_client
    if not settings.GEMINI_API_KEY:
        raise LLMNotConfigured("GEMINI_API_KEY is not set")
    if _gemini_client is None:
        from google import genai
        from google.genai import types
        # An unbounded LLM call is a liveness bug, not just slow: the worker
        # holds its Kafka partition while blocked, and once it exceeds
        # max.poll.interval.ms the broker evicts it and the whole stage stalls.
        # Cap well under that interval so a hung provider fails one message,
        # not the pipeline.
        _gemini_client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=settings.LLM_TIMEOUT_MS),
        )
    return _gemini_client


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
# \' and similar are valid escapes in Python/JS but not in JSON
_ILLEGAL_ESCAPE_RE = re.compile(r"\\(['/])")


def extract_json(text: str) -> Dict:
    """Tolerate fenced output, leading prose, and truncation.

    Models that hit their output cap emit valid JSON with the closing braces
    missing. Rather than discarding a good research summary over a syntax
    technicality, we repair the tail: close any open string, then close open
    brackets in the order they were opened."""
    text = text.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    else:
        # unterminated fence (truncated mid-output) — drop the opening marker
        text = re.sub(r"^```(?:json)?\s*", "", text)
    start = text.find("{")
    if start == -1:
        raise LLMOutputInvalid(f"no JSON object in output: {text[:200]!r}")
    text = text[start:]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # models sometimes emit \' — legal in Python/JS source, illegal in JSON
    cleaned = _ILLEGAL_ESCAPE_RE.sub(r"\1", text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    repaired = _repair_truncated_json(cleaned)
    if repaired is not None:
        return repaired
    raise LLMOutputInvalid(f"unparseable JSON from model: {text[:200]!r}")


def _repair_truncated_json(text: str) -> Optional[Dict]:
    """Best-effort completion of JSON cut off mid-emission. Returns None if the
    text is malformed in a way truncation alone cannot explain."""
    stack: list = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    candidate = text
    if in_string:
        # truncation can land mid-escape ("...\\n\\") — a lone trailing
        # backslash would escape the quote we are about to add
        if escaped:
            candidate = candidate[:-1]
        candidate += '"'
    # drop a dangling key or comma that has no value yet
    candidate = re.sub(r",\s*$", "", candidate.rstrip())
    candidate = re.sub(r',\s*"[^"]*"\s*:\s*$', "", candidate)
    candidate = re.sub(r'"[^"]*"\s*:\s*$', "", candidate.rstrip())
    candidate = re.sub(r",\s*$", "", candidate.rstrip())
    for opener in reversed(stack):
        candidate += "}" if opener == "{" else "]"
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _call_anthropic(prompt_name: str, system: str, user: str, max_tokens: int) -> LLMResult:
    import anthropic
    client = _get_anthropic_client()
    started = time.monotonic()
    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError) as e:
        LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
        raise LLMTransientError(str(e)) from e
    except Exception:
        LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
        raise
    latency_ms = int((time.monotonic() - started) * 1000)
    usage = response.usage
    LLM_CALLS.labels(prompt=prompt_name, result="ok").inc()
    LLM_TOKENS.labels(prompt=prompt_name, direction="input").inc(usage.input_tokens)
    LLM_TOKENS.labels(prompt=prompt_name, direction="output").inc(usage.output_tokens)
    return LLMResult(
        parsed=response.content[0].text,
        model=settings.CLAUDE_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        latency_ms=latency_ms,
    )


def _call_gemini(prompt_name: str, system: str, user: str, max_tokens: int) -> LLMResult:
    from google.genai import errors as genai_errors
    client = _get_gemini_client()
    started = time.monotonic()
    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=user,
            config={"system_instruction": system, "max_output_tokens": max_tokens},
        )
    except genai_errors.ServerError as e:
        LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
        raise LLMTransientError(str(e)) from e
    except genai_errors.ClientError as e:
        # 429 (rate limit) is transient; other 4xx (bad key, bad request) are not
        if getattr(e, "code", None) == 429:
            LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
            raise LLMTransientError(str(e)) from e
        LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
        raise
    except Exception:
        LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
        raise
    latency_ms = int((time.monotonic() - started) * 1000)
    usage = response.usage_metadata
    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage, "candidates_token_count", 0) or 0
    text = response.text
    if text is None:
        # empty/blocked response (e.g. safety filter) — treat as invalid output,
        # not a crash, so the caller's retry-with-feedback path can react
        LLM_CALLS.labels(prompt=prompt_name, result="error").inc()
        raise LLMOutputInvalid(
            f"Gemini returned no text (finish_reason="
            f"{response.candidates[0].finish_reason if response.candidates else '?'})"
        )
    LLM_CALLS.labels(prompt=prompt_name, result="ok").inc()
    LLM_TOKENS.labels(prompt=prompt_name, direction="input").inc(input_tokens)
    LLM_TOKENS.labels(prompt=prompt_name, direction="output").inc(output_tokens)
    return LLMResult(
        parsed=text,
        model=settings.GEMINI_MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )


_pace_lock = threading.Lock()
_last_call_at = 0.0


def _pace() -> None:
    """Serialize and space out provider calls. Rate-limit rejections still cost
    a request against the quota, so backing off *before* sending beats retrying
    after being refused."""
    global _last_call_at
    interval = settings.LLM_MIN_INTERVAL_MS / 1000.0
    with _pace_lock:
        wait = _last_call_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


@retry(
    retry=retry_if_exception_type((LLMTransientError, LLMOutputInvalid)),
    stop=stop_after_attempt(4),
    # providers commonly ask for ~30s on a 429; 15s max just re-hits the wall
    wait=wait_exponential(multiplier=2, min=5, max=45),
    reraise=True,
)
def _call(prompt_name: str, system: str, user: str, max_tokens: int) -> LLMResult:
    _pace()
    if settings.LLM_PROVIDER == "gemini":
        return _call_gemini(prompt_name, system, user, max_tokens)
    return _call_anthropic(prompt_name, system, user, max_tokens)


def complete_json(prompt_name: str, system: str, user: str, max_tokens: int = 1024) -> LLMResult:
    """JSON-mode completion. Parse failures count as retryable attempts (the retry
    decorator wraps parsing too, via LLMOutputInvalid)."""
    @retry(
        retry=retry_if_exception_type(LLMOutputInvalid),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    def call_and_parse() -> LLMResult:
        result = _call(prompt_name, system, user, max_tokens)
        result.parsed = extract_json(result.parsed)
        return result

    return call_and_parse()


def complete_text(prompt_name: str, system: str, user: str, max_tokens: int = 512) -> LLMResult:
    return _call(prompt_name, system, user, max_tokens)
