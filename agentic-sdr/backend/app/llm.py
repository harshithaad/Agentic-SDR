"""Claude access with the observability the spec demands: every call returns
token usage + latency and the caller logs them. Retries are bounded and
exponential (tenacity); JSON is parsed defensively; there is deliberately no
'one more call for luck' path — the old code made up to 9 API calls per draft."""
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.metrics import LLM_CALLS, LLM_TOKENS


class LLMNotConfigured(Exception):
    pass


class LLMOutputInvalid(Exception):
    """Model returned text we could not parse into the requested schema."""


@dataclass
class LLMResult:
    parsed: Any                 # dict for JSON prompts, str for text prompts
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if not settings.ANTHROPIC_API_KEY:
        raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=0)
    return _client


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> Dict:
    """Tolerate fenced output or leading prose; find the first JSON object."""
    text = text.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMOutputInvalid(f"no JSON object in output: {text[:200]!r}")
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMOutputInvalid(f"invalid JSON from model: {e}") from e


@retry(
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.APIStatusError,
         anthropic.RateLimitError, LLMOutputInvalid)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=15),
    reraise=True,
)
def _call(prompt_name: str, system: str, user: str, max_tokens: int) -> LLMResult:
    client = _get_client()
    started = time.monotonic()
    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
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
