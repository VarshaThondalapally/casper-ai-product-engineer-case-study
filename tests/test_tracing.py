import json
from types import SimpleNamespace

from llm_pipeline.models import (
    Actionability,
    EvidenceState,
    IntentKind,
    ModificationIntent,
    Operation,
    RecipeEdit,
    ReviewAnalysis,
)
from llm_pipeline.pipeline import LLMAnalysisPipeline
from llm_pipeline.tweak_extractor import TweakExtractor


class FakeResponses:
    def __init__(self, analysis: ReviewAnalysis) -> None:
        self.analysis = analysis
        self.arguments = None

    def parse(self, **kwargs):
        self.arguments = kwargs
        return SimpleNamespace(
            id="resp_test_123",
            model="test-model-2026-08-25",
            status="completed",
            output_parsed=self.analysis,
            output_text=self.analysis.model_dump_json(),
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )


class FakeClient:
    def __init__(self, analysis: ReviewAnalysis) -> None:
        self.responses = FakeResponses(analysis)


class FailingResponses:
    def parse(self, **kwargs):
        raise RuntimeError(
            "request failed with sk-secret-value-123456 and Bearer token.value.secret"
        )


class FailingClient:
    responses = FailingResponses()


def test_trace_records_api_boundary_policy_and_storage_without_secrets(tmp_path):
    review_id = "r1:featured:0"
    review_text = "I used 2 cups broth."
    analysis = ReviewAnalysis(
        review_id=review_id,
        sentiment="positive",
        intents=[
            ModificationIntent(
                intent_id="broth",
                kind=IntentKind.QUANTITY_ADJUSTMENT,
                source_quote=review_text,
                evidence_state=EvidenceState.PERFORMED,
                actionability=Actionability.ACTIONABLE,
                edits=[
                    RecipeEdit(
                        edit_id="broth-edit",
                        target_line_id="ingredient:0",
                        operation=Operation.REPLACE_LINE,
                        expected_text="1 cup broth",
                        replacement_text="2 cups broth",
                    )
                ],
            )
        ],
    )
    fake_client = FakeClient(analysis)
    extractor = TweakExtractor(
        api_key="TEST_SECRET_MUST_NOT_APPEAR",
        model="test-model",
        client=fake_client,
    )
    pipeline = LLMAnalysisPipeline(extractor=extractor)
    raw = {
        "recipe_id": "r1",
        "title": "Soup",
        "ingredients": ["1 cup broth"],
        "instructions": ["Heat the broth."],
        "featured_tweaks": [{"text": review_text, "rating": 5}],
    }

    result, trace = pipeline.process_recipe_data_with_trace(raw)
    result_path = pipeline.save_result(result, tmp_path / "result.json")
    summary_path = pipeline.save_trace(trace, tmp_path / "trace", result_path)

    assert result.pipeline_version == "1.0.0"
    assert len(result.candidates) == 1
    assert result.model_calls[0].response_id == "resp_test_123"
    assert result.model_calls[0].latency_seconds >= 0
    assert summary_path.name == "run-summary.json"
    summary = json.loads(summary_path.read_text())
    assert summary["result_path"] == "..\\result.json" or summary["result_path"] == "../result.json"
    assert summary["trace_path"] == "."
    assert any("potentially sensitive" in item for item in summary["disclosure"])

    request = json.loads((tmp_path / "trace" / "review-0-request.json").read_text())
    assert request["endpoint"] == "responses.parse"
    assert request["model"] == "test-model"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "medium"}
    assert request["text_format"] == "ReviewAnalysis"
    assert request["output_schema"]["title"] == "ReviewAnalysis"
    assert json.loads(request["input"])["review"]["text"] == review_text

    response = json.loads((tmp_path / "trace" / "review-0-response.json").read_text())
    assert response["response"]["response_id"] == "resp_test_123"
    assert response["response"]["status"] == "completed"
    assert response["response"]["output_parsed"]["review_id"] == review_id

    policy = json.loads((tmp_path / "trace" / "review-0-policy.json").read_text())
    assert policy["decision"]["bundle_status"] == "applied"
    assert policy["candidate"]["changes"][0]["after"] == "2 cups broth"

    trace_text = "\n".join(path.read_text() for path in (tmp_path / "trace").glob("*.json"))
    assert "TEST_SECRET_MUST_NOT_APPEAR" not in trace_text
    assert "authorization" not in trace_text.casefold()
    assert "internal reasoning" in trace_text


def test_failed_trace_redacts_key_and_bearer_shaped_secrets(tmp_path):
    extractor = TweakExtractor(model="test-model", client=FailingClient())
    pipeline = LLMAnalysisPipeline(extractor=extractor)
    raw = {
        "recipe_id": "r1",
        "title": "Soup",
        "ingredients": ["1 cup broth"],
        "instructions": ["Heat the broth."],
        "featured_tweaks": [{"text": "I used more broth.", "rating": 5}],
    }

    result, trace = pipeline.process_recipe_data_with_trace(raw)
    result_path = pipeline.save_result(result, tmp_path / "result.json")
    pipeline.save_trace(trace, tmp_path / "trace", result_path)
    trace_text = "\n".join(path.read_text() for path in (tmp_path / "trace").glob("*.json"))

    assert result.extraction_failures
    assert "sk-secret-value" not in trace_text
    assert "token.value.secret" not in trace_text
    assert "REDACTED" in trace_text
