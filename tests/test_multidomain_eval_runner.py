from evaluation.contracts import (
    EvalCase,
    EvalObservation,
    EvalResult,
)
from evaluation.multidomain_runner import MultiDomainEvalRunner
from evaluation.order_runner import CaseRun, SuiteRun


def _case(case_id: str, category: str) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_version="ecommerce-agent-golden-v1",
        category=category,
        turns=(f"question-{case_id}",),
    )


class FakeCategoryRunner:
    def __init__(self, *, passed: bool = True, metric_name: str = "bounded_termination"):
        self.passed = passed
        self.metric_name = metric_name
        self.received: list[tuple[str, ...]] = []

    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun:
        self.received.append(tuple(case.case_id for case in cases))
        case_runs = tuple(
            CaseRun(
                case_id=case.case_id,
                category=case.category,
                observation=EvalObservation(
                    case_id=case.case_id,
                    terminal_status="completed",
                    write_count=0,
                ),
                result=EvalResult(
                    case_id=case.case_id,
                    passed=self.passed,
                    metrics={self.metric_name: self.passed},
                    failed_assertions=() if self.passed else ("failed",),
                ),
            )
            for case in cases
        )
        return SuiteRun(
            dataset_version=cases[0].dataset_version,
            dataset_digest="0" * 64,
            case_runs=case_runs,
            summary={},
        )


def test_multidomain_runner_dispatches_each_category_and_restores_case_order():
    knowledge = FakeCategoryRunner(metric_name="answer_relevance")
    orders = FakeCategoryRunner(metric_name="tool_sequence")
    cases = (
        _case("knowledge-1", "knowledge"),
        _case("order-1", "order_logistics"),
        _case("knowledge-2", "knowledge"),
    )

    suite = MultiDomainEvalRunner(
        {
            "knowledge": knowledge,
            "order_logistics": orders,
        }
    ).run(cases)

    assert knowledge.received == [("knowledge-1", "knowledge-2")]
    assert orders.received == [("order-1",)]
    assert tuple(item.case_id for item in suite.case_runs) == (
        "knowledge-1",
        "order-1",
        "knowledge-2",
    )
    assert suite.summary["end_to_end_task_success"] == {
        "passed": 3,
        "total": 3,
        "rate": 1.0,
    }
    assert suite.summary["answer_relevance"] == {
        "passed": 2,
        "total": 2,
        "rate": 1.0,
    }
    assert suite.summary["tool_sequence"] == {
        "passed": 1,
        "total": 1,
        "rate": 1.0,
    }


def test_multidomain_runner_rejects_unregistered_category_before_execution():
    knowledge = FakeCategoryRunner()
    cases = (
        _case("knowledge-1", "knowledge"),
        _case("appointment-1", "appointment"),
    )

    try:
        MultiDomainEvalRunner({"knowledge": knowledge}).run(cases)
    except ValueError as exc:
        assert str(exc) == "missing category runners: appointment"
    else:
        raise AssertionError("missing category runner must fail closed")

    assert knowledge.received == []


def test_multidomain_runner_rejects_runner_that_drops_a_case():
    class DroppingRunner(FakeCategoryRunner):
        def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun:
            return super().run(cases[:1])

    cases = (
        _case("knowledge-1", "knowledge"),
        _case("knowledge-2", "knowledge"),
    )

    try:
        MultiDomainEvalRunner({"knowledge": DroppingRunner()}).run(cases)
    except ValueError as exc:
        assert str(exc) == "runner result mismatch for category knowledge"
    else:
        raise AssertionError("partial runner output must fail closed")


def test_multidomain_runner_rejects_empty_or_duplicate_case_ids():
    runner = MultiDomainEvalRunner({"knowledge": FakeCategoryRunner()})

    for cases, expected in (
        ((), "evaluation cases must not be empty"),
        (
            (
                _case("knowledge-1", "knowledge"),
                _case("knowledge-1", "knowledge"),
            ),
            "evaluation case_id values must be unique",
        ),
    ):
        try:
            runner.run(cases)
        except ValueError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError(expected)
