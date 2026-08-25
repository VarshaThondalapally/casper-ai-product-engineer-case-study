"""Pure scoring helpers for the hand-labeled live evaluation."""

from __future__ import annotations

from typing import Any

from .models import EvidenceState, RecipeEdit, ReviewAnalysis


def normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def evidence_anchor_matches(anchor: str, source_quote: str) -> bool:
    """Require the complete labeled anchor; a shorter model quote cannot get credit."""
    return normalize_text(anchor) in normalize_text(source_quote)


def match_intent_labels(
    labels: list[dict[str, Any]], analysis: ReviewAnalysis
) -> tuple[list[dict[str, Any]], list[str]]:
    unmatched = list(analysis.intents)
    matches: list[dict[str, Any]] = []
    for label in labels:
        match = next(
            (
                intent
                for intent in unmatched
                if evidence_anchor_matches(label["anchor"], intent.source_quote)
            ),
            None,
        )
        if match is None:
            matches.append({"label": label, "matched": False})
            continue
        unmatched.remove(match)
        accepted_kinds = label.get("accepted_kinds", [label["kind"]])
        matches.append(
            {
                "label": label,
                "matched": True,
                "intent_id": match.intent_id,
                "state_correct": match.evidence_state.value == label["state"],
                "kind_correct": match.kind.value in accepted_kinds,
                "actionability_correct": (match.actionability.value == label["actionability"]),
            }
        )
    return matches, [intent.intent_id for intent in unmatched]


def match_outcome_labels(
    anchors: list[str], analysis: ReviewAnalysis
) -> tuple[list[dict[str, Any]], list[str]]:
    unmatched = list(analysis.outcome_claims)
    matches: list[dict[str, Any]] = []
    for anchor in anchors:
        match = next(
            (claim for claim in unmatched if evidence_anchor_matches(anchor, claim.source_quote)),
            None,
        )
        if match is None:
            matches.append({"anchor": anchor, "matched": False})
            continue
        unmatched.remove(match)
        matches.append({"anchor": anchor, "matched": True, "claim": match.claim})
    return matches, [claim.source_quote for claim in unmatched]


def _output_text(edit: RecipeEdit) -> str:
    return edit.replacement_text or edit.insert_text or ""


def score_expected_edits(
    expected: list[dict[str, Any]], analysis: ReviewAnalysis
) -> dict[str, Any]:
    actual = [
        edit
        for intent in analysis.intents
        if intent.evidence_state is EvidenceState.PERFORMED
        for edit in intent.edits
    ]
    if not expected:
        return {
            "scored": False,
            "expected_count": 0,
            "actual_count": len(actual),
            "matches": [],
            "exact_bundle": None,
        }

    unmatched = list(actual)
    matches: list[dict[str, Any]] = []
    for label in expected:
        accepted_targets = label.get("accepted_target_line_ids", [label["target_line_id"]])
        match = next(
            (
                edit
                for edit in unmatched
                if edit.target_line_id in accepted_targets
                and edit.operation.value == label["operation"]
            ),
            None,
        )
        if match is None:
            matches.append({"label": label, "matched": False})
            continue
        unmatched.remove(match)
        output = normalize_text(_output_text(match))
        output_correct = all(
            normalize_text(required) in output for required in label["output_contains"]
        )
        output_correct = output_correct and all(
            any(normalize_text(option) in output for option in option_group)
            for option_group in label.get("output_any_contains", [])
        )
        matches.append(
            {
                "label": label,
                "matched": True,
                "edit_id": match.edit_id,
                "precondition_correct": match.expected_text
                in label.get("accepted_expected_texts", [label["expected_text"]]),
                "output_correct": output_correct,
            }
        )

    exact_bundle = len(actual) == len(expected) and all(
        item["matched"] and item["precondition_correct"] and item["output_correct"]
        for item in matches
    )
    return {
        "scored": True,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "matches": matches,
        "unmatched_actual_edit_ids": [edit.edit_id for edit in unmatched],
        "exact_bundle": exact_bundle,
    }
