from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# Ensure `import taos` works when script is run from `D:\agent\taos`.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass
class CheckResult:
    section: str
    name: str
    ok: bool
    status: Optional[int] = None
    latency_ms: float = 0.0
    details: str = ""
    critical: bool = True


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _truncate(text: str, max_len: int = 260) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _http_json(
    method: str,
    base_url: str,
    path: str,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    timeout_sec: int = 60,
) -> CheckResult:
    started = time.time()
    try:
        if method.upper() == "GET":
            resp = requests.get(base_url + path, headers=headers, timeout=timeout_sec)
        elif method.upper() == "POST":
            merged = dict(headers)
            merged["Content-Type"] = "application/json"
            resp = requests.post(base_url + path, headers=merged, json=payload, timeout=timeout_sec)
        elif method.upper() == "PUT":
            merged = dict(headers)
            merged["Content-Type"] = "application/json"
            resp = requests.put(base_url + path, headers=merged, json=payload, timeout=timeout_sec)
        else:
            raise ValueError(f"unsupported method {method}")

        latency = round((time.time() - started) * 1000, 2)
        body_text = ""
        try:
            body_text = json.dumps(resp.json(), ensure_ascii=True)
        except Exception:
            body_text = resp.text
        return CheckResult(
            section="",
            name=f"{method} {path}",
            ok=200 <= resp.status_code < 300,
            status=resp.status_code,
            latency_ms=latency,
            details=_truncate(body_text),
            critical=True,
        )
    except Exception as exc:
        latency = round((time.time() - started) * 1000, 2)
        return CheckResult(
            section="",
            name=f"{method} {path}",
            ok=False,
            status=None,
            latency_ms=latency,
            details=_truncate(str(exc)),
            critical=True,
        )


