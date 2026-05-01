from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


@dataclass
class CheckResult:
    name: str
    ok: bool
    status: Optional[int]
    latency_ms: float
    details: Dict[str, Any]
    error: str = ""
    critical: bool = True


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _json_trunc(value: Any, max_chars: int = 280) -> str:
    text = json.dumps(value, ensure_ascii=True) if not isinstance(value, str) else value
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _call_json(
    method: str,
    base_url: str,
    path: str,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> CheckResult:
    if requests is None:
        return CheckResult(
            name=path,
            ok=False,
            status=None,
            latency_ms=0.0,
            details={},
            error="requests package not installed",
        )
    t0 = time.time()
    try:
        if method == "GET":
            r = requests.get(base_url + path, headers=headers, timeout=timeout)
        else:
            merged = dict(headers)
            merged["Content-Type"] = "application/json"
            r = requests.post(base_url + path, headers=merged, json=payload, timeout=timeout)
        latency_ms = round((time.time() - t0) * 1000, 2)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:500]}
        ok = 200 <= r.status_code < 300
        return CheckResult(name=path, ok=ok, status=r.status_code, latency_ms=latency_ms, details={"body": body}, critical=True)
    except Exception as exc:
        latency_ms = round((time.time() - t0) * 1000, 2)
        return CheckResult(name=path, ok=False, status=None, latency_ms=latency_ms, details={}, error=str(exc), critical=True)


