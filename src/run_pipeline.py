"""Run the live pipeline against one file or the supplied data directory."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from llm_pipeline.pipeline import LLMAnalysisPipeline
from llm_pipeline.security import safe_exception_text


def main(
    argv: Sequence[str] | None = None,
    *,
    pipeline: LLMAnalysisPipeline | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data")
    parser.add_argument("--output", default="outputs")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Save redacted request, response, and policy artifacts per review",
    )
    args = parser.parse_args(argv)
    load_dotenv()

    runner = pipeline or LLMAnalysisPipeline()
    source = Path(args.path)
    try:
        if source.is_dir():
            run = runner.process_directory(
                source,
                args.output,
                capture_trace=args.trace,
            )
            calls = sum(len(result.model_calls) for result in run.results)
            candidates = sum(len(result.candidates) for result in run.results)
            extraction_failures = sum(len(result.extraction_failures) for result in run.results)
            failures = extraction_failures + len(run.file_failures)
            print(
                f"processed={len(run.results)} calls={calls} candidates={candidates} "
                f"failures={failures} file_failures={len(run.file_failures)}"
            )
            for file_name, error in sorted(run.file_failures.items()):
                print(f"file_failure={file_name} error={error}", file=sys.stderr)
            return 1 if failures else 0

        if args.trace:
            result, trace = runner.process_file_with_trace(source)
        else:
            result = runner.process_file(source)
            trace = None
        output = runner.result_output_path(args.output, result.original_recipe.recipe_id)
        runner.save_result(result, output)
        trace_summary = None
        if trace is not None:
            trace_summary = runner.save_trace(trace, output.parent / "trace", output)
        failures = len(result.extraction_failures)
        print(
            f"recipe={result.original_recipe.recipe_id} calls={len(result.model_calls)} "
            f"candidates={len(result.candidates)} failures={failures} "
            f"output={output}"
            f"{f' trace={trace_summary}' if trace_summary is not None else ''}"
        )
        return 1 if failures else 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns a reliable status
        print(f"error={safe_exception_text(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