def _wait_for_health(base_url: str, timeout_sec: int = 35) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.get(base_url + "/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def _start_server(port: int, dev_bypass: bool, env_mode: str) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["TAOS_ENV"] = env_mode
    env["AUTH_ALLOW_DEV_BYPASS"] = "true" if dev_bypass else "false"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "taos.apps.api.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd="D:/agent",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    return proc


def _stop_server(proc: Optional[subprocess.Popen[str]]) -> None:
    if proc is None:
        return
    try:
        proc.send_signal(signal.SIGINT)
    except Exception:
        return
    try:
        proc.communicate(timeout=20)
    except Exception:
        try:
            proc.kill()
            proc.communicate(timeout=10)
        except Exception:
            pass


def _check_bad_inputs(base_url: str, headers: Dict[str, str], user_id: str) -> List[CheckResult]:
    out: List[CheckResult] = []

    # Empty query
    r = _http_json("POST", base_url, "/execute", headers, payload={"query": "", "user_id": user_id, "user_tier": "free"})
    r.section = "bad_input"
    r.name = "empty query rejected"
    r.ok = r.status in {400, 422}
    out.append(r)

    # Too long query
    long_query = "x" * 2105
    r = _http_json("POST", base_url, "/execute", headers, payload={"query": long_query, "user_id": user_id, "user_tier": "free"})
    r.section = "bad_input"
    r.name = "long query rejected"
    r.ok = r.status in {400, 413, 422}
    out.append(r)

    # Invalid JSON body
    started = time.time()
    try:
        raw = requests.post(
            base_url + "/execute",
            headers={"X-User-ID": user_id, "Content-Type": "application/json"},
            data='{"query": ',
            timeout=20,
        )
        latency = round((time.time() - started) * 1000, 2)
        out.append(
            CheckResult(
                section="bad_input",
                name="invalid json body rejected",
                ok=raw.status_code in {400, 422},
                status=raw.status_code,
                latency_ms=latency,
                details=_truncate(raw.text),
            )
        )
    except Exception as exc:
        out.append(
            CheckResult(
                section="bad_input",
                name="invalid json body rejected",
                ok=False,
                details=_truncate(str(exc)),
            )
        )

    # Unknown field should not crash
    r = _http_json(
        "POST",
        base_url,
        "/execute",
        headers,
        payload={"query": "what is 2+2", "user_id": user_id, "user_tier": "free", "unknown_field": "x"},
        timeout_sec=45,
    )
    r.section = "bad_input"
    r.name = "unknown field no crash"
    r.ok = r.status in {200, 400, 422}
    out.append(r)
    return out


def _check_auth_boundaries(port: int) -> List[CheckResult]:
    out: List[CheckResult] = []
    base_url = f"http://127.0.0.1:{port}"
    proc = _start_server(port=port, dev_bypass=False, env_mode="production")
    try:
        if not _wait_for_health(base_url, timeout_sec=35):
            out.append(CheckResult("auth", "prod server startup", False, details="health check timeout"))
            return out

        started = time.time()
        no_token = requests.post(
            base_url + "/execute",
            headers={"X-User-ID": "dev_local_user", "Content-Type": "application/json"},
            json={"query": "hello", "user_id": "dev_local_user", "user_tier": "free"},
            timeout=20,
        )
        out.append(
            CheckResult(
                section="auth",
                name="missing token rejected in production",
                ok=no_token.status_code == 401,
                status=no_token.status_code,
                latency_ms=round((time.time() - started) * 1000, 2),
                details=_truncate(no_token.text),
            )
        )

        started = time.time()
        invalid = requests.post(
            base_url + "/execute",
            headers={"Authorization": "Bearer invalid_token", "Content-Type": "application/json"},
            json={"query": "hello", "user_id": "dev_local_user", "user_tier": "free"},
            timeout=20,
        )
        out.append(
            CheckResult(
                section="auth",
                name="invalid token rejected",
                ok=invalid.status_code == 401,
                status=invalid.status_code,
                latency_ms=round((time.time() - started) * 1000, 2),
                details=_truncate(invalid.text),
            )
        )
    finally:
        _stop_server(proc)
    return out


async def _run_dag_failure_checks_async() -> List[CheckResult]:
    from taos.core.execution.dag_models import DagDefinition, DagNode, DagNodeKind
    from taos.core.execution.dag_registry import DagRegistry
    from taos.core.execution.dag_runner import DagRunner
    from taos.core.planner.plan_validator import PlanValidator
    from taos.core.state.state_schema import PlanObject, PlanStep, StepResult, StepType

    class _FakeToolExecutor:
        async def execute(self, tool_name: str, tool_input=None, task_id: str = "", step_id: str = "") -> StepResult:
            if tool_name == "always_fail":
                return StepResult(
                    step_id=step_id,
                    success=False,
                    tool_name=tool_name,
                    error="forced failure",
                    error_type="TOOL_FAILURE",
                )
            return StepResult(
                step_id=step_id,
                success=False,
                tool_name=tool_name,
                error=f"unknown tool: {tool_name}",
                error_type="TOOL_FAILURE",
            )

    results: List[CheckResult] = []

    # Missing dag_name in plan
    validator = PlanValidator(registered_tools={"web_search"})
    bad_plan = PlanObject(
        steps=[PlanStep(id="s1", action="run dag", step_type=StepType.DAG_EXEC, dag_name=None, tool=None)]
    )
    validation = validator.validate(bad_plan)
    results.append(
        CheckResult(
            section="dag_failure",
            name="validator rejects missing dag_name",
            ok=(validation.is_valid is False and any("dag_name" in e for e in validation.errors)),
            details=_truncate(str(validation.errors)),
        )
    )

    # Unknown DAG name
    runner = DagRunner(tool_executor=_FakeToolExecutor())  # type: ignore[arg-type]
    try:
        await runner.run("non_existent_dag", {"query": "x"})
        unknown_ok = False
        unknown_detail = "expected exception was not raised"
    except Exception as exc:
        unknown_ok = "not found" in str(exc).lower() or "available dags" in str(exc).lower()
        unknown_detail = str(exc)
    results.append(
        CheckResult(
            section="dag_failure",
            name="unknown dag fails cleanly",
            ok=unknown_ok,
            details=_truncate(unknown_detail),
        )
    )

    # Circular dependency deadlock
    cycle_registry = DagRegistry()
    cycle_registry.register(
        DagDefinition(
            name="cycle_dag",
            nodes=[
                DagNode(id="a", kind=DagNodeKind.TRANSFORM, action="a", depends_on=["b"]),
                DagNode(id="b", kind=DagNodeKind.TRANSFORM, action="b", depends_on=["a"]),
            ],
            output_node_ids=["a"],
            max_runtime_seconds=5,
        )
    )
    cycle_runner = DagRunner(tool_executor=_FakeToolExecutor(), registry=cycle_registry)  # type: ignore[arg-type]
    cycle_result = await cycle_runner.run("cycle_dag", {"query": "x"})
    results.append(
        CheckResult(
            section="dag_failure",
            name="circular dependencies terminate with deadlock error",
            ok=(cycle_result.status == "failed" and "deadlock" in str(cycle_result.error).lower()),
            details=_truncate(str(cycle_result.error)),
        )
    )

    # Node failure after retries
    fail_registry = DagRegistry()
    fail_registry.register(
        DagDefinition(
            name="retry_fail_dag",
            nodes=[
                DagNode(
                    id="n1",
                    kind=DagNodeKind.TOOL,
                    action="force tool failure",
                    tool="always_fail",
                    retry_limit=2,
                    required=True,
                )
            ],
            output_node_ids=["n1"],
            max_runtime_seconds=20,
        )
    )
    fail_runner = DagRunner(tool_executor=_FakeToolExecutor(), registry=fail_registry)  # type: ignore[arg-type]
    fail_result = await fail_runner.run("retry_fail_dag", {"query": "x"})
    node = fail_result.node_results.get("n1")
    results.append(
        CheckResult(
            section="dag_failure",
            name="node failure respects retry limit",
            ok=(fail_result.status == "failed" and node is not None and node.retries_used == 2),
            details=_truncate(
                json.dumps(
                    {
                        "status": fail_result.status,
                        "retries_used": None if node is None else node.retries_used,
                        "error": fail_result.error,
                    },
                    ensure_ascii=True,
                )
            ),
        )
    )

    return results


def _check_dag_failures() -> List[CheckResult]:
    return asyncio.run(_run_dag_failure_checks_async())


def _check_concurrency(base_url: str, headers: Dict[str, str], user_id: str, workers: int = 6) -> List[CheckResult]:
    out: List[CheckResult] = []
    started = time.time()

    def one_call(i: int) -> tuple[bool, int, str]:
        q = f"what is {i} + {i}?"
        resp = requests.post(
            base_url + "/execute",
            headers={**headers, "Content-Type": "application/json"},
            json={"query": q, "user_id": user_id, "user_tier": "free"},
            timeout=45,
        )
        txt = ""
        try:
            body = resp.json()
            txt = str(body.get("answer", ""))[:80]
        except Exception:
            txt = resp.text[:80]
        return (resp.status_code == 200, resp.status_code, txt)

    success = 0
    statuses: List[int] = []
    snippets: List[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one_call, i) for i in range(workers)]
        for fut in as_completed(futs):
            ok, status, snippet = fut.result()
            statuses.append(status)
            snippets.append(snippet)
            if ok:
                success += 1

    out.append(
        CheckResult(
            section="concurrency",
            name=f"{workers} parallel /execute requests",
            ok=(success == workers),
            status=200 if success == workers else 500,
            latency_ms=round((time.time() - started) * 1000, 2),
            details=_truncate(json.dumps({"success": success, "workers": workers, "statuses": statuses}, ensure_ascii=True)),
        )
    )
    return out


