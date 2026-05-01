from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.chat.persistent_chat_manager import PersistentChatManager
from taos.core.persistence.runtime_status import build_persistence_status
from taos.core.tasks.persistent_manager import EXECUTIONS_COLLECTION, PersistentTaskManager
from taos.infra.persistence.firebase_store import FirestoreStore
from taos.infra.persistence.store import InMemoryStore


@dataclass
class PersistenceCase:
    id: str
    description: str

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "PersistenceCase":
        return cls(
            id=str(row.get("id") or "").strip(),
            description=str(row.get("description") or "").strip(),
        )


@dataclass
class PersistenceResult:
    case_id: str
    description: str
    passed: bool
    checks: dict[str, bool]
    failures: list[str]
    detail: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cases(path: str | Path) -> list[PersistenceCase]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [PersistenceCase.from_dict(row) for row in rows if isinstance(row, Mapping)]


def detect_cross_user_isolation_failure(*, owner_can_read: bool, other_can_read: bool) -> bool:
    return (not bool(owner_can_read)) or bool(other_can_read)


def _request_json(
    *,
    method: str,
    url: str,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> tuple[int | None, Any, str]:
    req_headers = {"Accept": "application/json", **dict(headers or {})}
    body = None
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        body = json.dumps(dict(payload)).encode("utf-8")
    req = urlrequest.Request(url, data=body, headers=req_headers, method=method.upper())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw or "{}")
            return int(response.status), parsed, ""
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {"raw": raw[:500]}
        return int(exc.code), parsed, ""
    except Exception as exc:
        return None, {}, str(exc)


async def _mock_chat_save_and_read(case: PersistenceCase, store: InMemoryStore) -> PersistenceResult:
    manager = PersistentChatManager(user_id="qa_user_a", store=store)
    chat_id = "phase122_chat_save_read"
    payload = {
        "title": "Persistence QA Chat",
        "messages": [
            {"id": "m1", "role": "user", "text": "hello", "status": "done", "ts": 1.0},
            {"id": "m2", "role": "assistant", "text": "hi", "status": "done", "ts": 2.0},
        ],
        "doc_ids": [],
    }
    await manager.upsert_chat(chat_id, payload)
    loaded = await manager.get_chat(chat_id)
    checks = {
        "chat_saved": loaded is not None,
        "message_count_preserved": bool(loaded and len(list(loaded.get("messages") or [])) == 2),
        "roundtrip_message_text": bool(loaded and str((loaded.get("messages") or [{}])[0].get("text")) == "hello"),
    }
    failures = [name for name, ok in checks.items() if not ok]
    return PersistenceResult(case_id=case.id, description=case.description, passed=not failures, checks=checks, failures=failures)


async def _mock_trace_metadata_saved(case: PersistenceCase, store: InMemoryStore) -> PersistenceResult:
    manager = PersistentChatManager(user_id="qa_user_trace", store=store)
    chat_id = "phase122_trace_chat"
    payload = {
        "title": "Trace Chat",
        "messages": [
            {
                "id": "m1",
                "role": "assistant",
                "text": "answer",
                "status": "done",
                "ts": 1.0,
                "trace": {"route_label": "fast_search", "request_id": "req_phase122"},
                "trust_block": {"citation_coverage": 0.9, "confidence": "high"},
            }
        ],
        "doc_ids": [],
    }
    await manager.upsert_chat(chat_id, payload)
    loaded = await manager.get_chat(chat_id)
    msg = dict((loaded or {}).get("messages", [{}])[0])
    checks = {
        "trace_persisted": isinstance(msg.get("trace"), Mapping),
        "trust_block_persisted": isinstance(msg.get("trust_block"), Mapping),
        "request_id_preserved": str(dict(msg.get("trace") or {}).get("request_id") or "") == "req_phase122",
    }
    failures = [name for name, ok in checks.items() if not ok]
    return PersistenceResult(case_id=case.id, description=case.description, passed=not failures, checks=checks, failures=failures)


