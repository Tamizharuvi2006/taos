"""
TAOS CLI — Command-line entry point.

Usage:
    python -m taos.apps.cli.main run "Search for the latest Python release"
    python -m taos.apps.cli.main run --file goals.txt
    python -m taos.apps.cli.main serve
    python -m taos.apps.cli.main replay --request-id <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import List, Optional


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="taos",
        description="TAOS AgentOS — Production-grade AI Agent",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ─── run command ──────────────────
    run_parser = subparsers.add_parser("run", help="Execute an agent task")
    run_parser.add_argument("goal", nargs="?", help="The goal/task to execute")
    run_parser.add_argument("--file", "-f", help="Read goal from file")
    run_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    run_parser.add_argument("--plan-only", action="store_true", help="Only generate plan, don't execute")

    # ─── serve command ────────────────
    serve_parser = subparsers.add_parser("serve", help="Start the API server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port")
    serve_parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")

    # ─── replay command ───────────────
    replay_parser = subparsers.add_parser("replay", help="Replay a past execution")
    replay_parser.add_argument("--request-id", required=True, help="Request ID to replay")

    args = parser.parse_args()

    if args.command == "run":
        _handle_run(args)
    elif args.command == "serve":
        _handle_serve(args)
    elif args.command == "replay":
        _handle_replay(args)
    else:
        parser.print_help()
        sys.exit(1)


def _handle_run(args) -> None:
    """Handle the 'run' command."""
    # Get goal
    goal = args.goal
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                goal = f.read().strip()
        except FileNotFoundError:
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)

    if not goal:
        print("Error: No goal provided. Use 'taos run \"your goal\"' or --file", file=sys.stderr)
        sys.exit(1)

    print(f"🧠 TAOS AgentOS")
    print(f"{'─' * 50}")
    print(f"📋 Goal: {goal}")
    print(f"{'─' * 50}")

    # Run the agent
    result = asyncio.run(_execute_goal(goal, plan_only=args.plan_only))

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_result(result, plan_only=args.plan_only)


async def _execute_goal(goal: str, plan_only: bool = False) -> dict:
    """Execute a goal using the orchestration engine."""
    from taos.infra.logging.logger import TAOSLogger
    from taos.orchestration.engine import OrchestrationEngine
    from taos.orchestration.workflow import WorkflowRunner

    logger = TAOSLogger(name="taos.cli")
    engine = OrchestrationEngine(logger=logger)
    workflow = WorkflowRunner(engine)

    if plan_only:
        result = await workflow.run_plan_only(goal)
        return result.result or {"error": result.error}
    else:
        result = await workflow.run_standard(goal)
        return result.result or {"error": result.error}


def _print_result(result: dict, plan_only: bool = False) -> None:
    """Pretty-print execution result."""
    if plan_only:
        print(f"\n📝 Generated Plan:")
        plan = result.get("plan", {})
        steps = plan.get("steps", [])
        for i, step in enumerate(steps):
            tool_str = f" [{step.get('tool')}]" if step.get("tool") else ""
            print(f"  {i+1}. {step.get('action', 'N/A')}{tool_str}")
        print(f"\n  Complexity: {result.get('complexity', 'N/A')}")
        print(f"  Planning cost: ${result.get('planning_cost', 0):.4f}")
        return

    success = result.get("success", False)
    status_icon = "✅" if success else "❌"

    print(f"\n{status_icon} Status: {result.get('status', 'unknown')}")

    if result.get("result"):
        print(f"\n📄 Result:")
        result_text = str(result["result"])
        if len(result_text) > 2000:
            result_text = result_text[:2000] + "..."
        print(f"  {result_text}")

    if result.get("error"):
        print(f"\n⚠️  Error: {result['error']}")

    print(f"\n📊 Metrics:")
    print(f"  Steps executed: {result.get('steps_executed', 0)}")
    print(f"  Total cost: ${result.get('total_cost', 0):.4f}")
    print(f"  Confidence: {result.get('confidence', 0):.2f}")
    print(f"  Elapsed: {result.get('elapsed_time', 0):.1f}s")
    print(f"  Replans: {result.get('replan_count', 0)}")
    print(f"{'─' * 50}")


def _handle_serve(args) -> None:
    """Handle the 'serve' command — start the API server."""
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    print(f"🚀 Starting TAOS API server on {args.host}:{args.port}")
    uvicorn.run(
        "taos.apps.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def _handle_replay(args) -> None:
    """Handle the 'replay' command — replay a past execution trace."""
    print(f"🔄 Replay not yet implemented for request: {args.request_id}")
    print("   (Requires persistent trace storage — planned for future)")


if __name__ == "__main__":
    main()
