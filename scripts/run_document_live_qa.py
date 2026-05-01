from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class DocumentQACase:
    id: str
    file: str
    question: str
    mode: str = "general_doc_assist"
    expect_uncertain: bool = False


@dataclass
class DocumentQAResult:
    case_id: str
    question: str
    mode: str
    file: str
    passed: bool
    skipped: bool = False
    skip_reason: str | None = None
    latency_ms: float = 0.0
    checks: Dict[str, bool] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    answer_preview: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_cases(path: str | Path) -> List[DocumentQACase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [DocumentQACase(**row) for row in raw]


def _mock_payload(case: DocumentQACase) -> Tuple[int, Dict[str, Any], float]:
    if case.mode == "important_questions":
        answer = (
            "Answer\n"
            "Important 16 mark questions:\n"
            "1. Explain RAG architecture with retrieval, chunking, and grounded answer generation.\n"
            "2. Compare vector database choices for RAG evaluation.\n\n"
            "Why this answer\n"
            "- The document lists long answer topics for RAG architecture, vector databases, evaluation, and safety.\n\n"
            "Confidence\nMedium, because the answer is grounded to the uploaded notes.\n\n"
            "Sources\n[S1] sample_marks_exam_notes.pdf chunk 1"
        )
    elif case.expect_uncertain:
        answer = (
            "Answer\n"
            "The document does not provide the CEO name.\n\n"
            "Why this answer\n"
            "- The retrieved policy chunk discusses eligibility only.\n\n"
            "Confidence\nHigh for absence in retrieved evidence.\n\n"
            "What to treat carefully\n"
            "- This does not prove the CEO name does not exist elsewhere."
        )
    else:
        answer = (
            "Answer\n"
            "The document says eligibility requires an active account, verified email, and accepted terms.\n\n"
            "Why this answer\n"
            "- The strongest retrieved chunk states those eligibility requirements directly.\n\n"
            "Confidence\nHigh, because the answer is grounded in the document text.\n\n"
            "Sources\n[S1] document chunk 1"
        )
    payload = {
        "answer": answer,
        "mode": case.mode,
        "confidence": 0.82,
        "sources": [
            {
                "doc_id": "mock_doc",
                "chunk_id": "chunk_1",
                "chunk_index": 0,
                "page_start": 1,
                "page_end": 1,
                "score": 0.91,
            }
        ],
        "validation": {
            "grounded": not case.expect_uncertain,
            "score": 0.86 if not case.expect_uncertain else 0.72,
            "unsupported_critical_claims": 0,
        },
        "cached": case.id.endswith("summary"),
        "warnings": ["Answer limited to retrieved document context."] if case.expect_uncertain else [],
        "metadata": {
            "retrieval_mode": "mock",
            "confidence_tier": "high" if not case.expect_uncertain else "medium",
            "source_count": 1,
            "grounding_level": "strong" if not case.expect_uncertain else "moderate",
            "cache_hit": case.id.endswith("summary"),
        },
    }
    return 200, payload, 250.0


def _post_json(url: str, payload: Mapping[str, Any], timeout: float) -> Tuple[int, Dict[str, Any], float]:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body or "{}"), (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError:
            parsed = {"error": body}
        return exc.code, parsed, (time.perf_counter() - started) * 1000


def _live_payload(case: DocumentQACase, base_url: str, timeout: float) -> Tuple[int, Dict[str, Any], float]:
    # Live document upload flows require deployment-specific auth/storage. This runner
    # targets already-attached doc fixture ids when supplied by external harnesses.
    payload = {
        "question": case.question,
        "doc_ids": [Path(case.file).stem],
        "mode": case.mode,
    }
    return _post_json(f"{base_url.rstrip('/')}/api/ask", payload, timeout)


def validate_case(case: DocumentQACase, payload: Mapping[str, Any], status_code: int, latency_ms: float) -> DocumentQAResult:
    answer = str(payload.get("answer") or payload.get("result") or "")
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), Mapping) else {}
    checks = {
        "status_code_ok": status_code == 200,
        "answer_not_empty": bool(answer.strip()),
        "source_chunk_present": bool(sources and (sources[0].get("chunk_id") or sources[0].get("chunk_index") is not None)),
        "metadata_present": bool(metadata),
        "cache_metadata_present": "cache_hit" in metadata or "retrieval_mode" in metadata or "cached" in payload,
        "unsupported_critical_zero": int(validation.get("unsupported_critical_claims") or 0) == 0,
        "important_questions_structure": True,
        "unknown_answer_uncertain": True,
    }
    if case.mode == "important_questions":
        checks["important_questions_structure"] = "16 mark" in answer.lower() or "important" in answer.lower()
    if case.expect_uncertain:
        checks["unknown_answer_uncertain"] = any(
            phrase in answer.lower()
            for phrase in ("does not provide", "not found", "not in the document", "not enough")
        )
    failures = [name for name, ok in checks.items() if not ok]
    return DocumentQAResult(
        case_id=case.id,
        question=case.question,
        mode=case.mode,
        file=case.file,
        passed=not failures,
        latency_ms=round(float(latency_ms or 0.0), 2),
        checks=checks,
        failures=failures,
        answer_preview=answer[:180],
    )