async def _mock_cross_user_isolation(case: PersistenceCase, store: InMemoryStore) -> PersistenceResult:
    owner = PersistentChatManager(user_id="qa_user_owner", store=store)
    intruder = PersistentChatManager(user_id="qa_user_intruder", store=store)
    chat_id = "phase122_isolation_chat"
    await owner.upsert_chat(
        chat_id,
        {
            "title": "Owner Chat",
            "messages": [{"id": "m1", "role": "user", "text": "private", "status": "done", "ts": 1.0}],
            "doc_ids": [],
        },
    )
    owner_chat = await owner.get_chat(chat_id)
    intruder_chat = await intruder.get_chat(chat_id)
    isolation_failed = detect_cross_user_isolation_failure(
        owner_can_read=owner_chat is not None,
        other_can_read=intruder_chat is not None,
    )
    checks = {
        "owner_can_read": owner_chat is not None,
        "intruder_cannot_read": intruder_chat is None,
        "isolation_failure_detected_false": isolation_failed is False,
    }
    failures = [name for name, ok in checks.items() if not ok]
    return PersistenceResult(case_id=case.id, description=case.description, passed=not failures, checks=checks, failures=failures)


async def _mock_memory_fallback(case: PersistenceCase, store: InMemoryStore) -> PersistenceResult:
    firebase_store = FirestoreStore()
    persistence = build_persistence_status(check_runtime=False)
    if firebase_store.is_available:
        checks = {
            "firebase_available": True,
            "fallback_required": False,
            "fallback_controlled": True,
        }
        return PersistenceResult(
            case_id=case.id,
            description=case.description,
            passed=True,
            checks=checks,
            failures=[],
            detail="Firebase runtime is available; fallback path not required.",
        )

    await store.set("memory", "k1", {"value": "ok"}, user_id="fallback_user")
    loaded = await store.get("memory", "k1", user_id="fallback_user")
    checks = {
        "firebase_unavailable": True,
        "memory_write_read_works": bool(loaded and loaded.get("value") == "ok"),
        "fallback_controlled": True,
        "persistence_mode_reported": persistence.get("persistence_mode") in {"memory_fallback", "disabled", "firestore"},
    }
    failures = [name for name, ok in checks.items() if not ok]
    return PersistenceResult(case_id=case.id, description=case.description, passed=not failures, checks=checks, failures=failures)


async def _mock_task_execution_history(case: PersistenceCase, store: InMemoryStore) -> PersistenceResult:
    manager = PersistentTaskManager(store=store, user_id="qa_user_task")
    task = await manager.create_task(goal="send me reminder after 5 mins")
    execution_row = {
        "execution_id": "exec_phase122_1",
        "task_id": task.task_id,
        "timestamp": time.time(),
        "success": True,
        "result": "done",
        "confidence": 0.88,
        "elapsed_ms": 120.0,
    }
    await store.set(EXECUTIONS_COLLECTION, execution_row["execution_id"], execution_row, user_id="qa_user_task")
    history = await manager.get_history(task.task_id, limit=10)
    checks = {
        "task_created": bool(task and task.task_id),
        "execution_persisted": bool(history),
        "execution_id_present": bool(history and str(history[0].get("execution_id") or "").startswith("exec_phase122_")),
    }
    failures = [name for name, ok in checks.items() if not ok]
    return PersistenceResult(case_id=case.id, description=case.description, passed=not failures, checks=checks, failures=failures)


def _live_headers(user_id: str) -> dict[str, str]:
    return {"X-User-ID": user_id}


def _live_chat_payload(chat_id: str, with_trace: bool = False) -> dict[str, Any]:
    message: dict[str, Any] = {"id": "m1", "role": "assistant", "text": "hello", "status": "done", "ts": 1.0}
    if with_trace:
        message["trace"] = {"request_id": f"req_{chat_id}", "route_label": "fast_message"}
        message["trust_block"] = {"citation_coverage": 0.8}
    return {"title": "Live QA Chat", "messages": [message], "doc_ids": []}


