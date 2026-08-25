from llm_pipeline.grounding import canonicalize_analysis_quotes
from llm_pipeline.models import ReviewAnalysis


def analysis_with_quote(quote: str) -> ReviewAnalysis:
    return ReviewAnalysis(
        review_id="r1:featured:0",
        sentiment="positive",
        intents=[],
        outcome_claims=[
            {
                "source_quote": quote,
                "claim": "Flavor outcome",
                "supports_intent_ids": [],
            }
        ],
    )


def test_unique_case_only_quote_is_copied_exactly_from_source():
    analysis, corrections = canonicalize_analysis_quotes(
        analysis_with_quote("The flavor falls flat."),
        "But the flavor falls flat.",
    )

    assert analysis.outcome_claims[0].source_quote == "the flavor falls flat."
    assert corrections == 1


def test_ambiguous_case_only_quote_is_not_canonicalized():
    analysis, corrections = canonicalize_analysis_quotes(
        analysis_with_quote("GOOD"),
        "Good texture and good flavor.",
    )

    assert analysis.outcome_claims[0].source_quote == "GOOD"
    assert corrections == 0


def test_whitespace_or_paraphrase_difference_is_not_canonicalized():
    analysis, corrections = canonicalize_analysis_quotes(
        analysis_with_quote("the flavor was flat"),
        "The flavor   was flat.",
    )

    assert analysis.outcome_claims[0].source_quote == "the flavor was flat"
    assert corrections == 0
