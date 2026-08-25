"""Contracts shared by semantic extraction and deterministic execution."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .version import TRACE_VERSION

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
EvidenceText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]
LineText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Section(str, Enum):
    INGREDIENTS = "ingredients"
    INSTRUCTIONS = "instructions"


class EvidenceState(str, Enum):
    PERFORMED = "performed"
    RECOMMENDED = "recommended"
    FUTURE = "future"
    HYPOTHETICAL = "hypothetical"
    PREFERENCE = "preference"
    UNCLEAR = "unclear"


class Actionability(str, Enum):
    ACTIONABLE = "actionable"
    INCOMPLETE = "incomplete"
    NON_ACTIONABLE = "non_actionable"


class IntentKind(str, Enum):
    QUANTITY_ADJUSTMENT = "quantity_adjustment"
    INGREDIENT_SUBSTITUTION = "ingredient_substitution"
    INGREDIENT_ADDITION = "ingredient_addition"
    INGREDIENT_REMOVAL = "ingredient_removal"
    TECHNIQUE_CHANGE = "technique_change"
    EQUIPMENT_CHANGE = "equipment_change"
    SERVING_CHANGE = "serving_change"
    OTHER = "other"


class Operation(str, Enum):
    REPLACE_LINE = "replace_line"
    REMOVE_LINE = "remove_line"
    INSERT_AFTER = "insert_after"


class DecisionStatus(str, Enum):
    APPLIED = "applied"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLIED = "not_applied"


class Recipe(StrictModel):
    recipe_id: Identifier
    title: ShortText
    ingredients: list[LineText] = Field(min_length=1, max_length=500)
    instructions: list[LineText] = Field(min_length=1, max_length=500)
    description: EvidenceText | None = None
    servings: str | int | None = None
    rating: dict[str, Any] | None = None


class RecipeLine(StrictModel):
    line_id: Identifier
    section: Section
    position: int = Field(ge=0)
    text: LineText


class ReviewEvidence(StrictModel):
    review_id: Identifier
    text: EvidenceText
    rating: int | None = Field(default=None, ge=1, le=5)
    featured_rank: int = Field(ge=0)


class OutcomeClaim(StrictModel):
    source_quote: EvidenceText = Field(description="Exact quote from the review")
    claim: EvidenceText
    supports_intent_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class RecipeEdit(StrictModel):
    """One exact line mutation; an intent may require several coordinated edits."""

    edit_id: Identifier
    target_line_id: Identifier
    operation: Operation
    expected_text: LineText = Field(description="Exact full text of the target recipe line")
    replacement_text: LineText | None = Field(
        default=None, description="Complete new line for replace_line"
    )
    insert_text: LineText | None = Field(
        default=None, description="Complete new line for insert_after"
    )


class ModificationIntent(StrictModel):
    """One semantic tweak; executable fields may be null when evidence is incomplete."""

    intent_id: Identifier
    kind: IntentKind
    source_quote: EvidenceText = Field(
        description="Shortest exact review quote supporting this intent"
    )
    evidence_state: EvidenceState
    actionability: Actionability
    edits: list[RecipeEdit] = Field(default_factory=list)
    rationale: EvidenceText | None = None
    missing_information: list[ShortText] = Field(default_factory=list, max_length=100)
    requires_intent_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class ReviewAnalysis(StrictModel):
    review_id: Identifier
    sentiment: ShortText
    intents: list[ModificationIntent] = Field(max_length=100)
    outcome_claims: list[OutcomeClaim] = Field(default_factory=list, max_length=100)


class ChangeRecord(StrictModel):
    intent_id: Identifier
    edit_id: Identifier
    line_id: Identifier
    section: Section
    operation: Operation
    before: LineText | None
    after: LineText | None
    source_quote: EvidenceText


class IntentDecision(StrictModel):
    intent_id: Identifier
    status: DecisionStatus
    reasons: list[str] = Field(default_factory=list)


class ReviewDecision(StrictModel):
    review_id: Identifier
    bundle_status: DecisionStatus
    intent_decisions: list[IntentDecision]
    reasons: list[str] = Field(default_factory=list)


class CandidateRecipe(StrictModel):
    candidate_id: Identifier
    source_review_id: Identifier
    relationship: Literal["alternative"] = "alternative"
    recipe: Recipe
    changes: list[ChangeRecord]


class ModelCallStats(StrictModel):
    review_id: Identifier
    model: ShortText
    response_id: str | None = None
    status: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_seconds: float = Field(default=0.0, ge=0)
    canonicalized_source_quotes: int = Field(default=0, ge=0)


class TraceRequest(StrictModel):
    """Serializable API boundary; credentials and HTTP headers are never included."""

    endpoint: str = "responses.parse"
    model: str
    instructions: str
    input: str
    text_format: str
    output_schema: dict[str, Any]
    reasoning: dict[str, str]
    max_output_tokens: int
    tools: list[dict[str, Any]] = Field(default_factory=list)
    parallel_tool_calls: bool
    store: bool


class TraceResponse(StrictModel):
    response_id: str | None = None
    model: str
    status: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float
    output_text: str | None = None
    output_parsed: ReviewAnalysis | None = None


class ModelCallTrace(StrictModel):
    review_id: Identifier
    request: TraceRequest
    response: TraceResponse | None = None
    error: str | None = None


class ExtractionCall(StrictModel):
    analysis: ReviewAnalysis
    stats: ModelCallStats
    trace: ModelCallTrace | None = None


class PolicyTrace(StrictModel):
    review_id: Identifier
    decision: ReviewDecision
    candidate: CandidateRecipe | None = None


class RunTrace(StrictModel):
    trace_version: str = TRACE_VERSION
    recipe_id: Identifier
    model_calls: list[ModelCallTrace]
    policy_decisions: list[PolicyTrace]
    extraction_failures: dict[str, str] = Field(default_factory=dict)
    disclosure: list[str] = Field(
        default_factory=lambda: [
            "Trace contains the exact application-level API request, not credentials or headers.",
            "Trace contains recipe and review content; treat it as potentially sensitive data.",
            "OpenAI internal reasoning is not returned by the API and is not represented here.",
        ]
    )


class PipelineResult(StrictModel):
    pipeline_version: str
    original_recipe: Recipe
    featured_reviews: list[ReviewEvidence]
    analyses: list[ReviewAnalysis]
    decisions: list[ReviewDecision]
    candidates: list[CandidateRecipe]
    extraction_failures: dict[str, str] = Field(default_factory=dict)
    model_calls: list[ModelCallStats] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DirectoryRun(StrictModel):
    results: list[PipelineResult]
    file_failures: dict[str, str] = Field(default_factory=dict)
