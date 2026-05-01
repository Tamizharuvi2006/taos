"""Workflow execution engine (DAG + decision + parallel nodes)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set

from taos.core.workflows.models import Workflow, WorkflowRun, WorkflowRunStatus, WorkflowNode
from taos.infra.logging.logger import TAOSLogger
from taos.services.notification.router import NotificationRouter


class WorkflowEngine:
    def __init__(self, logger: Optional[TAOSLogger] = None) -> None:
        self._logger = logger or TAOSLogger(name="taos.workflow.engine")
        self._notification_router = NotificationRouter()

    async def run(
        self,
        workflow: Workflow,
        initial_context: Optional[Dict[str, Any]] = None,
        user_id: str = "",
    ) -> WorkflowRun:
        run = WorkflowRun(workflow_id=workflow.workflow_id, user_id=user_id, context=initial_context or {})
        node_map = {node.id: node for node in workflow.nodes}
        indegree = {node.id: 0 for node in workflow.nodes}
        outgoing = defaultdict(list)
        incoming = defaultdict(list)

        for edge in workflow.edges:
            outgoing[edge.from_node].append(edge)
            incoming[edge.to_node].append(edge)
            indegree[edge.to_node] = indegree.get(edge.to_node, 0) + 1

        ready = deque([nid for nid, deg in indegree.items() if deg == 0])
        completed: Set[str] = set()

        try:
            while ready:
                batch = list(ready)
                ready.clear()

                results = await asyncio.gather(
                    *(self._execute_node(node_map[node_id], run.context) for node_id in batch),
                    return_exceptions=True,
                )

                for node_id, result in zip(batch, results):
                    if isinstance(result, Exception):
                        run.status = WorkflowRunStatus.FAILED
                        run.error = f"node {node_id} failed: {result}"
                        run.node_status[node_id] = "failed"
                        run.finished_at = time.time()
                        return run

                    run.context[node_id] = result
                    run.node_status[node_id] = "completed"
                    completed.add(node_id)

                for node_id in batch:
                    for edge in outgoing.get(node_id, []):
                        if not self._edge_allowed(edge.condition, run.context.get(node_id)):
                            continue
                        indegree[edge.to_node] -= 1
                        if indegree[edge.to_node] == 0 and edge.to_node not in completed:
                            ready.append(edge.to_node)

            run.status = WorkflowRunStatus.SUCCESS
            run.finished_at = time.time()
            return run
        except Exception as exc:
            run.status = WorkflowRunStatus.FAILED
            run.error = str(exc)
            run.finished_at = time.time()
            return run

    def _edge_allowed(self, condition: Optional[str], node_output: Any) -> bool:
        if not condition:
            return True
        if not isinstance(node_output, dict):
            return False
        decision = str(node_output.get("decision", "")).lower()
        return decision == str(condition).lower()

    async def _execute_node(self, node: WorkflowNode, context: Dict[str, Any]) -> Dict[str, Any]:
        node_type = node.type.lower()
        config = node.config or {}

        if node_type == "trigger":
            return {"ok": True, "message": "triggered", "config": config}

        if node_type == "research":
            query = config.get("query") or context.get("input_query") or "research topic"
            return {"ok": True, "query": query, "summary": f"Research collected for: {query}"}

        if node_type == "transform":
            source_node = config.get("source_node")
            source = context.get(source_node, context) if source_node else context
            text = config.get("template") or f"Transformed output from {source_node or 'context'}"
            return {"ok": True, "text": text, "source_preview": str(source)[:300]}

        if node_type == "decision":
            key = config.get("key", "")
            op = config.get("operator", "exists")
            expected = config.get("value")
            result = self._evaluate_decision(context, key, op, expected)
            return {"ok": True, "decision": "true" if result else "false"}

        if node_type == "action":
            action = config.get("action", "notify")
            payload = config.get("payload", {})
            return {"ok": True, "action": action, "payload": payload, "delivered": True}

        if node_type == "notify":
            channel = config.get("channel", "email")
            data = {
                "to": config.get("target", ""),
                "url": config.get("url", ""),
                "message": config.get("message", "Workflow notification"),
                "payload": {
                    "workflow_context": context,
                    "message": config.get("message", "Workflow notification"),
                },
            }
            send_result = await self._notification_router.send_notification(channel, data)
            return {"ok": True, "channel": channel, "notification": send_result}

        return {"ok": True, "message": f"noop node type: {node.type}"}

    def _evaluate_decision(self, context: Dict[str, Any], key: str, op: str, expected: Any) -> bool:
        value = _get_nested(context, key) if key else None
        op = op.lower()
        if op == "exists":
            return value is not None
        if op == "eq":
            return value == expected
        if op == "ne":
            return value != expected
        if op == "contains":
            return str(expected) in str(value)
        return False


def _get_nested(data: Dict[str, Any], dotted_key: str) -> Any:
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
