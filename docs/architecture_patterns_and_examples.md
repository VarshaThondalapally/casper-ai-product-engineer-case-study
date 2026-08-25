# Transferable architecture patterns and worked examples

This document captures general architecture lessons from prior multimodal and
image-understanding work. It does not reuse any former-employer code, prompts,
data, or proprietary implementation. The domain here is recipe-review evidence;
the transferable knowledge is semantic understanding at the boundary followed by
a strict, observable, deterministic execution contract.

## Transferable patterns

### 1. Open-ended semantic interpretation instead of regex intent routing

In prior multimodal product work, image understanding could not be reduced to a
fixed list of phrases or pixel templates. A model needed to understand extraction
intent, objects, relationships, sentiment, and uncertainty in context.

Recipe reviews have the same shape:

```text
"Used fresh grated ginger"          -> performed substitution
"Will use more broth next time"     -> future quantity intent
"I prefer more apple chunks"        -> preference
"Even w 2% milk"                    -> implicit performed substitution
```

Regex can find words such as `used`, `will`, or `prefer`; it cannot reliably
resolve the scope, implied target, experiment boundary, or whether the statement
is an outcome rather than an instruction. The model therefore performs semantic
extraction, while deterministic code validates its output.

### 2. Flexible understanding, strict typed output

The general pattern is to allow a model to understand arbitrary input while
requiring a stable downstream object. Here, Responses Structured Outputs must
satisfy the `ReviewAnalysis` Pydantic schema.

```text
free-form review
    -> sentiment
    -> atomic intents
    -> evidence state
    -> actionability
    -> exact source quote
    -> zero or more RecipeEdit records
    -> outcome claims
```

The prompt remains open to meanings we did not enumerate, while the software
boundary remains closed and testable.

### 3. Evidence grounding and provenance

Image-derived claims need a connection to visible evidence. Recipe claims need
the same connection to the review and original recipe.

Every applied edit retains:

```text
review_id -> source_quote -> intent_id -> edit_id
          -> exact original line -> before/after change -> candidate
```

The deterministic policy rejects a source quote that is not a contiguous verbatim
substring of the review.

### 4. Understanding state is separate from execution state

The system distinguishes two axes:

```text
Evidence:      performed / recommended / future / hypothetical / preference / unclear
Actionability: actionable / incomplete / non_actionable
```

A performed action can still be incomplete. A future action can be very precise
but is still not evidence of a tested result. Sentiment is captured separately and
never acts as permission to mutate a recipe.

### 5. Abstention is a product result

A reliable multimodal system cannot invent missing visual evidence. This pipeline
cannot invent a quantity, target, method, or dependency.

```text
"added a tiny dash of cinnamon"
    evidence_state = performed
    actionability = incomplete
    decision = needs_review
```

`needs_review` is preferable to a polished hallucination.

### 6. Immutable source plus derived variants

As with source media and derived analyses generally, the original recipe is never
modified in place. Every candidate starts from the same immutable original.

This makes variants comparable, prevents order-dependent contamination, and makes
rollback trivial.

### 7. Preserve experiment boundaries

A review describes one tasted experiment. Its performed changes are applied as a
transactional bundle. Different reviews describe different experiments and remain
alternatives unless the product later gains evidence for controlled composition.

### 8. Deterministic enforcement after the model

The LLM interprets meaning but does not decide conflicts or mutate text. Code
enforces:

- exact source grounding;
- performed-only eligibility;
- complete edit fields;
- dependency closure;
- stable target IDs;
- exact full-line preconditions;
- no-op rejection;
- duplicate and same-target conflict rules;
- transactional application.

### 9. Evaluation before optimization

The golden set and deterministic replay exposed two architecture bugs that happy
path demos did not:

1. One edit per intent produced zero useful candidates because “omit walnuts”
   needs ingredient and instruction edits.
2. Treating every shared target as a conflict blocked a valid replace-plus-insert
   composition.

Both were fixed without adding another LLM pass.

### 10. Observable model boundaries

The optional trace records the exact application request, public Responses API
configuration, response metadata, raw structured output text, parsed object,
policy decision, candidate and storage path. It deliberately excludes credentials,
headers, and unavailable internal model reasoning.

## Worked examples