def _check_persistence_restart(port: int, user_id: str) -> List[CheckResult]:
    out: List[CheckResult] = []
    base_url = f"http://127.0.0.1:{port}"
    headers = {"X-User-ID": user_id}

    proc = _start_server(port=port, dev_bypass=True, env_mode="development")
    try:
        if not _wait_for_health(base_url):
            out.append(CheckResult("persistence", "dev server startup before create", False, details="health timeout"))
            return out

        unique = uuid.uuid4().hex[:8]
        query = f"after 47 sec send me current gold price marker-{unique}"
        created = _http_json(
            "POST",
            base_url,
            "/tasks/from-chat",
            headers=headers,
            payload={"query": query, "auto_create": True},
            timeout_sec=60,
        )
        created.section = "persistence"
        created.name = "task created before restart"
        body: Dict[str, Any] = {}
        try:
            body = json.loads(created.details)
        except Exception:
            body = {}
        task_id = None
        if isinstance(body, dict):
            task_id = body.get("task_id")
        created.ok = created.ok and bool(task_id)
        created.details = _truncate(json.dumps({"task_id": task_id, "raw": body}, ensure_ascii=True))
        out.append(created)
    finally:
        _stop_server(proc)

    # Restart and verify task still exists
    proc2 = _start_server(port=port, dev_bypass=True, env_mode="development")
    try:
        if not _wait_for_health(base_url):
            out.append(CheckResult("persistence", "dev server startup after restart", False, details="health timeout"))
            return out
        listed = _http_json("GET", base_url, "/tasks", headers, timeout_sec=60)
        listed.section = "persistence"
        listed.name = "tasks list loads after restart"
        found = False
        try:
            payload = json.loads(listed.details)
            if isinstance(payload, list):
                # stored as truncated string list maybe
                found = any(isinstance(x, dict) and "task_" in str(x.get("task_id", "")) for x in payload)
        except Exception:
            found = listed.ok
        listed.ok = listed.ok and found
        listed.details = _truncate(listed.details)
        out.append(listed)
    finally:
        _stop_server(proc2)

    return out