def _wait_for_health(base_url: str, timeout_sec: int = 20) -> bool:
    if requests is None:
        return False
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.get(base_url + "/health", timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def _run_sse_check(base_url: str, headers: Dict[str, str], user_id: str, timeout_sec: int = 45) -> CheckResult:
    if requests is None:
        return CheckResult("SSE /execute/stream", False, None, 0.0, {}, "requests package not installed")

    t0 = time.time()
    events: List[str] = []
    try:
        payload = {"query": "what is 2 + 2?", "user_id": user_id, "user_tier": "free"}
        stream_headers = dict(headers)
        stream_headers["Content-Type"] = "application/json"
        with requests.post(
            base_url + "/execute/stream",
            headers=stream_headers,
            json=payload,
            stream=True,
            timeout=(5, max(10, timeout_sec)),
        ) as resp:
            if resp.status_code != 200:
                return CheckResult(
                    "SSE /execute/stream",
                    False,
                    resp.status_code,
                    round((time.time() - t0) * 1000, 2),
                    {"events": []},
                    f"http_status_{resp.status_code}",
                    critical=False,
                )

            deadline = time.time() + timeout_sec
            for line in resp.iter_lines(decode_unicode=True):
                if time.time() > deadline:
                    break
                if not line:
                    continue
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                    events.append(ev)
                    if ev.upper() == "FINAL":
                        break

        final_seen = any(ev.upper() == "FINAL" for ev in events)
        return CheckResult(
            "SSE /execute/stream",
            final_seen,
            200,
            round((time.time() - t0) * 1000, 2),
            {"events": events, "final_seen": final_seen, "event_count": len(events)},
            "" if final_seen else "final_event_not_seen",
            critical=False,
        )
    except Exception as exc:
        return CheckResult(
            "SSE /execute/stream",
            False,
            None,
            round((time.time() - t0) * 1000, 2),
            {"events": events},
            str(exc),
            critical=False,
        )


def _render_markdown(results: List[CheckResult], meta: Dict[str, Any]) -> str:
    lines = []
    lines.append("# TAOS QA Demo Report")
    lines.append("")
    lines.append(f"- Generated: {_now_utc()}")
    lines.append(f"- Base URL: `{meta['base_url']}`")
    lines.append(f"- Dev bypass: `{meta['dev_bypass']}`")
    lines.append(f"- User ID: `{meta['user_id']}`")
    lines.append("")
    lines.append("## Summary")
    passed = sum(1 for r in results if r.ok)
    lines.append(f"- Passed: **{passed}/{len(results)}**")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Critical | OK | Status | Latency (ms) | Details |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for r in results:
        status = "" if r.status is None else str(r.status)
        details = r.error if r.error else _json_trunc(r.details, max_chars=220).replace("\n", " ")
        lines.append(f"| `{r.name}` | {'yes' if r.critical else 'no'} | {'yes' if r.ok else 'no'} | {status} | {r.latency_ms:.2f} | {details} |")
    lines.append("")
    return "\n".join(lines)


def _print_console_summary(results: List[CheckResult]) -> None:
    print("")
    print("QA Demo Summary")
    print("================")
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        status = "-" if r.status is None else str(r.status)
        msg = r.error if r.error else _json_trunc(r.details, 120)
        c = "critical" if r.critical else "non-critical"
        print(f"[{mark}] {r.name} ({c}) | status={status} | {r.latency_ms:.2f}ms | {msg}")
    print("")
    passed = sum(1 for r in results if r.ok)
    print(f"Final: {passed}/{len(results)} checks passed")


def main() -> int:
    parser = argparse.ArgumentParser(description="One-command TAOS QA demo runner.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--server-port", type=int, default=8010, help="Port to start isolated QA server on.")
    parser.add_argument("--use-existing-server", action="store_true", help="Use already-running server at --base-url.")
    parser.add_argument("--user-id", default="QVkNm5uxzpOBLTOWDNJtQFoUH7D2")
    parser.add_argument("--dev-bypass", action="store_true", help="Enable development auth bypass for protected routes.")
    parser.add_argument("--write-report", default="", help="Optional markdown output path (e.g. QA_RESULTS_LIVE.md).")
    args = parser.parse_args()

    if requests is None:
        print("ERROR: 'requests' package is required for this script.")
        return 2

    env = os.environ.copy()
    if args.dev_bypass:
        env["AUTH_ALLOW_DEV_BYPASS"] = "true"
        env["TAOS_ENV"] = "development"

    server_proc: Optional[subprocess.Popen[str]] = None

    results: List[CheckResult] = []
    headers = {"X-User-ID": args.user_id}
    base_url = args.base_url.strip() or f"http://127.0.0.1:{args.server_port}"
    try:
        if args.use_existing_server:
            if not _wait_for_health(base_url, timeout_sec=8):
                print(f"ERROR: existing server is not healthy at {base_url}")
                return 3
        else:
            if _wait_for_health(base_url, timeout_sec=2):
                print(
                    f"ERROR: target QA server port already has a running service at {base_url}. "
                    f"Use --server-port with a free port or pass --use-existing-server."
                )
                return 4
            server_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "taos.apps.api.main:app", "--host", "127.0.0.1", "--port", str(args.server_port)],
                cwd="D:/agent",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
            )
            if not _wait_for_health(base_url, timeout_sec=35):
                print("ERROR: backend did not become healthy in time.")
                return 3

        # Core checks.
        results.append(_call_json("GET", base_url, "/health", headers, timeout=20))
        results.append(_call_json("GET", base_url, "/users/me", headers, timeout=30))
        results.append(_call_json("POST", base_url, "/execute", headers, payload={"query": "what is 25 * 17?", "user_id": args.user_id, "user_tier": "free"}, timeout=75))
        results.append(_call_json("POST", base_url, "/execute", headers, payload={"query": "research about iran and israel war current status", "user_id": args.user_id, "user_tier": "free"}, timeout=45))
        results.append(_call_json("POST", base_url, "/tasks/from-chat", headers, payload={"query": "after 30 sec send me the current price of gold", "auto_create": True}, timeout=25))
        results.append(_call_json("GET", base_url, "/tasks", headers, timeout=25))

        # Debug checks first (SSE is placed last because long-lived stream can disrupt some local dev servers).
        r = _call_json("GET", base_url, "/push/health", headers, timeout=10)
        r.critical = False
        results.append(r)
        r = _call_json("GET", base_url, "/debug/research-trace?limit=5", headers, timeout=10)
        r.critical = False
        results.append(r)
        r = _call_json("POST", base_url, "/debug/notify", headers, payload={"to_email": "aruvi2907@gmail.com", "user_id": args.user_id, "message": "Welcome to Relyce AI"}, timeout=20)
        r.critical = False
        results.append(r)
        results.append(_run_sse_check(base_url, headers, args.user_id, timeout_sec=30))

        _print_console_summary(results)

        if args.write_report:
            md = _render_markdown(
                results,
                {
                    "base_url": base_url,
                    "dev_bypass": args.dev_bypass,
                    "user_id": args.user_id,
                },
            )
            with open(args.write_report, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"\nReport written: {args.write_report}")

        critical_failures = [r for r in results if r.critical and not r.ok]
        if critical_failures:
            print(f"Critical failures: {len(critical_failures)}")
            return 1
        return 0
    finally:
        if server_proc is not None:
            try:
                server_proc.send_signal(signal.SIGINT)
            except Exception:
                pass
            try:
                server_proc.communicate(timeout=20)
            except Exception:
                server_proc.kill()
                server_proc.communicate(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