### Example A: performed, future, and outcome in one review

```text
"Very thick.. Even w 2% milk - will use more broth next time.
Used fresh grated ginger. Hearty and yummy!"
```

Expected interpretation:

| Phrase | Meaning | Evidence state | Execution |
|---|---|---|---|
| `Even w 2% milk` | Dairy substitution | Performed | Needs amount/target validation |
| `will use more broth next time` | Broth increase | Future | Not applied |
| `Used fresh grated ginger` | Ginger substitution | Performed | Needs exact amount |
| `Hearty and yummy!` | Outcome/sentiment | Not an edit | Never executed |

This cannot be represented safely by one regex-selected modification.

### Example B: performed but vague

Original recipe contains three cups of flour. A review says:

```text
"used 1/2 c less flour ... and added a tiny dash of cinnamon"
```

The flour change is deterministic: `3 cups -> 2.5 cups`. The cinnamon was
performed but has no reproducible quantity. Because both were tasted together,
the current policy marks the performed bundle `needs_review` instead of publishing
a partial recipe with the review's combined outcome.

### Example C: one semantic intent, multiple edits

```text
"omitted the nuts"
```

Before:

```text
Ingredient:  1 cup chopped walnuts
Instruction: Stir in flour, chocolate chips, and walnuts.
```

After:

```text
Ingredient:  removed
Instruction: Stir in flour and chocolate chips.
```

This is one intent with two coordinated edits. Either both commit or neither does.

### Example D: same target, genuine conflict

Suppose one review says both:

```text
"I changed the sugar to 1 cup."
"I changed the sugar to 2 cups."
```

Two replacements target the same original ingredient line. The system cannot know
which represents the final tested state, so the bundle becomes `needs_review`.

If Reviewer A says one cup and Reviewer B says two cups, they are not combined or
treated as an error. They become two independent alternatives.

### Example E: same target, compatible composition

One intent removes hot water by replacing:

```text
Dissolve baking soda in hot water. Add to batter along with salt.
```

Another inserts after that same original line:

```text
Stir cream of tartar into the batter.
```

This is compatible. Insertions execute first against the exact original anchor;
the anchor is then replaced. Final output:

```text
Add baking soda and salt to batter.
Stir cream of tartar into the batter.
```

### Example F: different recipe stages across reviews

```text
Reviewer A: change sugar
Reviewer B: add three eggs
Reviewer C: use an air fryer instead of an oven
```

Different targets do not prove compatibility. These were separate experiments,
possibly with different outcomes and hidden changes. The default output is three
alternatives, not one hybrid recipe. A future merge feature would need explicit
compatibility rules, provenance for the composed recipe, and evaluation of
cross-change interactions.

### Example G: status versus relationship

`applied`, `needs_review`, and `not_applied` are execution decisions.
`alternative` is a relationship between candidates.

```text
Review A -> applied      -> Candidate A --alternative-to--> Candidate B
Review B -> applied      -> Candidate B
Review C -> needs_review -> no candidate
Review D -> not_applied  -> no candidate
```

Calling `alternative` a fourth status would mix two different dimensions.

### Example H: no-op awareness

The original cookie recipe already contains one cup of white sugar. A reviewer
saying they used one cup of white sugar is evidence of following the original, not
a modification. The golden evaluation initially mislabeled this as an intent and
was corrected after comparing the review with the source recipe.

### Example I: full applied cookie bundle

The successful featured review produced five intents and seven exact edits:

```text
1 cup white sugar       -> 0.5 cup
1 cup brown sugar       -> 1.5 cups
2 teaspoons hot water   -> removed
water-dependent step    -> rewritten
cream of tartar         -> ingredient inserted
cream of tartar use     -> instruction inserted
chilling                -> one-hour instruction inserted
```

Every change retains the exact supporting quote and starts from the immutable
original recipe.

## Model choice for this design

`gpt-5.6-terra` with medium reasoning is the primary extractor because the task is
semantic and relational, while cost and latency still matter. It is called once
per review with strict structured output. No LLM is used after extraction.

The next model experiment should compare Terra against Luna on the same frozen
labels and field-level edit expectations. Sol is an escalation/evaluation option,
not a default extra pass. Model routing should be earned by measured failure modes,
not added speculatively.
