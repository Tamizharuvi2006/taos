from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from taos.core.memory.false_memory_guard import verify_memory_claim
from taos.core.memory.memory_extractor import extract_memories_from_text
from taos.core.memory.memory_retriever import MemoryRetriever
from taos.core.memory.project_context_selector import select_project_context
from taos.core.memory.project_memory_extractor import extract_project_memory
from taos.core.memory.project_memory_store import MultiChatProjectMemoryStore
from taos.core.memory.user_memory_store import UserMemoryStore

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "qa" / "memory_qa_cases.json"
JSON_REPORT = ROOT / "QA_RESULTS_MEMORY.json"
MD_REPORT = ROOT / "QA_RESULTS_MEMORY.md"


def load_cases(path: Path = CASES_PATH) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_mock_case(case: Dict[str, Any], *, memory_store: UserMemoryStore, project_store: MultiChatProjectMemoryStore) -> Dict[str, Any]:
    user_id = "qa_user"
    text = str(case["input"])
    checks: Dict[str, bool] = {}
    notes: List[str] = []

    if case.get("expected_memory_saved") is not None:
        extraction = extract_memories_from_text(text, user_id=user_id, source_chat_id="chat_qa", source_message_id=case["id"])
        for memory in extraction.memories:
            memory_store.create(memory)
        checks["memory_saved_matches_expected"] = bool(extraction.memories) == bool(case["expected_memory_saved"])
        if extraction.blocked_reason:
            notes.append(extraction.blocked_reason)

    if case.get("expected_memory_deleted"):
        saved = extract_memories_from_text("remember that I prefer concise answers", user_id=user_id)
        for memory in saved.memories:
            memory_store.create(memory)
        delete_query = extract_memories_from_text(text, user_id=user_id).forget_query
        matches = memory_store.retrieve(user_id, delete_query or text, top_k=10)
        deleted = 0
        for memory in matches:
            if memory_store.delete(user_id, memory.id):
                deleted += 1
        checks["memory_deleted"] = deleted > 0

    if case.get("expected_project_memory_used"):
        record = extract_project_memory("TAOS Phase 140 complete. Next: Phase 141 context pack.", user_id=user_id)
        assert record is not None
        project_store.upsert(record)
        selected = select_project_context(project_store, user_id=user_id, query=text)
        checks["project_memory_used"] = selected.record is not None and selected.confidence > 0

    if case.get("expected_no_fake_memory"):
        retriever = MemoryRetriever(memory_store)
        used = retriever.memory_used_summary(user_id, text)
        check = verify_memory_claim("favorite food", memory_store.list(user_id))
        checks["no_fake_memory"] = not used and not check.allowed

    passed = all(checks.values()) if checks else False
    return {
        "id": case["id"],
        "input": text,
        "passed": passed,
        "checks": checks,
        "notes": notes,
    }


def run_mock(max_cases: int | None = None) -> Dict[str, Any]:
    memory_store = UserMemoryStore()
    project_store = MultiChatProjectMemoryStore()
    cases = load_cases()
    if max_cases:
        cases = cases[:max_cases]
    results = [run_mock_case(case, memory_store=memory_store, project_store=project_store) for case in cases]
    passed = sum(1 for result in results if result["passed"])
    return {
        "mode": "mock",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / max(1, len(results)), 3),
        "cases": results,
    }


def write_reports(report: Dict[str, Any]) -> None:
    JSON_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# TAOS Memory QA Results",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Passed: **{report['passed']}/{report['total']}**",
        f"- Pass rate: **{report['pass_rate']}**",
        "",
        "| Case | Passed | Checks |",
        "| --- | ---: | --- |",
    ]
    for case in report["cases"]:
        checks = ", ".join(f"{key}={value}" for key, value in case["checks"].items())
        lines.append(f"| `{case['id']}` | {'yes' if case['passed'] else 'no'} | {checks} |")
    MD_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TAOS memory QA regression cases.")
    parser.add_argument("--mock", action="store_true", help="Run deterministic local mock QA.")
    parser.add_argument("--live", action="store_true", help="Reserved for live API memory QA.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--max-cases", type=int, default=0)
    args = parser.parse_args()

    if args.live:
        raise SystemExit("Live memory QA is intentionally explicit but not implemented in Phase 145 mock gate.")
    report = run_mock(max_cases=args.max_cases or None)
    write_reports(report)
    print(MD_REPORT.read_text(encoding="utf-8"))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
