"""Deterministic policy and transactional recipe editing."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256

from .models import (
    Actionability,
    CandidateRecipe,
    ChangeRecord,
    DecisionStatus,
    EvidenceState,
    IntentDecision,
    ModificationIntent,
    Operation,
    Recipe,
    RecipeLine,
    ReviewAnalysis,
    ReviewDecision,
    ReviewEvidence,
    Section,
)


@dataclass
class _WorkingLine:
    line_id: str
    section: Section
    text: str


def _execution_errors(
    intent: ModificationIntent,
    review: ReviewEvidence,
    line_index: dict[str, RecipeLine],
    performed_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    if intent.source_quote not in review.text:
        errors.append("SOURCE_QUOTE_NOT_GROUNDED")
    if intent.actionability is not Actionability.ACTIONABLE:
        errors.append("INTENT_NOT_ACTIONABLE")
    if intent.missing_information:
        errors.append("MISSING_INFORMATION")
    if not intent.edits:
        errors.append("INCOMPLETE_EXECUTION_FIELDS")
        return errors

    edit_ids = [edit.edit_id for edit in intent.edits]
    if len(edit_ids) != len(set(edit_ids)):
        errors.append("DUPLICATE_EDIT_ID")

    for edit in intent.edits:
        target = line_index.get(edit.target_line_id)
        if target is None:
            errors.append("TARGET_LINE_NOT_FOUND")
            continue
        if target.text != edit.expected_text:
            errors.append("PRECONDITION_MISMATCH")
        if edit.operation is Operation.REPLACE_LINE:
            if not edit.replacement_text:
                errors.append("REPLACEMENT_TEXT_MISSING")
            elif edit.replacement_text == edit.expected_text:
                errors.append("NO_OP")
            if edit.insert_text:
                errors.append("REPLACE_HAS_INSERT_TEXT")
        elif edit.operation is Operation.INSERT_AFTER:
            if not edit.insert_text:
                errors.append("INSERT_TEXT_MISSING")
            if edit.replacement_text:
                errors.append("INSERT_HAS_REPLACEMENT_TEXT")
        elif edit.operation is Operation.REMOVE_LINE and (
            edit.replacement_text or edit.insert_text
        ):
            errors.append("REMOVE_HAS_OUTPUT_TEXT")

    if any(required not in performed_ids for required in intent.requires_intent_ids):
        errors.append("DEPENDENCY_NOT_PERFORMED")
    if intent.intent_id in intent.requires_intent_ids:
        errors.append("SELF_DEPENDENCY")
    if len(intent.requires_intent_ids) != len(set(intent.requires_intent_ids)):
        errors.append("DUPLICATE_DEPENDENCY")
    return errors


def _apply_bundle(
    recipe: Recipe,
    recipe_lines: list[RecipeLine],
    review: ReviewEvidence,
    intents: list[ModificationIntent],
) -> tuple[Recipe, list[ChangeRecord]]:
    working = [
        _WorkingLine(line_id=line.line_id, section=line.section, text=line.text)
        for line in recipe_lines
    ]
    indexed_edits = [
        (ordinal, intent, edit)
        for ordinal, (intent, edit) in enumerate(
            (intent, edit) for intent in intents for edit in intent.edits
        )
    ]
    application_order = [
        *reversed([item for item in indexed_edits if item[2].operation is Operation.INSERT_AFTER]),
        *[item for item in indexed_edits if item[2].operation is Operation.REPLACE_LINE],
        *[item for item in indexed_edits if item[2].operation is Operation.REMOVE_LINE],
    ]
    indexed_changes: list[tuple[int, ChangeRecord]] = []

    for ordinal, intent, edit in application_order:
        index = next(
            position for position, line in enumerate(working) if line.line_id == edit.target_line_id
        )
        target = working[index]
        if target.text != edit.expected_text:
            raise ValueError("Transactional precondition changed during application")

        before: str | None = target.text
        after: str | None = None
        changed_line_id = target.line_id
        if edit.operation is Operation.REPLACE_LINE:
            after = edit.replacement_text
            target.text = after or ""
        elif edit.operation is Operation.REMOVE_LINE:
            working.pop(index)
        elif edit.operation is Operation.INSERT_AFTER:
            before = None
            after = edit.insert_text
            provenance = f"{review.review_id}:{edit.edit_id}"
            provenance_digest = sha256(provenance.encode("utf-8")).hexdigest()[:16]
            changed_line_id = f"inserted:{review.featured_rank}:{provenance_digest}"
            working.insert(
                index + 1,
                _WorkingLine(
                    line_id=changed_line_id,
                    section=target.section,
                    text=after or "",
                ),
            )
        else:  # pragma: no cover - schema enum keeps this unreachable
            raise ValueError(f"Unsupported operation: {edit.operation}")

        indexed_changes.append(
            (
                ordinal,
                ChangeRecord(
                    intent_id=intent.intent_id,
                    edit_id=edit.edit_id,
                    line_id=changed_line_id,
                    section=target.section,
                    operation=edit.operation,
                    before=before,
                    after=after,
                    source_quote=intent.source_quote,
                ),
            )
        )

    candidate = Recipe(
        recipe_id=f"{recipe.recipe_id}--variant--{review.featured_rank}",
        title=recipe.title,
        ingredients=[line.text for line in working if line.section is Section.INGREDIENTS],
        instructions=[line.text for line in working if line.section is Section.INSTRUCTIONS],
        description=recipe.description,
        servings=recipe.servings,
        rating=recipe.rating,
    )
    return candidate, [record for _, record in sorted(indexed_changes, key=lambda item: item[0])]


def evaluate_review_bundle(
    recipe: Recipe,
    recipe_lines: list[RecipeLine],
    review: ReviewEvidence,
    analysis: ReviewAnalysis,
) -> tuple[ReviewDecision, CandidateRecipe | None]:
    """Apply all safe performed intents together, or none of them."""
    line_index = {line.line_id: line for line in recipe_lines}
    performed_positions = [
        position
        for position, intent in enumerate(analysis.intents)
        if intent.evidence_state is EvidenceState.PERFORMED
    ]
    performed = [analysis.intents[position] for position in performed_positions]
    performed_ids = {intent.intent_id for intent in performed}
    decisions: list[IntentDecision | None] = [None] * len(analysis.intents)

    intent_id_counts = Counter(intent.intent_id for intent in analysis.intents)
    duplicate_ids = {intent_id for intent_id, count in intent_id_counts.items() if count > 1}
    edit_id_counts = Counter(edit.edit_id for intent in analysis.intents for edit in intent.edits)
    duplicate_edit_ids = {edit_id for edit_id, count in edit_id_counts.items() if count > 1}
    known_intent_ids = set(intent_id_counts)
    review_errors: list[str] = []
    if analysis.review_id != review.review_id:
        review_errors.append("ANALYSIS_REVIEW_ID_MISMATCH")
    if duplicate_ids:
        review_errors.append("DUPLICATE_INTENT_ID")
    if duplicate_edit_ids:
        review_errors.append("DUPLICATE_EDIT_ID")
    for claim in analysis.outcome_claims:
        if claim.source_quote not in review.text:
            review_errors.append("OUTCOME_QUOTE_NOT_GROUNDED")
        if any(intent_id not in known_intent_ids for intent_id in claim.supports_intent_ids):
            review_errors.append("OUTCOME_UNKNOWN_INTENT_REFERENCE")
        if len(claim.supports_intent_ids) != len(set(claim.supports_intent_ids)):
            review_errors.append("OUTCOME_DUPLICATE_INTENT_REFERENCE")

    operations_by_target: dict[str, list[Operation]] = {}
    insert_text_by_target: dict[str, list[str]] = {}
    for intent in performed:
        for edit in intent.edits:
            operations_by_target.setdefault(edit.target_line_id, []).append(edit.operation)
            if edit.operation is Operation.INSERT_AFTER and edit.insert_text:
                insert_text_by_target.setdefault(edit.target_line_id, []).append(edit.insert_text)

    conflicting_targets = {
        target
        for target, operations in operations_by_target.items()
        if len(operations) > 1
        and (
            Operation.REMOVE_LINE in operations
            or operations.count(Operation.REPLACE_LINE) > 1
            or len(insert_text_by_target.get(target, []))
            != len(set(insert_text_by_target.get(target, [])))
        )
    }

    bundle_errors: list[str] = list(review_errors)
    errors_by_position: dict[int, list[str]] = {}
    for position, intent in zip(performed_positions, performed, strict=True):
        errors = _execution_errors(intent, review, line_index, performed_ids)
        if intent.intent_id in duplicate_ids:
            errors.append("DUPLICATE_INTENT_ID")
        if any(edit.edit_id in duplicate_edit_ids for edit in intent.edits):
            errors.append("DUPLICATE_EDIT_ID")
        if any(edit.target_line_id in conflicting_targets for edit in intent.edits):
            errors.append("MULTIPLE_EDITS_TO_SAME_TARGET")
        errors_by_position[position] = errors
        bundle_errors.extend(errors)

    for position, intent in enumerate(analysis.intents):
        if intent.evidence_state is not EvidenceState.PERFORMED:
            reasons = [f"EVIDENCE_STATE_{intent.evidence_state.value.upper()}"]
            if intent.source_quote not in review.text:
                reasons.append("SOURCE_QUOTE_NOT_GROUNDED")
            if intent.intent_id in duplicate_ids:
                reasons.append("DUPLICATE_INTENT_ID")
            if any(edit.edit_id in duplicate_edit_ids for edit in intent.edits):
                reasons.append("DUPLICATE_EDIT_ID")
            decisions[position] = IntentDecision(
                intent_id=intent.intent_id,
                status=DecisionStatus.NOT_APPLIED,
                reasons=sorted(set(reasons)),
            )

    if not performed:
        status = DecisionStatus.NEEDS_REVIEW if review_errors else DecisionStatus.NOT_APPLIED
        return ReviewDecision(
            review_id=review.review_id,
            bundle_status=status,
            intent_decisions=[decision for decision in decisions if decision is not None],
            reasons=sorted({"NO_PERFORMED_INTENTS", *review_errors}),
        ), None

    if bundle_errors:
        for position, intent in zip(performed_positions, performed, strict=True):
            reasons = errors_by_position[position]
            if not reasons:
                reasons = ["PERFORMED_BUNDLE_ATOMICITY"]
            decisions[position] = IntentDecision(
                intent_id=intent.intent_id,
                status=DecisionStatus.NEEDS_REVIEW,
                reasons=sorted(set(reasons)),
            )
        return ReviewDecision(
            review_id=review.review_id,
            bundle_status=DecisionStatus.NEEDS_REVIEW,
            intent_decisions=[decision for decision in decisions if decision is not None],
            reasons=sorted(set(bundle_errors)),
        ), None

    candidate_recipe, changes = _apply_bundle(recipe, recipe_lines, review, performed)
    for position, intent in zip(performed_positions, performed, strict=True):
        decisions[position] = IntentDecision(
            intent_id=intent.intent_id,
            status=DecisionStatus.APPLIED,
        )
    decision = ReviewDecision(
        review_id=review.review_id,
        bundle_status=DecisionStatus.APPLIED,
        intent_decisions=[decision for decision in decisions if decision is not None],
    )
    candidate = CandidateRecipe(
        candidate_id=f"{recipe.recipe_id}:candidate:{review.featured_rank}",
        source_review_id=review.review_id,
        recipe=candidate_recipe,
        changes=changes,
    )
    return decision, candidate
