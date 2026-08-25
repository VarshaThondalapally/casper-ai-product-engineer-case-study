"""Normalize scraped data without widening the assignment's evidence source."""

from __future__ import annotations

from typing import Any

from .models import Recipe, RecipeLine, ReviewEvidence, Section

MAX_RECIPE_ID_CHARS = 100
MAX_FEATURED_REVIEWS = 20


def _required_line_list(raw: dict[str, Any], field: str) -> list[str]:
    value = raw.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Recipe field '{field}' must be a non-empty list")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"Recipe field '{field}' must contain only strings")
    return value


def parse_recipe(raw: dict[str, Any]) -> Recipe:
    if not isinstance(raw, dict):
        raise TypeError("Recipe input must be a JSON object")
    raw_recipe_id = raw.get("recipe_id")
    if isinstance(raw_recipe_id, bool) or not isinstance(raw_recipe_id, (str, int)):
        raise TypeError("Recipe field 'recipe_id' must be a string or integer")
    recipe_id = str(raw_recipe_id).strip()
    if not recipe_id or len(recipe_id) > MAX_RECIPE_ID_CHARS:
        raise ValueError(
            f"Recipe field 'recipe_id' must contain 1-{MAX_RECIPE_ID_CHARS} characters"
        )
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Recipe field 'title' must be a non-empty string")
    description = raw.get("description")
    if description == "":
        description = None
    return Recipe(
        recipe_id=recipe_id,
        title=title,
        ingredients=_required_line_list(raw, "ingredients"),
        instructions=_required_line_list(raw, "instructions"),
        description=description,
        servings=raw.get("servings"),
        rating=raw.get("rating"),
    )


def build_recipe_lines(recipe: Recipe) -> list[RecipeLine]:
    return [
        *[
            RecipeLine(
                line_id=f"ingredient:{position}",
                section=Section.INGREDIENTS,
                position=position,
                text=text,
            )
            for position, text in enumerate(recipe.ingredients)
        ],
        *[
            RecipeLine(
                line_id=f"instruction:{position}",
                section=Section.INSTRUCTIONS,
                position=position,
                text=text,
            )
            for position, text in enumerate(recipe.instructions)
        ],
    ]


def normalize_featured_reviews(raw: dict[str, Any], recipe_id: str) -> list[ReviewEvidence]:
    """Use featured_tweaks as authoritative and remove only exact duplicates."""
    reviews: list[ReviewEvidence] = []
    seen: set[str] = set()
    featured = raw.get("featured_tweaks")
    if featured is None:
        return reviews
    if not isinstance(featured, list):
        raise TypeError("Recipe field 'featured_tweaks' must be a list when present")
    if len(featured) > MAX_FEATURED_REVIEWS:
        raise ValueError(f"A recipe may contain at most {MAX_FEATURED_REVIEWS} featured tweaks")
    for rank, item in enumerate(featured):
        if not isinstance(item, dict):
            raise TypeError(f"Featured tweak at index {rank} must be an object")
        raw_text = item.get("text")
        if not isinstance(raw_text, str):
            raise TypeError(f"Featured tweak at index {rank} must contain string field 'text'")
        text = raw_text.strip()
        if not text:
            raise ValueError(f"Featured tweak at index {rank} must contain non-empty text")
        if text in seen:
            continue
        seen.add(text)
        reviews.append(
            ReviewEvidence(
                review_id=f"{recipe_id}:featured:{rank}",
                text=text,
                rating=item.get("rating"),
                featured_rank=rank,
            )
        )
    return reviews
