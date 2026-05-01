from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))


def build_report(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [dict(row or {}) for row in records]
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("route") or row.get("feedback_type") or row.get("category") or "unknown")
        grouped.setdefault(key, []).append(row)
    bugfix_queue = [
        {
            "group": key,
            "count": len(items),
            "priority": _priority(key, items),
        }
        for key, items in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    ]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_records": len(rows),
        "groups": {key: len(items) for key, items in grouped.items()},
        "bugfix_queue": bugfix_queue,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Post-release Report",
        "",
        f"- Generated: {report['timestamp']}",
        f"- Total records: {report['total_records']}",
        "",
        "| Group | Count | Priority |",
        "| --- | ---: | --- |",
    ]
    for item in report["bugfix_queue"]:
        lines.append(f"| {item['group']} | {item['count']} | {item['priority']} |")
    return "\n".join(lines).strip() + "\n"


def _priority(key: str, items: List[Dict[str, Any]]) -> str:
    text = f"{key} " + " ".join(str(item) for item in items).lower()
    if any(marker in text for marker in ("didnt_understand", "generic_fallback", "low_coverage", "admin")):
        return "high"
    if any(marker in text for marker in ("source_unavailable", "rate_limited", "slow")):
        return "medium"
    return "normal"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TAOS post-release monitoring report")
    parser.add_argument("--input", default="")
    parser.add_argument("--out-json", default="docs/post_release_report_latest.json")
    parser.add_argument("--out-md", default="docs/post_release_report_latest.md")
    args = parser.parse_args()
    records: List[Dict[str, Any]] = []
    if args.input:
        path = _REPO_ROOT / args.input
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            records = list(data.get("records") or data.get("results") or [])
    report = build_report(records)
    (_REPO_ROOT / args.out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (_REPO_ROOT / args.out_md).write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
