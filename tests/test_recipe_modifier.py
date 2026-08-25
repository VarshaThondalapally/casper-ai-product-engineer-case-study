from llm_pipeline.models import (
    Actionability,
    DecisionStatus,
    EvidenceState,
    IntentKind,
    ModificationIntent,
    Operation,
    OutcomeClaim,
    Recipe,
    RecipeEdit,
    ReviewAnalysis,
    ReviewEvidence,
)
from llm_pipeline.normalization import build_recipe_lines
from llm_pipeline.recipe_modifier import evaluate_review_bundle


def recipe() -> Recipe:
    return Recipe(
        recipe_id="cookie",
        title="Cookies",
        ingredients=["2 eggs", "0.5 teaspoon salt", "1 cup walnuts"],
        instructions=["Mix the batter.", "Bake for 10 minutes."],
    )


def review(
    text: str = "I used 1 tsp salt, omitted walnuts, and may add cinnamon next time.",
) -> ReviewEvidence:
    return ReviewEvidence(review_id="cookie:featured:0", text=text, rating=5, featured_rank=0)


def intent(
    intent_id: str,
    quote: str,
    target: str | None,
    operation: Operation | None,
    expected: str | None,
    replacement: str | None = None,
    *,
    state: EvidenceState = EvidenceState.PERFORMED,
    actionability: Actionability = Actionability.ACTIONABLE,
    missing: list[str] | None = None,
    requires: list[str] | None = None,
) -> ModificationIntent:
    edits = []
    if target is not None and operation is not None and expected is not None:
        edits.append(
            RecipeEdit(
                edit_id=f"{intent_id}-edit",
                target_line_id=target,
                operation=operation,
                expected_text=expected,
                replacement_text=replacement,
            )
        )
    return ModificationIntent(
        intent_id=intent_id,
        kind=IntentKind.QUANTITY_ADJUSTMENT,
        source_quote=quote,
        evidence_state=state,
        actionability=actionability,
        edits=edits,
        missing_information=missing or [],
        requires_intent_ids=requires or [],
    )


def test_performed_bundle_is_applied_and_future_intent_is_not():
    original = recipe()
    evidence = review()
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
            ),
            intent(
                "nuts",
                "omitted walnuts",
                "ingredient:2",
                Operation.REMOVE_LINE,
                "1 cup walnuts",
            ),
            intent(
                "cinnamon",
                "may add cinnamon next time",
                None,
                None,
                None,
                state=EvidenceState.FUTURE,
                actionability=Actionability.INCOMPLETE,
            ),
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert decision.bundle_status is DecisionStatus.APPLIED
    assert candidate is not None
    assert candidate.recipe.ingredients == ["2 eggs", "1 teaspoon salt"]
    assert [item.status for item in decision.intent_decisions] == [
        DecisionStatus.APPLIED,
        DecisionStatus.APPLIED,
        DecisionStatus.NOT_APPLIED,
    ]
    assert original.ingredients == ["2 eggs", "0.5 teaspoon salt", "1 cup walnuts"]


def test_one_unresolved_performed_intent_blocks_the_whole_performed_bundle():
    original = recipe()
    evidence = review("I used 1 tsp salt and added a little cinnamon.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
            ),
            intent(
                "cinnamon",
                "added a little cinnamon",
                None,
                None,
                None,
                actionability=Actionability.INCOMPLETE,
                missing=["Exact cinnamon amount and insertion point"],
            ),
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert decision.bundle_status is DecisionStatus.NEEDS_REVIEW
    assert candidate is None
    assert decision.intent_decisions[0].reasons == ["PERFORMED_BUNDLE_ATOMICITY"]
    assert "MISSING_INFORMATION" in decision.intent_decisions[1].reasons


