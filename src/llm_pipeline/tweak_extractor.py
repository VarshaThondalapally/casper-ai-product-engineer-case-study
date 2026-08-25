"""Single-pass semantic interpretation using Responses Structured Outputs."""

from __future__ import annotations

import os
import time
from typing import Any

from openai import OpenAI

from .grounding import canonicalize_analysis_quotes
from .models import (
    ExtractionCall,
    ModelCallStats,
    ModelCallTrace,
    Recipe,
    RecipeLine,
    ReviewAnalysis,
    ReviewEvidence,
    TraceRequest,
    TraceResponse,
)
from .prompts import EXTRACTION_INSTRUCTIONS, build_extraction_input
from .security import safe_exception_text

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_OUTPUT_TOKENS = 3000
ALLOWED_REASONING_EFFORTS = {"low", "medium", "high"}


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class ExtractionError(RuntimeError):
    def __init__(self, message: str, trace: ModelCallTrace | None = None) -> None:
        super().__init__(message)
        self.trace = trace


def _usage(response: Any, field: str) -> int:
    usage = getattr(response, "usage", None)
    value = getattr(usage, field, 0) if usage is not None else 0
    return value if isinstance(value, int) else 0


class TweakExtractor:
    """The LLM interprets language; it never mutates a recipe."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        reasoning_effort: str | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        if not self.model.strip():
            raise ValueError("OpenAI model name must not be empty")
        self._api_key = api_key
        self._client = client
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _bounded_float("OPENAI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 1.0, 300.0)
        )
        if not 1.0 <= self.timeout_seconds <= 300.0:
            raise ValueError("timeout_seconds must be between 1 and 300")
        self.max_retries = (
            max_retries
            if max_retries is not None
            else _bounded_int("OPENAI_MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, 5)
        )
        if not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        self.reasoning_effort = reasoning_effort or os.getenv("OPENAI_REASONING_EFFORT", "medium")
        if self.reasoning_effort not in ALLOWED_REASONING_EFFORTS:
            raise ValueError(
                "OPENAI_REASONING_EFFORT must be one of: "
                + ", ".join(sorted(ALLOWED_REASONING_EFFORTS))
            )
        self.max_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else _bounded_int("OPENAI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, 256, 10_000)
        )
        if not 256 <= self.max_output_tokens <= 10_000:
            raise ValueError("max_output_tokens must be between 256 and 10000")

    @property
    def client(self) -> Any:
        if self._client is None:
            api_key = self._api_key or os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ExtractionError("OPENAI_API_KEY is not configured")
            self._client = OpenAI(
                api_key=api_key,
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        return self._client

    def analyze(
        self,
        review: ReviewEvidence,
        recipe: Recipe,
        recipe_lines: list[RecipeLine],
        *,
        capture_trace: bool = False,
    ) -> ExtractionCall:
        request_input = build_extraction_input(review, recipe, recipe_lines)
        request_arguments = {
            "model": self.model,
            "instructions": EXTRACTION_INSTRUCTIONS,
            "input": request_input,
            "text_format": ReviewAnalysis,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "tools": [],
            "parallel_tool_calls": False,
            "store": False,
        }
        trace_request = (
            TraceRequest(
                model=self.model,
                instructions=EXTRACTION_INSTRUCTIONS,
                input=request_input,
                text_format=ReviewAnalysis.__name__,
                output_schema=ReviewAnalysis.model_json_schema(),
                reasoning={"effort": self.reasoning_effort},
                max_output_tokens=self.max_output_tokens,
                tools=[],
                parallel_tool_calls=False,
                store=False,
            )
            if capture_trace
            else None
        )
        started = time.perf_counter()
        try:
            response = self.client.responses.parse(**request_arguments)
        except Exception as exc:
            latency = time.perf_counter() - started
            safe_error = safe_exception_text(exc)
            trace = (
                ModelCallTrace(
                    review_id=review.review_id,
                    request=trace_request,
                    error=safe_error,
                )
                if trace_request is not None
                else None
            )
            raise ExtractionError(f"Responses API call failed: {safe_error}", trace) from exc

        latency = time.perf_counter() - started
        analysis = getattr(response, "output_parsed", None)
        output_text = getattr(response, "output_text", None)
        response_trace = (
            TraceResponse(
                response_id=getattr(response, "id", None),
                model=getattr(response, "model", self.model),
                status=getattr(response, "status", None),
                input_tokens=_usage(response, "input_tokens"),
                output_tokens=_usage(response, "output_tokens"),
                latency_seconds=round(latency, 6),
                output_text=output_text if isinstance(output_text, str) else None,
                output_parsed=analysis if isinstance(analysis, ReviewAnalysis) else None,
            )
            if trace_request is not None
            else None
        )
        trace = (
            ModelCallTrace(
                review_id=review.review_id,
                request=trace_request,
                response=response_trace,
            )
            if trace_request is not None
            else None
        )
        if not isinstance(analysis, ReviewAnalysis):
            raise ExtractionError("Model returned no parsed structured output", trace)
        if analysis.review_id != review.review_id:
            raise ExtractionError("Model returned a mismatched review_id", trace)
        analysis, canonicalized_quote_count = canonicalize_analysis_quotes(analysis, review.text)
        return ExtractionCall(
            analysis=analysis,
            stats=ModelCallStats(
                review_id=review.review_id,
                model=getattr(response, "model", self.model),
                response_id=getattr(response, "id", None),
                status=getattr(response, "status", None),
                input_tokens=_usage(response, "input_tokens"),
                output_tokens=_usage(response, "output_tokens"),
                latency_seconds=round(latency, 6),
                canonicalized_source_quotes=canonicalized_quote_count,
            ),
            trace=trace,
        )
