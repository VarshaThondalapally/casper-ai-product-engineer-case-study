import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_pipeline.models import Actionability, EvidenceState, IntentKind, ModificationIntent
from llm_pipeline.normalization import normalize_featured_reviews, parse_recipe

DATA = Path(__file__).parents[1] / "data"


def test_featured_tweaks_are_authoritative_and_exact_duplicates_are_removed():
    raw = {
        "recipe_id": 7,
        "featured_tweaks": [
            {"text": "Used more ginger.", "rating": 5},
            {"text": "Used more ginger.", "rating": 4},
        ],
        "reviews": [{"text": "This non-featured review must not enter the pipeline."}],
    }

    reviews = normalize_featured_reviews(raw, "7")

    assert [review.text for review in reviews] == ["Used more ginger."]
    assert reviews[0].review_id == "7:featured:0"


def test_supplied_corpus_contains_twelve_featured_reviews_across_four_recipes():
    counts = []
    for path in DATA.glob("recipe_*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        recipe = parse_recipe(raw)
        counts.append(len(normalize_featured_reviews(raw, recipe.recipe_id)))

    assert sorted(counts) == [0, 0, 1, 2, 4, 5]
    assert sum(counts) == 12


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"title": "Soup", "ingredients": ["broth"], "instructions": ["Heat."]}, "recipe_id"),
        ({"recipe_id": "r1", "ingredients": ["broth"], "instructions": ["Heat."]}, "title"),
        (
            {"recipe_id": "r1", "title": "Soup", "ingredients": [], "instructions": ["Heat."]},
            "ingredients",
        ),
        (
            {
                "recipe_id": "r1",
                "title": "Soup",
                "ingredients": [1],
                "instructions": ["Heat."],
            },
            "ingredients",
        ),
    ],
)
def test_malformed_recipe_data_is_rejected_instead_of_defaulted(raw, message):
    with pytest.raises((TypeError, ValueError), match=message):
        parse_recipe(raw)


def test_malformed_featured_review_is_rejected():
    with pytest.raises((TypeError, ValueError), match="featured_tweaks"):
        normalize_featured_reviews({"featured_tweaks": "not-a-list"}, "r1")


def test_empty_featured_review_is_rejected_instead_of_silently_skipped():
    with pytest.raises(ValueError, match="non-empty text"):
        normalize_featured_reviews({"featured_tweaks": [{"text": "   "}]}, "r1")


def test_featured_review_count_is_bounded_before_model_calls():
    featured = [{"text": f"Review {index}"} for index in range(21)]

    with pytest.raises(ValueError, match="at most 20"):
        normalize_featured_reviews({"featured_tweaks": featured}, "r1")


def test_empty_source_quote_is_rejected_by_the_structured_contract():
    with pytest.raises(ValidationError):
        ModificationIntent(
            intent_id="salt",
            kind=IntentKind.QUANTITY_ADJUSTMENT,
            source_quote="   ",
            evidence_state=EvidenceState.PERFORMED,
            actionability=Actionability.INCOMPLETE,
        )
