"""Run the frozen, field-aware live evaluation against every featured review."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv

from llm_pipeline.evaluation import (
    match_intent_labels,
    match_outcome_labels,
    score_expected_edits,
)
from llm_pipeline.grounding import canonicalize_analysis_quotes
from llm_pipeline.models import ModelCallStats, ReviewAnalysis
from llm_pipeline.normalization import (
    build_recipe_lines,
    normalize_featured_reviews,
    parse_recipe,
)
from llm_pipeline.pipeline import atomic_write_text
from llm_pipeline.prompts import EXTRACTION_INSTRUCTIONS
from llm_pipeline.recipe_modifier import evaluate_review_bundle
from llm_pipeline.security import safe_exception_text
from llm_pipeline.tweak_extractor import TweakExtractor
from llm_pipeline.version import PIPELINE_VERSION

EVALUATION_VERSION = "2.0.0"
THRESHOLDS = {
    "labeled_intent_recall": 0.80,
    "labeled_intent_precision": 0.80,
    "evidence_state_accuracy_on_matched": 0.90,
    "intent_kind_accuracy_on_matched": 0.85,
    "actionability_accuracy_on_matched": 0.85,
    "source_quote_grounding": 1.0,
    "outcome_label_recall": 0.90,
    "outcome_quote_grounding": 1.0,
    "bundle_status_accuracy": 1.0,
    "candidate_presence_accuracy": 1.0,
    "edit_target_operation_recall": 1.0,
    "edit_precondition_accuracy": 1.0,
    "edit_output_constraint_accuracy": 1.0,
    "exact_applied_bundle_accuracy": 1.0,
}


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _digest_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data")
    parser.add_argument("--labels", default="evaluation/golden_labels.json")
    parser.add_argument("--output", default="outputs/evaluation-report.json")
    parser.add_argument(
        "--replay-report",
        help="Rescore stored extracted intents without making model calls",
    )
    args = parser.parse_args()
    load_dotenv()

    label_path = Path(args.labels)
    labels = json.loads(label_path.read_text(encoding="utf-8"))
    raw_by_id = {}
    for path in Path(args.data).glob("recipe_*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw_by_id[str(raw["recipe_id"])] = raw

    replay_path = Path(args.replay_report) if args.replay_report else None
    replay_report = json.loads(replay_path.read_text(encoding="utf-8")) if replay_path else None
    replay_cases = (
        {(case["recipe_id"], case["featured_rank"]): case for case in replay_report["cases"]}
        if replay_report
        else {}
    )
    extractor = None if replay_report else TweakExtractor()
    if replay_report:
        requested_model = replay_report["metadata"]["requested_model"]
    else:
        if extractor is None:  # pragma: no cover - construction invariant
            raise RuntimeError("Live evaluation requires an extractor")
        requested_model = extractor.model
    counters = {
        "labels": 0,
        "matched_labels": 0,
        "correct_states": 0,
        "correct_kinds": 0,
        "correct_actionability": 0,
        "grounded_quotes": 0,
        "total_intents": 0,
        "outcome_labels": 0,
        "matched_outcomes": 0,
        "grounded_claim_quotes": 0,
        "total_claims": 0,
        "correct_bundle_statuses": 0,
        "correct_candidate_presence": 0,
        "expected_edits": 0,
        "actual_scored_edits": 0,
        "matched_edits": 0,
        "correct_preconditions": 0,
        "correct_outputs": 0,
        "scored_bundles": 0,
        "exact_bundles": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "canonicalized_source_quotes": 0,
    }
    failures = []
    cases = []
    total_latency_seconds = 0.0
    bundle_counts = {"applied": 0, "needs_review": 0, "not_applied": 0}
    candidate_count = 0

    for case in labels:
        recipe = parse_recipe(raw_by_id[case["recipe_id"]])
        lines = build_recipe_lines(recipe)
        reviews = normalize_featured_reviews(raw_by_id[case["recipe_id"]], recipe.recipe_id)
        review = next(item for item in reviews if item.featured_rank == case["featured_rank"])
        counters["labels"] += len(case["labels"])
        counters["outcome_labels"] += len(case["outcome_anchors"])
        try:
            if replay_report:
                stored = replay_cases[(recipe.recipe_id, review.featured_rank)]
                analysis = ReviewAnalysis(
                    review_id=review.review_id,
                    sentiment="replayed",
                    intents=stored["extracted_intents"],
                    outcome_claims=stored["outcome_claims"],
                )
                analysis, corrections = canonicalize_analysis_quotes(analysis, review.text)
                stats = ModelCallStats(
                    review_id=review.review_id,
                    model=stored["model"],
                    input_tokens=stored["input_tokens"],
                    output_tokens=stored["output_tokens"],
                    latency_seconds=stored["latency_seconds"],
                    canonicalized_source_quotes=corrections,
                )
            else:
                if extractor is None:  # pragma: no cover - narrowed by replay branch
                    raise RuntimeError("Live extractor was not initialized")
                call = extractor.analyze(review, recipe, lines)
                analysis = call.analysis
                stats = call.stats
        except Exception as exc:  # noqa: BLE001 - every failed case belongs in the report
            failures.append(
                {
                    "recipe_id": recipe.recipe_id,
                    "featured_rank": review.featured_rank,
                    "error": safe_exception_text(exc),
                }
            )
            continue

        total_latency_seconds += stats.latency_seconds
        counters["input_tokens"] += stats.input_tokens
        counters["output_tokens"] += stats.output_tokens
        counters["canonicalized_source_quotes"] += stats.canonicalized_source_quotes

        intent_matches, unmatched_intent_ids = match_intent_labels(case["labels"], analysis)
        matched_intents = [item for item in intent_matches if item["matched"]]
        counters["matched_labels"] += len(matched_intents)
        counters["correct_states"] += sum(item["state_correct"] for item in matched_intents)
        counters["correct_kinds"] += sum(item["kind_correct"] for item in matched_intents)
        counters["correct_actionability"] += sum(
            item["actionability_correct"] for item in matched_intents
        )
        counters["total_intents"] += len(analysis.intents)
        counters["grounded_quotes"] += sum(
            bool(intent.source_quote) and intent.source_quote in review.text
            for intent in analysis.intents
        )

        outcome_matches, unmatched_outcome_quotes = match_outcome_labels(
            case["outcome_anchors"], analysis
        )
        counters["matched_outcomes"] += sum(item["matched"] for item in outcome_matches)
        counters["total_claims"] += len(analysis.outcome_claims)
        counters["grounded_claim_quotes"] += sum(
            bool(claim.source_quote) and claim.source_quote in review.text
            for claim in analysis.outcome_claims
        )

        edit_score = score_expected_edits(case["expected_edits"], analysis)
        if edit_score["scored"]:
            counters["scored_bundles"] += 1
            counters["exact_bundles"] += int(edit_score["exact_bundle"])
            counters["expected_edits"] += edit_score["expected_count"]
            counters["actual_scored_edits"] += edit_score["actual_count"]
            matched_edits = [item for item in edit_score["matches"] if item["matched"]]
            counters["matched_edits"] += len(matched_edits)
            counters["correct_preconditions"] += sum(
                item["precondition_correct"] for item in matched_edits
            )
            counters["correct_outputs"] += sum(item["output_correct"] for item in matched_edits)

        decision, candidate = evaluate_review_bundle(recipe, lines, review, analysis)
        bundle_counts[decision.bundle_status.value] += 1
        candidate_count += int(candidate is not None)
        status_correct = decision.bundle_status.value == case["expected_bundle_status"]
        expected_candidate = case["expected_bundle_status"] == "applied"
        candidate_presence_correct = (candidate is not None) == expected_candidate
        counters["correct_bundle_statuses"] += int(status_correct)
        counters["correct_candidate_presence"] += int(candidate_presence_correct)
        cases.append(
            {
                "recipe_id": recipe.recipe_id,
                "featured_rank": review.featured_rank,
                "intent_labels": intent_matches,
                "unmatched_intent_ids": unmatched_intent_ids,
                "outcome_labels": outcome_matches,
                "unmatched_outcome_quotes": unmatched_outcome_quotes,
                "edit_score": edit_score,
                "extracted_intents": [
                    intent.model_dump(mode="json") for intent in analysis.intents
                ],
                "outcome_claims": [
                    claim.model_dump(mode="json") for claim in analysis.outcome_claims
                ],
                "model": stats.model,
                "input_tokens": stats.input_tokens,
                "output_tokens": stats.output_tokens,
                "latency_seconds": stats.latency_seconds,
                "expected_bundle_status": case["expected_bundle_status"],
                "actual_bundle_status": decision.bundle_status.value,
                "decision_reasons": decision.reasons,
                "bundle_status_correct": status_correct,
                "candidate_presence_correct": candidate_presence_correct,
            }
        )

    completed_cases = len(cases)
    metrics = {
        "labeled_intent_recall": _ratio(counters["matched_labels"], counters["labels"]),
        "labeled_intent_precision": _ratio(counters["matched_labels"], counters["total_intents"]),
        "evidence_state_accuracy_on_matched": _ratio(
            counters["correct_states"], counters["matched_labels"]
        ),
        "intent_kind_accuracy_on_matched": _ratio(
            counters["correct_kinds"], counters["matched_labels"]
        ),
        "actionability_accuracy_on_matched": _ratio(
            counters["correct_actionability"], counters["matched_labels"]
        ),
        "source_quote_grounding": _ratio(counters["grounded_quotes"], counters["total_intents"]),
        "outcome_label_recall": _ratio(counters["matched_outcomes"], counters["outcome_labels"]),
        "outcome_quote_grounding": _ratio(
            counters["grounded_claim_quotes"], counters["total_claims"], empty=1.0
        ),
        "bundle_status_accuracy": _ratio(counters["correct_bundle_statuses"], completed_cases),
        "candidate_presence_accuracy": _ratio(
            counters["correct_candidate_presence"], completed_cases
        ),
        "edit_target_operation_recall": _ratio(
            counters["matched_edits"], counters["expected_edits"]
        ),
        "edit_target_operation_precision": _ratio(
            counters["matched_edits"], counters["actual_scored_edits"]
        ),
        "edit_precondition_accuracy": _ratio(
            counters["correct_preconditions"], counters["matched_edits"]
        ),
        "edit_output_constraint_accuracy": _ratio(
            counters["correct_outputs"], counters["matched_edits"]
        ),
        "exact_applied_bundle_accuracy": _ratio(
            counters["exact_bundles"], counters["scored_bundles"]
        ),
        "labels": counters["labels"],
        "matched_labels": counters["matched_labels"],
        "outcome_labels": counters["outcome_labels"],
        "matched_outcomes": counters["matched_outcomes"],
        "expected_edits": counters["expected_edits"],
        "matched_edits": counters["matched_edits"],
        "failed_cases": len(failures),
        "input_tokens": counters["input_tokens"],
        "output_tokens": counters["output_tokens"],
        "canonicalized_source_quotes": counters["canonicalized_source_quotes"],
        "latency_seconds": round(total_latency_seconds, 3),
        "bundle_counts": bundle_counts,
        "candidates": candidate_count,
    }
    threshold_failures = [
        metric for metric, threshold in THRESHOLDS.items() if metrics[metric] < threshold
    ]
    report = {
        "metadata": {
            "evaluation_version": EVALUATION_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "requested_model": requested_model,
            "source_commit": os.getenv("GITHUB_SHA")
            or os.getenv("SOURCE_COMMIT")
            or "working-tree",
            "labels_sha256": sha256(label_path.read_bytes()).hexdigest(),
            "prompt_sha256": sha256(EXTRACTION_INSTRUCTIONS.encode("utf-8")).hexdigest(),
            "schema_sha256": _digest_json(ReviewAnalysis.model_json_schema()),
            "replayed_from_sha256": sha256(replay_path.read_bytes()).hexdigest()
            if replay_path
            else None,
            "thresholds": THRESHOLDS,
        },
        "metrics": metrics,
        "threshold_failures": threshold_failures,
        "cases": cases,
        "failures": failures,
    }
    destination = Path(args.output)
    atomic_write_text(destination, json.dumps(report, indent=2))
    print(json.dumps(metrics, indent=2))
    if failures or threshold_failures:
        print(json.dumps({"threshold_failures": threshold_failures}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
