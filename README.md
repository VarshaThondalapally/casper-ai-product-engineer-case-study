# Recipe Review Evidence Pipeline

[![CI](https://github.com/VarshaThondalapally/recipe-review-evidence-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/VarshaThondalapally/recipe-review-evidence-pipeline/actions/workflows/ci.yml)

This project turns community-tested recipe tweaks into safe, attributed recipe
alternatives. The LLM interprets open-ended language once; deterministic code
decides whether the evidence is executable and applies exact edits.

## Product semantics

- Every `featured_tweaks` review is analyzed. Non-featured reviews are not silently
  mixed into the evidence set.
- Every distinct tweak is extracted with an exact source quote and classified as
  `performed`, `recommended`, `future`, `hypothetical`, `preference`, or `unclear`.
- Only complete, grounded, performed tweaks can be applied.
- Performed tweaks from one review are a co-tested bundle: all safe edits commit,
  or none do.
- Different reviews produce independent alternatives from the immutable original.
  They are never naively merged or ranked without a helpful-vote signal.
- Vague, conflicting, future, and preference evidence remains visible as
  `needs_review` or `not_applied` instead of becoming a false success.

## Architecture

```text
featured review + stable recipe line IDs
                 |
                 v
  one GPT-5.6 Terra structured extraction
                 |
                 v
 grounding / evidence / precondition / conflict policy
                 |
                 v
 transactional exact edits -> attributed alternative
```

The model cannot mutate a recipe. It returns strict Pydantic data containing
semantic intents and one or more exact line edits per intent. The deterministic
layer validates quote grounding, evidence state, actionability, dependencies,
duplicate IDs, exact preconditions, no-ops, and target conflicts before editing.
Input sizes and call counts are bounded; output identifiers are sanitized and
resolved inside the configured output root.

## Setup

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --group dev
```

For live extraction, copy `.env.example` to `.env` and set the key locally:

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
```

Timeout, retry count, reasoning effort and output-token limits are optional,
bounded environment settings documented in `.env.example`.

## Run

Deterministic tests do not call an API:

```bash
uv run pytest --cov=llm_pipeline --cov=run_pipeline --cov-report=term-missing --cov-fail-under=90 -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run bandit -r src -q
```

Run the six supplied recipes:

```bash
uv run python src/run_pipeline.py data --output outputs
```

The command returns non-zero if any source file or model extraction fails. Capture
the exact redacted API/policy boundary for a demo run with this PowerShell-safe,
single-line command:

```bash
uv run python src/run_pipeline.py data/recipe_10813_best-chocolate-chip-cookies.json --output outputs/demo --trace
```

Trace output is organized per review:

```text
outputs/demo/10813/trace/
├── run-summary.json
├── review-0-request.json
├── review-0-response.json
├── review-0-policy.json
└── ...
```

The request includes exactly what the application passed to `responses.parse`,
including the strict JSON Schema. Responses include public IDs, usage, latency,
raw structured text and parsed output. Policy files show why the review was
applied or withheld. Credentials, headers and private model reasoning are not
recorded. Trace paths are relative, and trace files are explicitly labeled as
potentially sensitive because they contain recipe and review text.

Run the hand-labeled evaluation of all 12 featured reviews:

```bash
uv run python src/evaluate_extractions.py
```

Rescore an existing full report without making model calls:

```bash
uv run python src/evaluate_extractions.py --replay-report outputs/evaluation-report.json --output outputs/evaluation-replay.json
```

Outputs are written below `outputs/` and intentionally ignored by git because
they contain model-generated experiment data.

## Verified result

The final field-aware `gpt-5.6-terra` run covered all 12 featured reviews, 34
intent labels, 20 primary outcome labels and 14 exact edit expectations:

- 88.2% intent recall and 81.1% labeled precision
- 100% evidence-state accuracy and 90.0% actionability accuracy on matched intents
- 100% intent/outcome quote grounding and 100% primary-outcome recall
- 100% bundle-status and candidate-presence accuracy
- 100% edit target/operation recall and precision
- 100% exact precondition and required-output accuracy after deterministic replay
- three exact executable bundles; 3 applied / 7 needs review / 2 not applied
- zero failed model calls; 27,404 input / 12,652 output tokens; 157.2 seconds

The replay changed only scoring for explicitly enumerated equivalent fractions and
valid insertion anchors; it did not alter extracted model output. The sanitized
summary, thresholds and artifact hashes are committed at
[`evaluation/benchmark_summary.json`](evaluation/benchmark_summary.json).

This remains a small, curated, assignment-specific evaluation—not a production or
food-safety guarantee. It now measures exact executable edits, but still needs a
separate holdout corpus and broader repeated-run/model-comparison testing.

See [the assessment report](docs/assessment_report.md),
[transferable architecture patterns and worked examples](docs/architecture_patterns_and_examples.md),
and [the required agent trajectory](agent_trajectory.md). The 5–7 minute video is
an external submission item rather than a repository artifact.