def run_cases(
    cases: List[DocumentQACase],
    *,
    live: bool = False,
    base_url: str = "http://localhost:8000",
    max_cases: int | None = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    selected = cases[: max_cases or None]
    results: List[DocumentQAResult] = []
    for case in selected:
        fixture_path = _REPO_ROOT / case.file
        if not fixture_path.exists():
            results.append(
                DocumentQAResult(
                    case_id=case.id,
                    question=case.question,
                    mode=case.mode,
                    file=case.file,
                    passed=True,
                    skipped=True,
                    skip_reason=f"Missing fixture: {case.file}",
                )
            )
            continue
        status, payload, latency = _live_payload(case, base_url, timeout) if live else _mock_payload(case)
        if live and status in {404, 409}:
            error_code = str(payload.get("error_code") or payload.get("code") or "document_fixture_unavailable")
            results.append(
                DocumentQAResult(
                    case_id=case.id,
                    question=case.question,
                    mode=case.mode,
                    file=case.file,
                    passed=True,
                    skipped=True,
                    skip_reason=f"Live fixture document unavailable: {error_code}",
                    latency_ms=round(float(latency or 0.0), 2),
                )
            )
            continue
        results.append(validate_case(case, payload, status_code=status, latency_ms=latency))
    active = [result for result in results if not result.skipped]
    passed = [result for result in active if result.passed]
    pass_rate = 1.0 if not active else round(len(passed) / float(len(active)), 3)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "mock",
        "base_url": base_url if live else None,
        "case_count": len(results),
        "active_case_count": len(active),
        "passed_count": len(passed),
        "failed_count": len(active) - len(passed),
        "skipped_count": len(results) - len(active),
        "pass_rate": pass_rate,
        "results": [result.to_dict() for result in results],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# TAOS Document QA Matrix",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Mode: `{report.get('mode')}`",
        f"- Pass rate: **{report.get('pass_rate')}**",
        f"- Passed: **{report.get('passed_count')}/{report.get('active_case_count')}**",
        f"- Skipped: **{report.get('skipped_count')}**",
        "",
        "| Case | Mode | OK | Latency ms | Failed checks |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in list(report.get("results") or []):
        failed = "skipped: " + str(row.get("skip_reason")) if row.get("skipped") else ", ".join(row.get("failures") or []) or "-"
        lines.append(
            f"| `{row.get('case_id')}` | {row.get('mode')} | {'yes' if row.get('passed') else 'no'} | {row.get('latency_ms')} | {failed} |"
        )
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], *, json_path: str | Path, md_path: str | Path) -> None:
    Path(json_path).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    Path(md_path).write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TAOS document QA matrix.")
    parser.add_argument("--cases", default=str(_REPO_ROOT / "qa" / "document_qa_cases.json"))
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock mode (default).")
    parser.add_argument("--live", action="store_true", help="Run guarded live mode.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out-json", default=str(_REPO_ROOT / "QA_RESULTS_DOCUMENT.json"))
    parser.add_argument("--out-md", default=str(_REPO_ROOT / "QA_RESULTS_DOCUMENT.md"))
    args = parser.parse_args()
    report = run_cases(
        load_cases(args.cases),
        live=bool(args.live),
        base_url=args.base_url,
        max_cases=args.max_cases,
        timeout=args.timeout,
    )
    write_report(report, json_path=args.out_json, md_path=args.out_md)
    print(render_markdown(report))
    return 0 if int(report.get("failed_count") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