def test_two_performed_edits_to_same_original_line_are_not_silently_composed():
    original = recipe()
    evidence = review("I used 1 tsp salt, then decided 2 tsp salt was better.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="mixed",
        intents=[
            intent(
                "salt-1",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
            ),
            intent(
                "salt-2",
                "2 tsp salt was better",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "2 teaspoons salt",
            ),
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert decision.bundle_status is DecisionStatus.NEEDS_REVIEW
    assert candidate is None
    assert all(
        "MULTIPLE_EDITS_TO_SAME_TARGET" in item.reasons for item in decision.intent_decisions
    )


def test_exact_precondition_failure_is_visible_instead_of_fuzzy_matched():
    original = recipe()
    evidence = review("I used 1 tsp salt.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "1/2 tsp salt",
                "1 teaspoon salt",
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert decision.intent_decisions[0].reasons == ["PRECONDITION_MISMATCH"]


def test_hallucinated_source_quote_cannot_reach_execution():
    original = recipe()
    evidence = review("I used 1 tsp salt.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "I doubled the salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert decision.intent_decisions[0].reasons == ["SOURCE_QUOTE_NOT_GROUNDED"]


def test_no_op_is_not_reported_as_an_applied_change():
    original = recipe()
    evidence = review("I used 0.5 teaspoon salt.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 0.5 teaspoon salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "0.5 teaspoon salt",
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert decision.intent_decisions[0].reasons == ["NO_OP"]


def test_dependency_must_be_another_performed_intent_in_the_same_review():
    original = recipe()
    evidence = review("I used 1 tsp salt and may omit walnuts next time.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="mixed",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
                requires=["nuts"],
            ),
            intent(
                "nuts",
                "may omit walnuts next time",
                None,
                None,
                None,
                state=EvidenceState.FUTURE,
                actionability=Actionability.INCOMPLETE,
            ),
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert "DEPENDENCY_NOT_PERFORMED" in decision.intent_decisions[0].reasons


def test_one_semantic_intent_can_apply_coordinated_ingredient_and_instruction_edits():
    original = recipe()
    evidence = review("I omitted walnuts.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            ModificationIntent(
                intent_id="remove-walnuts",
                kind=IntentKind.INGREDIENT_REMOVAL,
                source_quote="omitted walnuts",
                evidence_state=EvidenceState.PERFORMED,
                actionability=Actionability.ACTIONABLE,
                edits=[
                    RecipeEdit(
                        edit_id="remove-ingredient",
                        target_line_id="ingredient:2",
                        operation=Operation.REMOVE_LINE,
                        expected_text="1 cup walnuts",
                    ),
                    RecipeEdit(
                        edit_id="update-instruction",
                        target_line_id="instruction:0",
                        operation=Operation.REPLACE_LINE,
                        expected_text="Mix the batter.",
                        replacement_text="Mix the batter without walnuts.",
                    ),
                ],
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert decision.bundle_status is DecisionStatus.APPLIED
    assert candidate is not None
    assert len(candidate.changes) == 2
    assert candidate.recipe.ingredients == ["2 eggs", "0.5 teaspoon salt"]
    assert candidate.recipe.instructions[0] == "Mix the batter without walnuts."


def test_replace_and_insert_after_same_anchor_are_composed_in_stable_order():
    original = recipe()
    evidence = review("I mixed carefully and rested the batter.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            ModificationIntent(
                intent_id="mix",
                kind=IntentKind.TECHNIQUE_CHANGE,
                source_quote="mixed carefully",
                evidence_state=EvidenceState.PERFORMED,
                actionability=Actionability.ACTIONABLE,
                edits=[
                    RecipeEdit(
                        edit_id="replace-mix",
                        target_line_id="instruction:0",
                        operation=Operation.REPLACE_LINE,
                        expected_text="Mix the batter.",
                        replacement_text="Mix the batter carefully.",
                    )
                ],
            ),
            ModificationIntent(
                intent_id="rest",
                kind=IntentKind.TECHNIQUE_CHANGE,
                source_quote="rested the batter",
                evidence_state=EvidenceState.PERFORMED,
                actionability=Actionability.ACTIONABLE,
                edits=[
                    RecipeEdit(
                        edit_id="insert-rest",
                        target_line_id="instruction:0",
                        operation=Operation.INSERT_AFTER,
                        expected_text="Mix the batter.",
                        insert_text="Rest the batter.",
                    )
                ],
            ),
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert decision.bundle_status is DecisionStatus.APPLIED
    assert candidate is not None
    assert candidate.recipe.instructions[:2] == [
        "Mix the batter carefully.",
        "Rest the batter.",
    ]


def test_duplicate_edit_ids_across_intents_block_the_bundle():
    original = recipe()
    evidence = review("I used 1 tsp salt and omitted walnuts.")
    salt = intent(
        "salt",
        "used 1 tsp salt",
        "ingredient:1",
        Operation.REPLACE_LINE,
        "0.5 teaspoon salt",
        "1 teaspoon salt",
    )
    nuts = intent(
        "nuts",
        "omitted walnuts",
        "ingredient:2",
        Operation.REMOVE_LINE,
        "1 cup walnuts",
    )
    nuts.edits[0].edit_id = salt.edits[0].edit_id
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[salt, nuts],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert decision.bundle_status is DecisionStatus.NEEDS_REVIEW
    assert "DUPLICATE_EDIT_ID" in decision.reasons
    assert all("DUPLICATE_EDIT_ID" in item.reasons for item in decision.intent_decisions)


def test_ungrounded_outcome_and_unknown_intent_reference_block_the_bundle():
    original = recipe()
    evidence = review("I used 1 tsp salt. It tasted better.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
            )
        ],
        outcome_claims=[
            OutcomeClaim(
                source_quote="It won an award.",
                claim="Award-winning result",
                supports_intent_ids=["missing-intent"],
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert set(decision.reasons) >= {
        "OUTCOME_QUOTE_NOT_GROUNDED",
        "OUTCOME_UNKNOWN_INTENT_REFERENCE",
    }


def test_grounded_outcome_with_known_intent_keeps_bundle_executable():
    original = recipe()
    evidence = review("I used 1 tsp salt. It tasted better.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
            )
        ],
        outcome_claims=[
            OutcomeClaim(
                source_quote="It tasted better.",
                claim="The changed batch tasted better",
                supports_intent_ids=["salt"],
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert decision.bundle_status is DecisionStatus.APPLIED
    assert candidate is not None


def test_self_dependency_is_rejected():
    original = recipe()
    evidence = review("I used 1 tsp salt.")
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[
            intent(
                "salt",
                "used 1 tsp salt",
                "ingredient:1",
                Operation.REPLACE_LINE,
                "0.5 teaspoon salt",
                "1 teaspoon salt",
                requires=["salt"],
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert "SELF_DEPENDENCY" in decision.reasons


def test_remove_and_insert_after_same_target_conflict():
    original = recipe()
    evidence = review("I omitted walnuts and added pecans after them.")
    remove = intent(
        "remove",
        "omitted walnuts",
        "ingredient:2",
        Operation.REMOVE_LINE,
        "1 cup walnuts",
    )
    insert = ModificationIntent(
        intent_id="pecans",
        kind=IntentKind.INGREDIENT_ADDITION,
        source_quote="added pecans after them",
        evidence_state=EvidenceState.PERFORMED,
        actionability=Actionability.ACTIONABLE,
        edits=[
            RecipeEdit(
                edit_id="insert-pecans",
                target_line_id="ingredient:2",
                operation=Operation.INSERT_AFTER,
                expected_text="1 cup walnuts",
                insert_text="1 cup pecans",
            )
        ],
    )
    analysis = ReviewAnalysis(
        review_id=evidence.review_id,
        sentiment="positive",
        intents=[remove, insert],
    )

    decision, candidate = evaluate_review_bundle(
        original, build_recipe_lines(original), evidence, analysis
    )

    assert candidate is None
    assert "MULTIPLE_EDITS_TO_SAME_TARGET" in decision.reasons


def test_inserted_provenance_id_stays_bounded_with_maximum_edit_id():
    original = recipe()
    evidence = review("I added pecans after the walnuts.")
    addition = ModificationIntent(
        intent_id="pecans",
        kind=IntentKind.INGREDIENT_ADDITION,
        source_quote="added pecans after the walnuts",
        evidence_state=EvidenceState.PERFORMED,
        actionability=Actionability.ACTIONABLE,
        edits=[
            RecipeEdit(
                edit_id="e" * 128,
                target_line_id="ingredient:2",
                operation=Operation.INSERT_AFTER,
                expected_text="1 cup walnuts",
                insert_text="1 cup pecans",
            )
        ],
    )

    decision, candidate = evaluate_review_bundle(
        original,
        build_recipe_lines(original),
        evidence,
        ReviewAnalysis(
            review_id=evidence.review_id,
            sentiment="positive",
            intents=[addition],
        ),
    )

    assert decision.bundle_status is DecisionStatus.APPLIED
    assert candidate is not None
    assert len(candidate.changes[0].line_id) <= 128
    assert candidate.changes[0].line_id.startswith("inserted:0:")
