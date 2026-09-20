"""Category-based orchestration for a complete formal evaluation suite."""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Protocol

from evaluation.contracts import EvalCase
from evaluation.formal_gate import dataset_digest
from evaluation.order_runner import CaseRun, SuiteRun, summarize_case_runs


class CategoryRunner(Protocol):
    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun: ...


class MultiDomainEvalRunner:
    """Dispatch cases to explicit category runners and fail on partial output."""

    def __init__(self, runners: Mapping[str, CategoryRunner]) -> None:
        self.runners = dict(runners)

    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun:
        if not cases:
            raise ValueError("evaluation cases must not be empty")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("evaluation case_id values must be unique")
        grouped: dict[str, list[EvalCase]] = defaultdict(list)
        for case in cases:
            grouped[case.category].append(case)

        missing = sorted(set(grouped) - set(self.runners))
        if missing:
            raise ValueError(f"missing category runners: {', '.join(missing)}")

        results_by_id: dict[str, CaseRun] = {}
        for category, category_cases in grouped.items():
            case_tuple = tuple(category_cases)
            suite = self.runners[category].run(case_tuple)
            expected_ids = {case.case_id for case in case_tuple}
            actual_ids = {case_run.case_id for case_run in suite.case_runs}
            outputs_are_consistent = all(
                case_run.category == category
                and case_run.observation.case_id == case_run.case_id
                and case_run.result.case_id == case_run.case_id
                for case_run in suite.case_runs
            )
            if (
                actual_ids != expected_ids
                or len(suite.case_runs) != len(case_tuple)
                or not outputs_are_consistent
            ):
                raise ValueError(
                    f"runner result mismatch for category {category}"
                )
            results_by_id.update(
                {case_run.case_id: case_run for case_run in suite.case_runs}
            )

        ordered_runs = tuple(results_by_id[case.case_id] for case in cases)
        return SuiteRun(
            dataset_version=cases[0].dataset_version,
            dataset_digest=dataset_digest(cases),
            case_runs=ordered_runs,
            summary=summarize_case_runs(ordered_runs),
        )
