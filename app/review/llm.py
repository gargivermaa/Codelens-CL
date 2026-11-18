"""
Review LLM client (Gemini, free tier).

Structured output: the request asks for application/json constrained by the
Pydantic schema, and the response is validated with that same schema. A
malformed or empty response is retried once with the validation error fed
back; after that the caller gets an LLMError - never invented findings.
"""
import logging
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger("codelens.llm")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class LLMError(RuntimeError):
    """The model failed or returned unusable output."""


class LLMQuotaError(LLMError):
    """Free-tier quota / rate limit exhausted (retry later; never switch to paid)."""


class LLMConfigurationError(RuntimeError):
    """The configured model does not exist / is not available to this key.

    Deliberately NOT an LLMError: the pipeline tolerates LLMError per pass, but a
    wrong model name must stop the review loudly instead of silently producing
    rules-only reviews."""


@dataclass
class LLMResult:
    data: BaseModel
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    attempts: int


class GeminiReviewLLM:
    def __init__(self, client=None, model: str | None = None, max_output_tokens: int = 8192):
        self.model = model or settings.GEMINI_REVIEW_MODEL
        self.max_output_tokens = max_output_tokens
        self._client = client
        self._last_call = 0.0

    @property
    def client(self):
        if self._client is None:
            if not settings.GEMINI_API_KEY:
                raise LLMError("GEMINI_API_KEY is not set (add it to .env)")
            self._client = genai.Client(
                api_key=settings.GEMINI_API_KEY,
                http_options=types.HttpOptions(
                    timeout=120_000,
                    retry_options=types.HttpRetryOptions(
                        attempts=settings.GEMINI_MAX_ATTEMPTS, initial_delay=2.0, max_delay=60.0,
                        http_status_codes=[408, 429, 500, 502, 503, 504]),
                ),
            )
        return self._client

    def _throttle(self) -> None:
        wait = self._last_call + settings.GEMINI_MIN_REQUEST_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def generate(self, system: str, prompt: str, schema: type[BaseModel]) -> LLMResult:
        started = time.perf_counter()
        input_tokens = output_tokens = 0
        usage_known = True
        contents = prompt
        last_problem = "no response"

        for attempt in (1, 2):
            self._throttle()
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.1,
                        max_output_tokens=self.max_output_tokens,
                        response_mime_type="application/json",
                        response_schema=schema,
                    ),
                )
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                if code == 429 or "RESOURCE_EXHAUSTED" in str(getattr(exc, "status", "")):
                    raise LLMQuotaError(f"Gemini free-tier quota/rate limit exhausted for {self.model}; "
                                        f"retry later. Details: {exc}") from exc
                if code == 404:
                    raise LLMConfigurationError(
                        f"Review model {self.model!r} is not available to this API key. Set "
                        f"GEMINI_REVIEW_MODEL in .env to a model that is free of charge on the "
                        f"free tier (python -m app.cli models lists available ones). Details: {exc}") from exc
                raise LLMError(f"Gemini request failed: {exc}") from exc

            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = getattr(usage, "prompt_token_count", None)
            output = getattr(usage, "candidates_token_count", None)
            if prompt_tokens is None:
                usage_known = False
            else:
                input_tokens += int(prompt_tokens)
                output_tokens += int(output or 0)

            text = _response_text(response)
            if not text:
                last_problem = _empty_reason(response)
            else:
                try:
                    data = schema.model_validate_json(_FENCE.sub("", text))
                    return LLMResult(data, self.model, input_tokens if usage_known else None,
                                     output_tokens if usage_known else None,
                                     int((time.perf_counter() - started) * 1000), attempt)
                except ValidationError as exc:
                    last_problem = f"invalid JSON for schema: {exc.errors()[:3]}"
            logger.warning("Malformed LLM output (attempt %d): %s", attempt, last_problem)
            contents = (prompt + "\n\nYour previous answer was rejected: " + str(last_problem)[:500]
                        + "\nReturn ONLY JSON that matches the schema.")
        raise LLMError(f"Model {self.model} returned unusable output twice: {last_problem}")


def _response_text(response) -> str:
    try:
        return (response.text or "").strip()
    except (ValueError, AttributeError):
        return ""


def _empty_reason(response) -> str:
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return f"prompt blocked: {feedback.block_reason}"
    candidates = getattr(response, "candidates", None) or []
    if candidates and getattr(candidates[0], "finish_reason", None):
        return f"empty output (finish_reason={candidates[0].finish_reason})"
    return "empty output"
