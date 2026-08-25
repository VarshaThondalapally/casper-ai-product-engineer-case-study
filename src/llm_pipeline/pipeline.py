"""Orchestrate evidence extraction, policy, and independent recipe variants."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from .models import (
    DirectoryRun,
    ExtractionCall,
    ModelCallStats,
    PipelineResult,
    PolicyTrace,
    Recipe,
    RecipeLine,
    ReviewAnalysis,
    ReviewEvidence,
    RunTrace,
)
from .normalization import build_recipe_lines, normalize_featured_reviews, parse_recipe
from .recipe_modifier import evaluate_review_bundle
from .security import safe_exception_text
from .tweak_extractor import TweakExtractor
from .version import PIPELINE_VERSION

MAX_RECIPE_FILES_PER_RUN = 50
MAX_MODEL_CALLS_PER_DIRECTORY_RUN = 100

_SAFE_OUTPUT_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *{f"com{number}" for number in range(1, 10)},
    *{f"lpt{number}" for number in range(1, 10)},
}


def safe_output_component(value: str) -> str:
    """Return a stable filesystem component without trusting a source identifier."""
    normalized = _SAFE_OUTPUT_COMPONENT.sub("_", value).strip("._")
    if not normalized:
        normalized = "recipe"
    normalized = normalized[:80]
    if (
        normalized != value
        or normalized.casefold() in _WINDOWS_RESERVED_NAMES
        or value != value.casefold()
    ):
        digest = sha256(value.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized}-{digest}"
    return normalized


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _recipe_output_directory(root: Path, recipe_id: str) -> Path:
    resolved_root = root.resolve()
    candidate = resolved_root / safe_output_component(recipe_id)
    if candidate.is_symlink():
        raise ValueError("Recipe output directory may not be a symbolic link")
    destination = candidate.resolve()
    if destination.parent != resolved_root:
        raise ValueError("Resolved recipe output escaped the configured output directory")
    return destination


class Extractor(Protocol):
    def analyze(
        self,
        review: ReviewEvidence,
        recipe: Recipe,
        recipe_lines: list[RecipeLine],
        *,
        capture_trace: bool = False,
    ) -> ExtractionCall: ...


class LLMAnalysisPipeline:
    def __init__(self, extractor: Extractor | None = None) -> None:
        self.extractor = extractor or TweakExtractor()

    def _process_recipe_data(
        self, raw: dict[str, Any], *, capture_trace: bool
    ) -> tuple[PipelineResult, RunTrace | None]:
        original = parse_recipe(raw)
        lines = build_recipe_lines(original)
        reviews = normalize_featured_reviews(raw, original.recipe_id)
        analyses: list[ReviewAnalysis] = []
        decisions = []
        candidates = []
        failures: dict[str, str] = {}
        call_stats: list[ModelCallStats] = []
        model_traces = []
        policy_traces: list[PolicyTrace] = []

        for review in reviews:
            try:
                call = self.extractor.analyze(
                    review,
                    original,
                    lines,
                    capture_trace=capture_trace,
                )
                analysis = call.analysis
                if analysis.review_id != review.review_id:
                    raise ValueError("Extractor returned a mismatched review_id")
                analyses.append(analysis)
                call_stats.append(call.stats)
                if call.trace is not None:
                    model_traces.append(call.trace)
                decision, candidate = evaluate_review_bundle(original, lines, review, analysis)
                decisions.append(decision)
                if candidate is not None:
                    candidates.append(candidate)
                if capture_trace:
                    policy_traces.append(
                        PolicyTrace(
                            review_id=review.review_id,
                            decision=decision,
                            candidate=candidate,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - isolate failures per review
                failures[review.review_id] = safe_exception_text(exc)
                failed_trace = getattr(exc, "trace", None)
                if capture_trace and failed_trace is not None:
                    model_traces.append(failed_trace)

        limitations = [
            "Candidates are independent alternatives; cross-review changes are not merged.",
            "Candidates are not ranked because the supplied data has no helpful-vote signal.",
            "Only featured_tweaks are treated as in-scope evidence.",
        ]
        if not reviews:
            limitations.append("This recipe has no featured tweaks, so no model call was made.")

        result = PipelineResult(
            pipeline_version=PIPELINE_VERSION,
            original_recipe=original,
            featured_reviews=reviews,
            analyses=analyses,
            decisions=decisions,
            candidates=candidates,
            extraction_failures=failures,
            model_calls=call_stats,
            limitations=limitations,
        )
        trace = (
            RunTrace(
                recipe_id=original.recipe_id,
                model_calls=model_traces,
                policy_decisions=policy_traces,
                extraction_failures=failures,
            )
            if capture_trace
            else None
        )
        return result, trace

    def process_recipe_data(self, raw: dict[str, Any]) -> PipelineResult:
        result, _ = self._process_recipe_data(raw, capture_trace=False)
        return result

    def process_recipe_data_with_trace(
        self, raw: dict[str, Any]
    ) -> tuple[PipelineResult, RunTrace]:
        result, trace = self._process_recipe_data(raw, capture_trace=True)
        if trace is None:  # pragma: no cover - guaranteed by capture_trace=True
            raise RuntimeError("Trace capture was requested but not produced")
        return result, trace

    def process_file(self, recipe_path: str | Path) -> PipelineResult:
        path = Path(recipe_path)
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return self.process_recipe_data(raw)

    def process_file_with_trace(self, recipe_path: str | Path) -> tuple[PipelineResult, RunTrace]:
        path = Path(recipe_path)
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return self.process_recipe_data_with_trace(raw)

    @staticmethod
    def save_result(result: PipelineResult, output_path: str | Path) -> Path:
        path = Path(output_path)
        atomic_write_text(path, result.model_dump_json(indent=2))
        return path

    @staticmethod
    def result_output_path(output_dir: str | Path, recipe_id: str) -> Path:
        return _recipe_output_directory(Path(output_dir), recipe_id) / "result.json"

    @staticmethod
    def save_trace(
        trace: RunTrace,
        trace_dir: str | Path,
        result_path: str | Path,
    ) -> Path:
        """Write review-level trace files without credentials or HTTP headers."""
        destination = Path(trace_dir)
        destination.mkdir(parents=True, exist_ok=True)
        written_files: list[str] = []

        for call in trace.model_calls:
            rank = call.review_id.rsplit(":", maxsplit=1)[-1]
            request_name = f"review-{rank}-request.json"
            response_name = f"review-{rank}-response.json"
            atomic_write_text(destination / request_name, call.request.model_dump_json(indent=2))
            response_payload = {
                "review_id": call.review_id,
                "response": call.response.model_dump(mode="json")
                if call.response is not None
                else None,
                "error": call.error,
            }
            atomic_write_text(destination / response_name, json.dumps(response_payload, indent=2))
            written_files.extend([request_name, response_name])

        for policy in trace.policy_decisions:
            rank = policy.review_id.rsplit(":", maxsplit=1)[-1]
            policy_name = f"review-{rank}-policy.json"
            atomic_write_text(destination / policy_name, policy.model_dump_json(indent=2))
            written_files.append(policy_name)

        result = Path(result_path)
        resolved_destination = destination.resolve()
        summary = {
            "trace_version": trace.trace_version,
            "generated_at": datetime.now(UTC).isoformat(),
            "recipe_id": trace.recipe_id,
            "result_path": os.path.relpath(result.resolve(), start=resolved_destination),
            "trace_path": ".",
            "model_call_count": len(trace.model_calls),
            "policy_decision_count": len(trace.policy_decisions),
            "extraction_failures": trace.extraction_failures,
            "files": sorted(written_files),
            "disclosure": trace.disclosure,
        }
        summary_path = destination / "run-summary.json"
        atomic_write_text(summary_path, json.dumps(summary, indent=2))
        return summary_path

    def process_directory(
        self,
        data_dir: str | Path,
        output_dir: str | Path,
        *,
        capture_trace: bool = False,
    ) -> DirectoryRun:
        source = Path(data_dir)
        destination = Path(output_dir)
        if not source.is_dir():
            raise ValueError(f"Recipe data directory does not exist: {source}")
        recipe_paths = sorted(source.glob("recipe_*.json"))
        if not recipe_paths:
            raise ValueError(f"No recipe_*.json files found in: {source}")
        if len(recipe_paths) > MAX_RECIPE_FILES_PER_RUN:
            raise ValueError(
                f"A directory run may contain at most {MAX_RECIPE_FILES_PER_RUN} recipe files"
            )
        results: list[PipelineResult] = []
        file_failures: dict[str, str] = {}
        planned_model_calls = 0
        for recipe_path in recipe_paths:
            try:
                with recipe_path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                original = parse_recipe(raw)
                review_count = len(normalize_featured_reviews(raw, original.recipe_id))
                if planned_model_calls + review_count > MAX_MODEL_CALLS_PER_DIRECTORY_RUN:
                    raise ValueError(
                        "Directory run exceeds the "
                        f"{MAX_MODEL_CALLS_PER_DIRECTORY_RUN}-call model budget"
                    )
                planned_model_calls += review_count
                result, trace = self._process_recipe_data(raw, capture_trace=capture_trace)
                result_path = self.result_output_path(destination, result.original_recipe.recipe_id)
                recipe_dir = result_path.parent
                self.save_result(result, result_path)
                if trace is not None:
                    self.save_trace(trace, recipe_dir / "trace", result_path)
                results.append(result)
            except Exception as exc:  # noqa: BLE001 - isolate malformed source files
                file_failures[recipe_path.name] = safe_exception_text(exc)
        return DirectoryRun(results=results, file_failures=file_failures)
