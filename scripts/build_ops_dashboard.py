"""Build TAOS operational dashboard artifacts from local eval and trace data."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_PARENT = _REPO_ROOT.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from taos.core.monitoring.ops_dashboard import (  # noqa: E402
    build_ops_dashboard,
    load_json,
    parse_research_markdown,
    render_ops_dashboard_markdown,
    write_ops_dashboard,
)


def _resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _REPO_ROOT / path


def build_dashboard(
    *,
    intelligence_report: Path,
    research_report: Path | None = None,
    execution_records_path: Path | None = None,
    provider_health_path: Path | None = None,
) -> Dict[str, Any]:
    intelligence = load_json(intelligence_report)
    research = load_json(research_report) if research_report and research_report.suffix.lower() == ".json" else {}
    if not research and research_report:
        research = parse_research_markdown(research_report)
    execution_data = load_json(execution_records_path)
    execution_records = execution_data.get("records") if isinstance(execution_data.get("records"), list) else []
    provider_data = load_json(provider_health_path)
    provider_snapshots = provider_data.get("providers") if isinstance(provider_data.get("providers"), list) else []
    return build_ops_dashboard(
        execution_records=execution_records,
        intelligence_report=intelligence,
        research_report=research,
        provider_snapshots=provider_snapshots,
        inputs={
            "intelligence_report": str(intelligence_report),
            "research_report": str(research_report) if research_report else None,
            "execution_records": str(execution_records_path) if execution_records_path else None,
            "provider_health": str(provider_health_path) if provider_health_path else None,
        },
    )


def render_markdown(dashboard: Dict[str, Any]) -> str:
    return render_ops_dashboard_markdown(dashboard)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build TAOS operational dashboard artifacts.")
    parser.add_argument("--intelligence-report", default="docs/intelligence_eval_latest.json")
    parser.add_argument("--research-report", default="eval/research_eval_report.json")
    parser.add_argument("--research-markdown", default="eval/research_eval_report.md")
    parser.add_argument("--execution-records", default="PERFORMANCE_RESULTS.json")
    parser.add_argument("--provider-health", default="")
    parser.add_argument("--out-json", default="docs/ops_dashboard_latest.json")
    parser.add_argument("--out-md", default="docs/ops_dashboard_latest.md")
    args = parser.parse_args()

    intelligence_path = _resolve_repo_path(args.intelligence_report)
    research_path = _resolve_repo_path(args.research_report)
    if not research_path.exists():
        research_path = _resolve_repo_path(args.research_markdown)
    execution_records_path = _resolve_repo_path(args.execution_records) if args.execution_records else None
    provider_health_path = _resolve_repo_path(args.provider_health) if args.provider_health else None
    out_json_path = _resolve_repo_path(args.out_json)
    out_md_path = _resolve_repo_path(args.out_md)
    dashboard = build_dashboard(
        intelligence_report=intelligence_path,
        research_report=research_path if research_path.exists() else None,
        execution_records_path=execution_records_path if execution_records_path and execution_records_path.exists() else None,
        provider_health_path=provider_health_path if provider_health_path and provider_health_path.exists() else None,
    )
    write_ops_dashboard(dashboard, out_json=out_json_path, out_md=out_md_path)
    print(f"Saved ops dashboard JSON to: {out_json_path}")
    print(f"Saved ops dashboard Markdown to: {out_md_path}")


if __name__ == "__main__":
    main()
