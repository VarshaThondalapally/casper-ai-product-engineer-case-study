# Agent trajectory

## 1. Preserve and audit

- Confirmed the repository and source baseline before making changes.
- Ran the abandoned candidate implementation's baseline: 37 tests passed.
- Audited the implementation and rejected its direction because its complexity and
  semantic call graph were disproportionate to the assessment.

## 2. Recover from the source baseline

- Restarted from the supplied source baseline.
- Re-read the inherited model, prompt, random review selection, fuzzy modifier,
  orchestrator, test script, and all six supplied recipe files.
- Counted 12 featured reviews across four recipes and two zero-tweak recipes.

## 3. Establish product semantics

- Chose `featured_tweaks` as the authoritative assignment evidence.
- Separated evidence state from actionability.
- Preserved performed changes within a review as a transactional co-tested bundle.
- Treated reviews as independent alternatives from an immutable original.
- Declined cross-review merging and primary ranking without supporting evidence.

## 4. Implement the smallest useful architecture

- Added stable ingredient/instruction IDs.
- Replaced JSON mode and manual parsing with one strict Responses API extraction.
- Selected `gpt-5.6-terra` with medium reasoning and an environment override.
- Added exact grounding, dependency, no-op, precondition, and conflict policy.
- Removed random selection, fuzzy matching, the unused safety check, and the
  generated-recipe wrapper.

## 5. Test and learn

- Added deterministic tests before live evaluation.
- Hand-labeled all 12 featured reviews with 34 intent/evidence expectations.
- First live run found a mistaken no-op label and two omitted implicit intents.
- Second run exposed quote nondeterminism; tightened the contiguous verbatim quote
  contract and added grounding as a hard threshold.
- Replay through policy revealed a more serious schema defect: one edit per intent
  caused zero candidates for coordinated changes such as ingredient removal plus
  instruction cleanup.
- Changed the contract so one intent can own multiple atomic edits.
- Refined same-target policy to compose replace-plus-insert safely while blocking
  true replacement/removal conflicts.

## 6. Final evidence

- 55 deterministic tests, including corpus-label and release-version guards, pass
  under the final coverage gate.
- The verification gate covers lint, formatting, compilation, Bandit, dependency
  lock/audit, 90% line coverage and a read-only Python 3.11/3.13 CI matrix.
- The frozen labels now cover 34 intents, 20 primary outcomes, all 12 bundle
  decisions and 14 exact edits across the three executable alternatives.
- Final live evaluation: 88.2% intent recall, 81.1% labeled precision, 100%
  source/outcome grounding, 100% bundle/candidate accuracy, 100% exact field
  edit replay and zero failed calls.
- Policy replay produced three applied alternatives, seven needs-review bundles
  and two not-applied bundles.

## 7. Guided run and observability follow-up

- Guided a VS Code run from branch/interpreter verification through deterministic
  tests, safe credential presence check and a single-recipe live execution.
- Confirmed that stale generated output from an abandoned branch was not evidence
  for the current contract, removed it from the submission, and reran the final
  evaluation against the current implementation.
- Added optional `--trace` capture after the user correctly requested visibility
  into the exact API boundary and storage path.
- Added per-review request, response and policy files plus a run summary. Trace
  tests prove the API key and authorization headers are excluded.
- Persisted the transferable architecture patterns and all early worked examples
  in `docs/architecture_patterns_and_examples.md` rather than leaving them only
  in conversation history.

No API credential was printed or committed. Generated live outputs remain ignored.

## 8. Final engineering audit

- Reclassified every late finding by origin: inherited starter behavior,
  unavoidable LLM-system risk, or a defect/gap in the replacement.
- Tightened input schemas and run bounds; removed silent recipe defaults and made
  malformed files visible without discarding valid sibling results.
- Hardened output paths, atomic persistence, error redaction, prompt-injection
  boundaries, retries, timeouts and output-token ceilings.
- Fixed replacement-specific policy gaps: empty evidence, duplicate IDs,
  dependency integrity, review identity, outcome grounding and intent references.
- Added a conservative evidence canonicalizer that repairs only a unique
  case-only mismatch while retaining the raw response in trace.
- Replaced intent-only evaluation with field-aware scoring, frozen thresholds,
  prompt/schema/label hashes and deterministic replay.
- Upgraded the vulnerable inherited dotenv dependency and confirmed the locked
  runtime set has no known published vulnerabilities.
- Removed stale outputs and assessment drafts from the submitted tree; kept only
  the sanitized benchmark summary required to substantiate the reported result.