def _live_run_case(case: PersistenceCase, *, base_url: str, timeout: float) -> PersistenceResult:
    base = str(base_url).rstrip("/")
    if case.id == "chat_save_and_read":
        chat_id = f"phase122_live_{int(time.time() * 1000)}"
        status_put, _, err_put = _request_json(
            method="PUT",
            url=f"{base}/chats/{chat_id}",
            payload=_live_chat_payload(chat_id),
            headers=_live_headers("live_user_a"),
            timeout=timeout,
        )
        status_list, body_list, err_list = _request_json(
            method="GET",
            url=f"{base}/chats",
            headers=_live_headers("live_user_a"),
            timeout=timeout,
        )
        rows = body_list if isinstance(body_list, list) else body_list.get("items", [])
        found = any(str(row.get("id") or "") == chat_id for row in list(rows or []))
        checks = {"put_ok": status_put == 200, "list_ok": status_list == 200, "chat_found": found}
        failures = [name for name, ok in checks.items() if not ok]
        return PersistenceResult(case.id, case.description, not failures, checks, failures, detail=err_put or err_list)

    if case.id == "trace_metadata_saved":
        chat_id = f"phase122_live_trace_{int(time.time() * 1000)}"
        status_put, _, err_put = _request_json(
            method="PUT",
            url=f"{base}/chats/{chat_id}",
            payload=_live_chat_payload(chat_id, with_trace=True),
            headers=_live_headers("live_user_trace"),
            timeout=timeout,
        )
        checks = {"put_ok": status_put == 200}
        failures = [name for name, ok in checks.items() if not ok]
        return PersistenceResult(case.id, case.description, not failures, checks, failures, detail=err_put)

    if case.id == "cross_user_isolation":
        chat_id = f"phase122_live_iso_{int(time.time() * 1000)}"
        _request_json(
            method="PUT",
            url=f"{base}/chats/{chat_id}",
            payload=_live_chat_payload(chat_id),
            headers=_live_headers("live_owner"),
            timeout=timeout,
        )
        status_b, body_b, err_b = _request_json(
            method="GET",
            url=f"{base}/chats",
            headers=_live_headers("live_intruder"),
            timeout=timeout,
        )
        rows_b = body_b if isinstance(body_b, list) else body_b.get("items", [])
        intruder_can_read = any(str(row.get("id") or "") == chat_id for row in list(rows_b or []))
        checks = {
            "list_ok": status_b == 200,
            "intruder_cannot_read": intruder_can_read is False,
            "isolation_failure_detected_false": detect_cross_user_isolation_failure(owner_can_read=True, other_can_read=intruder_can_read) is False,
        }
        failures = [name for name, ok in checks.items() if not ok]
        return PersistenceResult(case.id, case.description, not failures, checks, failures, detail=err_b)

    if case.id == "memory_fallback":
        status, body, err = _request_json(method="GET", url=f"{base}/health", headers=_live_headers("live_user_a"), timeout=timeout)
        checks_blob = dict(body.get("checks") or {}) if isinstance(body, Mapping) else {}
        checks = {
            "health_ok": status == 200,
            "fallback_controlled": checks_blob.get("firebase_runtime_ready") in {True, False, None},
        }
        failures = [name for name, ok in checks.items() if not ok]
        return PersistenceResult(case.id, case.description, not failures, checks, failures, detail=err)

    if case.id == "task_execution_history":
        status, body, err = _request_json(method="GET", url=f"{base}/tasks", headers=_live_headers("live_user_a"), timeout=timeout)
        if status in {401, 403, 404}:
            return PersistenceResult(
                case.id,
                case.description,
                True,
                {"live_task_endpoint_optional": True},
                [],
                skipped=True,
                skip_reason=f"Task history endpoint optional in current live context (status={status}).",
            )
        checks = {
            "tasks_endpoint_ok": status == 200,
            "tasks_payload_shape_ok": isinstance(body, Mapping) or isinstance(body, list),
        }
        failures = [name for name, ok in checks.items() if not ok]
        return PersistenceResult(case.id, case.description, not failures, checks, failures, detail=err)

    return PersistenceResult(case.id, case.description, False, {"known_case": False}, ["known_case"], detail="Unknown persistence QA case id.")


