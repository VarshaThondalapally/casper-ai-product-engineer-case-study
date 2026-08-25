import json

from llm_pipeline.pipeline import LLMAnalysisPipeline
from run_pipeline import main


class FailingExtractor:
    def analyze(self, review, recipe, recipe_lines, *, capture_trace=False):
        raise RuntimeError("simulated extraction failure")


def recipe_payload(featured=True):
    return {
        "recipe_id": "r1",
        "title": "Soup",
        "ingredients": ["1 cup broth"],
        "instructions": ["Heat the broth."],
        "featured_tweaks": [{"text": "I used more broth."}] if featured else [],
    }


def test_cli_returns_nonzero_when_any_extraction_fails(tmp_path, capsys):
    source = tmp_path / "recipe_failure.json"
    source.write_text(json.dumps(recipe_payload()), encoding="utf-8")
    pipeline = LLMAnalysisPipeline(extractor=FailingExtractor())

    status = main([str(source), "--output", str(tmp_path / "outputs")], pipeline=pipeline)

    assert status == 1
    assert "failures=1" in capsys.readouterr().out


def test_cli_returns_fatal_status_for_invalid_json(tmp_path, capsys):
    source = tmp_path / "recipe_invalid.json"
    source.write_text("{not json", encoding="utf-8")

    status = main([str(source), "--output", str(tmp_path / "outputs")])

    assert status == 2
    assert "JSONDecodeError" in capsys.readouterr().err


def test_cli_zero_tweak_recipe_succeeds_without_an_api_call(tmp_path):
    source = tmp_path / "recipe_no_tweaks.json"
    source.write_text(json.dumps(recipe_payload(featured=False)), encoding="utf-8")

    status = main([str(source), "--output", str(tmp_path / "outputs")])

    assert status == 0
    assert (tmp_path / "outputs" / "r1" / "result.json").exists()
