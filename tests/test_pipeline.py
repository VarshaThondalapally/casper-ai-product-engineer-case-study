import json
import tomllib
from pathlib import Path

from llm_pipeline.models import (
    Actionability,
    EvidenceState,
    ExtractionCall,
    IntentKind,
    ModelCallStats,
    ModificationIntent,
    Operation,
    RecipeEdit,
    ReviewAnalysis,
)
from llm_pipeline.pipeline import LLMAnalysisPipeline, safe_output_component
from llm_pipeline.version import PIPELINE_VERSION, TRACE_VERSION


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, review, recipe, recipe_lines, *, capture_trace=False):
        self.calls += 1
        target = recipe_lines[0]
        analysis = ReviewAnalysis(
            review_id=review.review_id,
            sentiment="positive",
            intents=[
                ModificationIntent(
                    intent_id=f"change-{review.featured_rank}",
                    kind=IntentKind.QUANTITY_ADJUSTMENT,
                    source_quote=review.text,
                    evidence_state=EvidenceState.PERFORMED,
                    actionability=Actionability.ACTIONABLE,
                    edits=[
                        RecipeEdit(
                            edit_id=f"edit-{review.featured_rank}",
                            target_line_id=target.line_id,
                            operation=Operation.REPLACE_LINE,
                            expected_text=target.text,
                            replacement_text=f"{target.text} (variant {review.featured_rank})",
                        )
                    ],
                )
            ],
        )
        return ExtractionCall(
            analysis=analysis,
            stats=ModelCallStats(review_id=review.review_id, model="fake"),
        )


class WrongReviewExtractor(FakeExtractor):
    def analyze(self, review, recipe, recipe_lines, *, capture_trace=False):
        call = super().analyze(review, recipe, recipe_lines, capture_trace=capture_trace)
        call.analysis.review_id = "wrong:featured:0"
        return call


def raw(featured):
    return {
        "recipe_id": "r1",
        "title": "Soup",
        "ingredients": ["1 cup broth"],
        "instructions": ["Heat the broth."],
        "featured_tweaks": featured,
    }


def test_each_featured_review_becomes_an_independent_alternative():
    extractor = FakeExtractor()
    result = LLMAnalysisPipeline(extractor=extractor).process_recipe_data(
        raw([{"text": "I used more broth."}, {"text": "I used less broth."}])
    )

    assert extractor.calls == 2
    assert len(result.candidates) == 2
    assert result.candidates[0].recipe.ingredients == ["1 cup broth (variant 0)"]
    assert result.candidates[1].recipe.ingredients == ["1 cup broth (variant 1)"]
    assert result.original_recipe.ingredients == ["1 cup broth"]
    assert {candidate.relationship for candidate in result.candidates} == {"alternative"}


def test_no_featured_tweaks_means_no_model_call_and_an_explicit_result():
    extractor = FakeExtractor()
    result = LLMAnalysisPipeline(extractor=extractor).process_recipe_data(raw([]))

    assert extractor.calls == 0
    assert result.candidates == []
    assert result.analyses == []
    assert "This recipe has no featured tweaks, so no model call was made." in result.limitations


def test_recipe_identifier_cannot_escape_output_directory(tmp_path):
    pipeline = LLMAnalysisPipeline(extractor=FakeExtractor())
    output_root = tmp_path / "outputs"

    result_path = pipeline.result_output_path(output_root, "../../outside")

    assert result_path.parent.parent == output_root.resolve()
    assert ".." not in result_path.parent.name


def test_output_components_handle_windows_reserved_and_case_colliding_ids():
    assert safe_output_component("10813") == "10813"
    assert safe_output_component("CON") != "CON"
    assert safe_output_component("Recipe") != safe_output_component("recipe")


def test_directory_run_isolates_a_malformed_recipe_file(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "recipe_bad.json").write_text("{not json", encoding="utf-8")
    (data_dir / "recipe_good.json").write_text(json.dumps(raw([])), encoding="utf-8")
    pipeline = LLMAnalysisPipeline(extractor=FakeExtractor())

    run = pipeline.process_directory(data_dir, tmp_path / "outputs")

    assert len(run.results) == 1
    assert set(run.file_failures) == {"recipe_bad.json"}
    assert (tmp_path / "outputs" / "r1" / "result.json").exists()


def test_empty_recipe_directory_is_an_explicit_failure(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    pipeline = LLMAnalysisPipeline(extractor=FakeExtractor())

    try:
        pipeline.process_directory(data_dir, tmp_path / "outputs")
    except ValueError as exc:
        assert "No recipe_*.json files" in str(exc)
    else:  # pragma: no cover - makes the intended failure explicit
        raise AssertionError("Expected empty directory to fail")


def test_mismatched_review_identity_is_an_extraction_failure():
    result = LLMAnalysisPipeline(extractor=WrongReviewExtractor()).process_recipe_data(
        raw([{"text": "I used more broth."}])
    )

    assert result.candidates == []
    assert list(result.extraction_failures) == ["r1:featured:0"]
    assert "mismatched review_id" in next(iter(result.extraction_failures.values()))


def test_all_six_supplied_recipes_run_through_the_orchestrator(tmp_path):
    data_dir = Path(__file__).parents[1] / "data"
    extractor = FakeExtractor()

    run = LLMAnalysisPipeline(extractor=extractor).process_directory(data_dir, tmp_path / "outputs")

    assert len(run.results) == 6
    assert run.file_failures == {}
    assert extractor.calls == 12
    assert sum(len(result.candidates) for result in run.results) == 12


def test_release_versions_stay_synchronized():
    project_root = Path(__file__).parents[1]
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    benchmark = json.loads(
        (project_root / "evaluation" / "benchmark_summary.json").read_text(encoding="utf-8")
    )

    assert project["project"]["version"] == PIPELINE_VERSION
    assert benchmark["metadata"]["pipeline_version"] == PIPELINE_VERSION
    assert TRACE_VERSION == PIPELINE_VERSION