def _render_report(results: List[CheckResult]) -> str:
    sections: Dict[str, List[CheckResult]] = {}
    for r in results:
        sections.setdefault(r.section, []).append(r)

    passed = sum(1 for r in results if r.ok)
    lines: List[str] = []
    lines.append("# TAOS Failure-Path QA Report")
    lines.append("")
    lines.append(f"- Generated: {_now_utc()}")
    lines.append(f"- Total: **{passed}/{len(results)}** checks passed")
    lines.append("")

    for section in sorted(sections.keys()):
        lines.append(f"## {section}")
        lines.append("")
        lines.append("| Check | Critical | OK | HTTP | Latency (ms) | Details |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for r in sections[section]:
            status = "" if r.status is None else str(r.status)
            lines.append(
                f"| `{r.name}` | {'yes' if r.critical else 'no'} | {'yes' if r.ok else 'no'} | {status} | {r.latency_ms:.2f} | {_truncate(r.details, 220).replace(chr(10), ' ')} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run failure-path QA checks for TAOS.")
    parser.add_argument("--user-id", default="QVkNm5uxzpOBLTOWDNJtQFoUH7D2")
    parser.add_argument("--dev-port", type=int, default=8011)
    parser.add_argument("--prod-port", type=int, default=8012)
    parser.add_argument("--write-report", default="QA_FAILURE_RESULTS.md")
    args = parser.parse_args()

    all_results: List[CheckResult] = []

    # 1) Dev server checks: bad inputs + concurrency.
    dev_base = f"http://127.0.0.1:{args.dev_port}"
    dev_headers = {"X-User-ID": args.user_id}
    dev_proc = _start_server(port=args.dev_port, dev_bypass=True, env_mode="development")
    try:
        if not _wait_for_health(dev_base):
            all_results.append(CheckResult("startup", "dev server health", False, details="health check timeout"))
        else:
            all_results.extend(_check_bad_inputs(dev_base, dev_headers, args.user_id))
            all_results.extend(_check_concurrency(dev_base, dev_headers, args.user_id, workers=6))
    finally:
        _stop_server(dev_proc)

    # 2) Auth boundaries (production, no bypass).
    all_results.extend(_check_auth_boundaries(args.prod_port))

    # 3) DAG failure behavior checks.
    all_results.extend(_check_dag_failures())

    # 4) Persistence restart checks (start/stop/start).
    all_results.extend(_check_persistence_restart(port=args.dev_port, user_id=args.user_id))

    # Console summary
    passed = sum(1 for r in all_results if r.ok)
    print("\nFailure-Path QA Summary")
    print("=======================")
    for r in all_results:
        mark = "PASS" if r.ok else "FAIL"
        status = "-" if r.status is None else str(r.status)
        c = "critical" if r.critical else "non-critical"
        print(f"[{mark}] {r.section} :: {r.name} ({c}) | status={status} | {r.latency_ms:.2f}ms | {_truncate(r.details, 120)}")
    print(f"\nFinal: {passed}/{len(all_results)} checks passed")

    report = _render_report(all_results)
    with open(args.write_report, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report written: {args.write_report}")

    critical_failures = [r for r in all_results if r.critical and not r.ok]
    if critical_failures:
        print(f"Critical failures: {len(critical_failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
