"""Evidence-safe source-span canonicalization."""

from __future__ import annotations

import re

from .models import ReviewAnalysis


def _unique_case_only_span(quote: str, source: str) -> str | None:
    if quote in source:
        return quote
    matches = list(re.finditer(re.escape(quote), source, flags=re.IGNORECASE))
    if len(matches) != 1:
        return None
    match = matches[0]
    exact_span = source[match.start() : match.end()]
    if exact_span.casefold() != quote.casefold():
        return None
    return exact_span


def canonicalize_analysis_quotes(
    analysis: ReviewAnalysis, source_review: str
) -> tuple[ReviewAnalysis, int]:
    """Copy unique case-only quote matches from source; never paraphrase evidence."""
    canonical = analysis.model_copy(deep=True)
    corrections = 0
    for intent in canonical.intents:
        exact = _unique_case_only_span(intent.source_quote, source_review)
        if exact is not None and exact != intent.source_quote:
            intent.source_quote = exact
            corrections += 1
    for claim in canonical.outcome_claims:
        exact = _unique_case_only_span(claim.source_quote, source_review)
        if exact is not None and exact != claim.source_quote:
            claim.source_quote = exact
            corrections += 1
    return canonical, corrections
