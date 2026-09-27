from ahawr_retrieval.fingerprint import ComponentFingerprint, QueryFingerprint, compare
from ahawr_retrieval.models import RetrieveRequest
from ahawr_retrieval.query_builder import build_query


def fp(**components: str) -> QueryFingerprint:
    return QueryFingerprint({k: ComponentFingerprint.of(v) for k, v in components.items()})


TASK = "Fix totals in compute_total so that tax lines are not counted twice."


def test_formatting_only_change_is_normalized_equivalent() -> None:
    a = fp(task=TASK, feedback="Tests fail: compute_total counts TAX lines twice.")
    b = fp(task=TASK, feedback="  tests FAIL -- compute_total counts tax lines twice!!  ")
    result = compare(a, b, 0.75)
    assert result.equivalent and result.level == "normalized"


def test_rephrased_feedback_with_same_anchors_is_semantic_equivalent() -> None:
    a = fp(task=TASK, feedback="compute_total still counts tax lines twice; tests failing")
    b = fp(task=TASK, feedback="tests still failing: compute_total counts the tax lines twice")
    result = compare(a, b, 0.75)
    assert result.equivalent
    assert result.level in {"normalized", "semantic"}


def test_new_anchor_in_feedback_forces_reretrieval() -> None:
    a = fp(task=TASK, feedback="compute_total counts tax lines twice")
    b = fp(task=TASK, feedback="compute_total counts tax lines twice, see app/tax.py")
    result = compare(a, b, 0.75)
    assert not result.equivalent and result.detail["reason"] == "anchors"


def test_substantive_feedback_change_is_not_equivalent() -> None:
    a = fp(task=TASK, feedback="add logging around invoice parsing")
    b = fp(task=TASK, feedback="handle empty invoices and negative amounts gracefully")
    assert not compare(a, b, 0.75).equivalent


def test_feedback_presence_matters() -> None:
    assert not compare(fp(task=TASK), fp(task=TASK, feedback="fix the tests"), 0.75).equivalent


def test_fingerprint_roundtrip() -> None:
    original = fp(task=TASK, feedback="compute_total")
    restored = QueryFingerprint.from_dict(original.to_dict())
    assert compare(original, restored, 1.0).level == "exact"


def test_query_builder_profiles_differ() -> None:
    base = {
        "corpora": ["ws"],
        "task": {
            "task_id": "T1",
            "title": "Fix totals",
            "objective": "compute_total must skip tax lines",
            "acceptance_criteria": ["tests/test_parser.py passes"],
            "verification": ["pytest tests/test_parser.py"],
            "scope": ["app/parser.py"],
        },
    }
    worker = build_query(RetrieveRequest(profile="worker", **base))
    reviewer = build_query(
        RetrieveRequest(
            profile="reviewer",
            review={
                "worker_output": "FIX APPLIED: changed app/parser.py compute_total",
                "changed_files": ["app/parser.py"],
            },
            **base,
        )
    )
    assert set(worker.fingerprint.components) == {"task"}
    assert set(reviewer.fingerprint.components) == {"task", "evidence"}
    assert reviewer.rerank_text.startswith("Verify:")
    assert "app/parser.py" in reviewer.paths
    assert "compute_total" in worker.identifiers