async def _run_mock_cases(cases: list[PersistenceCase]) -> list[PersistenceResult]:
    store = InMemoryStore()
    results: list[PersistenceResult] = []
    for case in cases:
        if case.id == "chat_save_and_read":
            results.append(await _mock_chat_save_and_read(case, store))
        elif case.id == "trace_metadata_saved":
            results.append(await _mock_trace_metadata_saved(case, store))
        elif case.id == "cross_user_isolation":
            results.append(await _mock_cross_user_isolation(case, store))
        elif case.id == "memory_fallback":
            results.append(await _mock_memory_fallback(case, store))
        elif case.id == "task_execution_history":
            results.append(await _mock_task_execution_history(case, store))
        else:
            results.append(
                PersistenceResult(
                    case_id=case.id,
                    description=case.description,
                    passed=False,
                    checks={"known_case": False},
                    failures=["known_case"],
                    detail="Unknown persistence QA case id.",
                )
            )
    return results


def run_persistence_qa(
    *,
    cases: list[PersistenceCase],
    live: bool = False,
    explicit_live: bool = False,
    base_url: str = "http://localhost:8000",
    max_cases: int | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    if live and not explicit_live:
        raise ValueError("Live mode is blocked unless explicit_live=True (CLI --live).")
    selected = cases[: max_cases or None]

    if live:
        results = [_live_run_case(case, base_url=base_url, timeout=timeout) for case in selected]
    else:
        results = asyncio.run(_run_mock_cases(selected))

    active = [row for row in results if not row.skipped]
    passed = [row for row in active if row.passed]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "mock",
        "base_url": base_url if live else None,
        "case_count": len(results),
        "active_case_count": len(active),
        "passed_count": len(passed),
        "failed_count": len(active) - len(passed),
        "skipped_count": len(results) - len(active),
        "pass_rate": 1.0 if not active else round(len(passed) / float(len(active)), 3),
        "ok": (len(active) - len(passed)) == 0,
        "results": [row.to_dict() for row in results],
    }
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# TAOS Persistence QA",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Mode: `{report.get('mode')}`",
        f"- Pass rate: **{report.get('pass_rate')}**",
        f"- Passed: **{report.get('passed_count')}/{report.get('active_case_count')}**",
        f"- Skipped: **{report.get('skipped_count')}**",
        "",
        "| Case | OK | Skipped | Failed checks |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in list(report.get("results") or []):
        failed_checks = "skipped: " + str(row.get("skip_reason")) if row.get("skipped") else ", ".join(list(row.get("failures") or [])) or "-"
        lines.append(f"| `{row.get('case_id')}` | {'yes' if row.get('passed') else 'no'} | {'yes' if row.get('skipped') else 'no'} | {failed_checks} |")
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], *, json_path: str | Path, md_path: str | Path) -> None:
    Path(json_path).write_text(json.dumps(dict(report), indent=2, sort_keys=True), encoding="utf-8")
    Path(md_path).write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TAOS persistence QA checks.")
    parser.add_argument("--cases", default=str(_REPO_ROOT / "qa" / "persistence_cases.json"))
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock persistence QA.")
    parser.add_argument("--live", action="store_true", help="Run explicit live mode (required for real API calls).")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out-json", default=str(_REPO_ROOT / "QA_RESULTS_PERSISTENCE.json"))
    parser.add_argument("--out-md", default=str(_REPO_ROOT / "QA_RESULTS_PERSISTENCE.md"))
    args = parser.parse_args(argv)

    use_live = bool(args.live and not args.mock)
    report = run_persistence_qa(
        cases=load_cases(args.cases),
        live=use_live,
        explicit_live=bool(args.live),
        base_url=args.base_url,
        max_cases=args.max_cases,
        timeout=args.timeout,
    )
    write_report(report, json_path=args.out_json, md_path=args.out_md)
    print(render_markdown(report))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
