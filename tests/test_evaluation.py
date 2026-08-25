import json
from pathlib import Path

from llm_pipeline.evaluation import (
    evidence_anchor_matches,
    match_intent_labels,
    normalize_text,
    score_expected_edits,
)
from llm_pipeline.models import (
    Actionability,
    EvidenceState,
    IntentKind,
    ModificationIntent,
    Operation,
    RecipeEdit,
    ReviewAnalysis,
)
from llm_pipeline.normalization import build_recipe_lines, normalize_featured_reviews, parse_recipe


def analysis() -> ReviewAnalysis:
    return ReviewAnalysis(
        review_id="r1:featured:0",
        sentiment="positive",
        intents=[
            ModificationIntent(
                intent_id="salt",
                kind=IntentKind.QUANTITY_ADJUSTMENT,
                source_quote="I used one teaspoon of salt",
                evidence_state=EvidenceState.PERFORMED,
                actionability=Actionability.ACTIONABLE,
                edits=[
                    RecipeEdit(
                        edit_id="salt-edit",
                        target_line_id="ingredient:0",
                        operation=Operation.REPLACE_LINE,
                        expected_text="0.5 teaspoon salt",
                        replacement_text="1 teaspoon salt",
                    )
                ],
            )
        ],
    )


def test_label_match_requires_the_complete_anchor_not_a_short_model_fragment():
    assert evidence_anchor_matches("one teaspoon of salt", "I used one teaspoon of salt")
    assert not evidence_anchor_matches("one teaspoon of salt", "salt")


def test_intent_scoring_checks_state_kind_and_actionability():
    matches, unmatched = match_intent_labels(
        [
            {
                "anchor": "one teaspoon of salt",
                "state": "performed",
                "kind": "quantity_adjustment",
                "actionability": "actionable",
            }
        ],
        analysis(),
    )

    assert unmatched == []
    assert matches[0]["matched"] is True
    assert matches[0]["state_correct"] is True
    assert matches[0]["kind_correct"] is True
    assert matches[0]["actionability_correct"] is True


def test_field_level_edit_scoring_checks_target_precondition_and_output():
    score = score_expected_edits(
        [
            {
                "target_line_id": "ingredient:0",
                "operation": "replace_line",
                "expected_text": "0.5 teaspoon salt",
                "output_contains": ["1 teaspoon", "salt"],
            }
        ],
        analysis(),
    )

    assert score["exact_bundle"] is True
    assert score["matches"][0]["precondition_correct"] is True
    assert score["matches"][0]["output_correct"] is True


def test_extra_edit_prevents_an_exact_bundle_pass():
    value = analysis()
    value.intents[0].edits.append(
        RecipeEdit(
            edit_id="extra",
            target_line_id="instruction:0",
            operation=Operation.INSERT_AFTER,
            expected_text="Mix.",
            insert_text="Serve.",
        )
    )

    score = score_expected_edits(
        [
            {
                "target_line_id": "ingredient:0",
                "operation": "replace_line",
                "expected_text": "0.5 teaspoon salt",
                "output_contains": ["1 teaspoon"],
            }
        ],
        value,
    )

    assert score["exact_bundle"] is False
    assert score["unmatched_actual_edit_ids"] == ["extra"]


def test_equivalent_golden_targets_and_output_terms_are_explicitly_allowed():
    score = score_expected_edits(
        [
            {
                "target_line_id": "ingredient:1",
                "accepted_target_line_ids": ["ingredient:0", "ingredient:1"],
                "operation": "replace_line",
                "expected_text": "one-half teaspoon salt",
                "accepted_expected_texts": [
                    "one-half teaspoon salt",
                    "0.5 teaspoon salt",
                ],
                "output_contains": ["salt"],
                "output_any_contains": [["1 teaspoon", "one teaspoon"]],
            }
        ],
        analysis(),
    )

    assert score["exact_bundle"] is True


def test_golden_labels_are_complete_valid_and_grounded_in_the_supplied_corpus():
    project_root = Path(__file__).parents[1]
    cases = json.loads(
        (project_root / "evaluation" / "golden_labels.json").read_text(encoding="utf-8")
    )
    source_cases = {}
    for path in (project_root / "data").glob("recipe_*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        recipe = parse_recipe(raw)
        lines = build_recipe_lines(recipe)
        for review in normalize_featured_reviews(raw, recipe.recipe_id):
            source_cases[(recipe.recipe_id, review.featured_rank)] = (review, lines)

    case_keys = {(case["recipe_id"], case["featured_rank"]) for case in cases}
    assert len(case_keys) == len(cases) == 12
    assert case_keys == set(source_cases)

    valid_states = {state.value for state in EvidenceState}
    valid_kinds = {kind.value for kind in IntentKind}
    valid_actions = {action.value for action in Actionability}
    valid_operations = {operation.value for operation in Operation}
    for case in cases:
        review, lines = source_cases[(case["recipe_id"], case["featured_rank"])]
        line_ids = {line.line_id for line in lines}
        line_texts = {line.text for line in lines}
        assert case["expected_bundle_status"] in {"applied", "needs_review", "not_applied"}
        assert bool(case["expected_edits"]) is (case["expected_bundle_status"] == "applied")
        assert len({label["anchor"] for label in case["labels"]}) == len(case["labels"])
        for label in case["labels"]:
            assert normalize_text(label["anchor"]) in normalize_text(review.text)
            assert label["state"] in valid_states
            assert label["kind"] in valid_kinds
            assert set(label.get("accepted_kinds", [label["kind"]])) <= valid_kinds
            assert label["actionability"] in valid_actions
        for anchor in case["outcome_anchors"]:
            assert normalize_text(anchor) in normalize_text(review.text)
        for edit in case["expected_edits"]:
            assert edit["target_line_id"] in line_ids
            assert set(edit.get("accepted_target_line_ids", [edit["target_line_id"]])) <= line_ids
            assert edit["operation"] in valid_operations
            assert edit["expected_text"] in line_texts
            assert set(edit.get("accepted_expected_texts", [edit["expected_text"]])) <= line_texts
            assert "output_contains" in edit
