"""Micro-DAG execution runner used by StepRunner for DAG_EXEC steps."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from taos.config.settings import get_settings
from taos.core.execution.dag_models import (
    DagNode,
    DagNodeKind,
    DagNodeResult,
    DagRunResult,
)
from taos.core.execution.dag_registry import DagRegistry, get_default_dag_registry
from taos.core.semantic.intent_classifier import DomainType
from taos.core.tools.source_ranker import SourceRanker
from taos.core.tools.tool_executor import ToolExecutor


class DagRunner:
    """Executes DAG definitions with dependency ordering and node retries."""

    _TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

    def __init__(
        self,
        tool_executor: ToolExecutor,
        registry: Optional[DagRegistry] = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._registry = registry or get_default_dag_registry()
        self._settings = get_settings()
        self._source_ranker = SourceRanker()

    async def run(
        self,
        dag_name: str,
        input_data: Optional[Dict[str, Any]] = None,
        task_id: str = "",
        parent_step_id: str = "",
    ) -> DagRunResult:
        """Run a named DAG against input data."""
        dag = self._registry.get(dag_name)
        started = time.time()
        context: Dict[str, Any] = {"input": input_data or {}}
        node_results: Dict[str, DagNodeResult] = {}
        completed: set[str] = set()
        node_map: Dict[str, DagNode] = {n.id: n for n in dag.nodes}
        batch_records: List[Dict[str, Any]] = []
        frontier_index = 0
        batch_index = 0
        concurrency_cap = max(1, int(self._settings.dag_max_concurrency or 1))

        while len(completed) < len(node_map):
            ready = [
                node
                for node in dag.nodes
                if node.id not in completed
                and all(dep in completed for dep in node.depends_on)
            ]

            if not ready:
                return DagRunResult(
                    dag_name=dag.name,
                    status="failed",
                    node_results=node_results,
                    final_output={},
                    total_cost=sum(r.cost for r in node_results.values()),
                    total_latency=time.time() - started,
                    error="DAG deadlock or unresolved dependencies",
                    execution_mode="parallel" if concurrency_cap > 1 else "sequential",
                    batches=batch_records,
                    frontier_count=frontier_index,
                )

            frontier_index += 1
            for frontier_batch in self._chunk_nodes(ready, concurrency_cap):
                if time.time() - started > dag.max_runtime_seconds:
                    return DagRunResult(
                        dag_name=dag.name,
                        status="failed",
                        node_results=node_results,
                        final_output={},
                        total_cost=sum(r.cost for r in node_results.values()),
                        total_latency=time.time() - started,
                        error=(
                            f"DAG runtime exceeded {dag.max_runtime_seconds}s "
                            f"(dag={dag.name}, frontier={frontier_index})"
                        ),
                        execution_mode="parallel" if concurrency_cap > 1 else "sequential",
                        batches=batch_records,
                        frontier_count=frontier_index,
                    )

                batch_index += 1
                batch_started = time.time()
                snapshot = dict(context)
                batch_results = await asyncio.gather(
                    *[
                        self._execute_node_with_retry(
                            node=node,
                            context=snapshot,
                            task_id=task_id,
                            parent_step_id=parent_step_id,
                            batch_index=batch_index,
                            frontier_index=frontier_index,
                        )
                        for node in frontier_batch
                    ]
                )

                failed_required: Optional[DagNodeResult] = None
                failed_required_node: Optional[DagNode] = None
                batch_status = "success"
                for node, node_result in zip(frontier_batch, batch_results):
                    node_results[node.id] = node_result
                    completed.add(node.id)
                    context[node.id] = node_result.output
                    if node_result.status == "failed":
                        batch_status = "failed"
                        if node.required and failed_required is None:
                            failed_required = node_result
                            failed_required_node = node

                batch_records.append(
                    {
                        "batch_index": batch_index,
                        "frontier_index": frontier_index,
                        "status": batch_status,
                        "node_ids": [node.id for node in frontier_batch],
                        "latency_ms": round((time.time() - batch_started) * 1000, 2),
                    }
                )

                if failed_required and failed_required_node:
                    return DagRunResult(
                        dag_name=dag.name,
                        status="failed",
                        node_results=node_results,
                        final_output={
                            node_id: context.get(node_id)
                            for node_id in dag.output_node_ids
                            if context.get(node_id) is not None
                        },
                        total_cost=sum(r.cost for r in node_results.values()),
                        total_latency=time.time() - started,
                        error=failed_required.error or f"Required node failed: {failed_required_node.id}",
                        execution_mode="parallel" if concurrency_cap > 1 else "sequential",
                        batches=batch_records,
                        frontier_count=frontier_index,
                    )

        final_output = {node_id: context.get(node_id) for node_id in dag.output_node_ids}
        return DagRunResult(
            dag_name=dag.name,
            status="success",
            execution_mode="parallel" if concurrency_cap > 1 else "sequential",
            node_results=node_results,
            final_output=final_output,
            batches=batch_records,
            frontier_count=frontier_index,
            total_cost=sum(r.cost for r in node_results.values()),
            total_latency=time.time() - started,
            error=None,
        )

    async def _execute_node_with_retry(
        self,
        node: DagNode,
        context: Dict[str, Any],
        task_id: str,
        parent_step_id: str,
        batch_index: int,
        frontier_index: int,
    ) -> DagNodeResult:
        attempts = max(0, int(node.retry_limit)) + 1
        last_result: Optional[DagNodeResult] = None

        for attempt in range(attempts):
            started = time.time()
            try:
                result = await asyncio.wait_for(
                    self._execute_node(node, context, task_id, parent_step_id),
                    timeout=max(1, int(node.timeout_seconds or 1)),
                )
            except asyncio.TimeoutError:
                result = DagNodeResult(
                    node_id=node.id,
                    status="failed",
                    output=None,
                    error=f"Node {node.id} timed out after {node.timeout_seconds}s",
                    latency=time.time() - started,
                    tool_name=node.tool,
                )
            result.retries_used = attempt
            result.batch_index = batch_index
            result.frontier_index = frontier_index
            if result.status == "success" or result.status == "skipped":
                return result
            last_result = result
            if attempt < attempts - 1:
                await asyncio.sleep(min(2**attempt, 5))

        return last_result or DagNodeResult(
            node_id=node.id,
            status="failed",
            error=f"Node {node.id} failed with unknown error",
            retries_used=max(0, attempts - 1),
            batch_index=batch_index,
            frontier_index=frontier_index,
        )

    async def _execute_node(
        self,
        node: DagNode,
        context: Dict[str, Any],
        task_id: str,
        parent_step_id: str,
    ) -> DagNodeResult:
        started = time.time()
        if not self._condition_allowed(node.condition, context):
            return DagNodeResult(
                node_id=node.id,
                status="skipped",
                output=None,
                latency=time.time() - started,
            )

        try:
            if node.kind == DagNodeKind.TOOL:
                resolved_inputs = self._resolve_payload(node.inputs, context)
                tool_result = await self._tool_executor.execute(
                    tool_name=node.tool or "",
                    tool_input=resolved_inputs,
                    task_id=task_id,
                    step_id=f"{parent_step_id}:{node.id}" if parent_step_id else node.id,
                )
                if not tool_result.success:
                    return DagNodeResult(
                        node_id=node.id,
                        status="failed",
                        output=None,
                        error=tool_result.error,
                        latency=time.time() - started,
                        cost=tool_result.cost,
                        tool_name=node.tool,
                    )
                return DagNodeResult(
                    node_id=node.id,
                    status="success",
                    output=tool_result.result,
                    error=None,
                    latency=time.time() - started,
                    cost=tool_result.cost,
                    tool_name=node.tool,
                )

            if node.kind == DagNodeKind.TRANSFORM:
                output = self._run_transform(node=node, context=context)
                return DagNodeResult(
                    node_id=node.id,
                    status="success",
                    output=output,
                    latency=time.time() - started,
                    cost=0.0,
                )

            if node.kind == DagNodeKind.VALIDATE:
                ok, details = self._run_validate(node=node, context=context)
                return DagNodeResult(
                    node_id=node.id,
                    status="success" if ok else "failed",
                    output=details,
                    error=None if ok else "Validation node failed",
                    latency=time.time() - started,
                )

            return DagNodeResult(
                node_id=node.id,
                status="failed",
                output=None,
                error=f"Unsupported DAG node kind: {node.kind}",
                latency=time.time() - started,
            )
        except Exception as exc:
            return DagNodeResult(
                node_id=node.id,
                status="failed",
                output=None,
                error=f"DAG node '{node.id}' execution error: {str(exc)}",
                latency=time.time() - started,
                tool_name=node.tool,
            )

    def _run_transform(self, node: DagNode, context: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._resolve_payload(node.inputs, context)
        mode = str(payload.get("mode", "")).lower()
        if mode == "rank_sources":
            merged: List[Dict[str, Any]] = []
            for path in payload.get("source_paths", []):
                rows = self._lookup_path(context, str(path))
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if isinstance(row, dict):
                        merged.append(row)
            ranked = self._source_ranker.rank(
                merged,
                domain=DomainType.GENERAL,
                max_results=int(payload.get("max_results", 8) or 8),
            )
            results = [
                {
                    "title": item.title,
                    "link": item.url,
                    "snippet": item.snippet,
                    "tier": item.tier,
                    "provider": item.provider,
                    "rank_score": round(float(item.rank_score or 0.0), 4),
                }
                for item in ranked
            ]
            return {
                "count": len(results),
                "provider_count": len({item["provider"] for item in results if item.get("provider")}),
                "results": results,
            }
        if mode == "research_brief":
            ranked_sources = context.get("collect_sources", {}) if isinstance(context.get("collect_sources"), dict) else {}
            rows: List[Dict[str, Any]] = []
            for item in ranked_sources.get("results", [])[:6]:
                rows.append(
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "tier": item.get("tier", ""),
                        "provider": item.get("provider", ""),
                    }
                )
            extracts: List[Dict[str, Any]] = []
            for path in payload.get("extract_paths", []):
                extracted = self._lookup_path(context, str(path))
                if not isinstance(extracted, dict):
                    continue
                extracts.append(
                    {
                        "url": extracted.get("url", ""),
                        "title": extracted.get("title", ""),
                        "published_at": extracted.get("published_at", ""),
                        "text": extracted.get("text", ""),
                    }
                )
            return {
                "goal": payload.get("goal", ""),
                "source_count": len(rows),
                "provider_count": len({row.get("provider") for row in rows if row.get("provider")}),
                "sources": rows,
                "extracts": extracts,
            }
        return payload

    def _run_validate(self, node: DagNode, context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
        payload = self._resolve_payload(node.inputs, context)
        path = str(payload.get("path", "")).strip()
        min_items = int(payload.get("min_items", 1))
        value = self._lookup_path(context, path) if path else None
        if isinstance(value, list):
            ok = len(value) >= max(0, min_items)
        else:
            ok = value is not None
        return ok, {"path": path, "min_items": min_items, "value_type": type(value).__name__}

    def _condition_allowed(self, condition: Optional[str], context: Dict[str, Any]) -> bool:
        if not condition:
            return True
        cond = str(condition).strip()
        if cond.startswith("exists:"):
            path = cond.split(":", 1)[1].strip()
            return self._lookup_path(context, path) is not None
        if cond.startswith("truthy:"):
            path = cond.split(":", 1)[1].strip()
            return bool(self._lookup_path(context, path))
        resolved = self._resolve_template_string(cond, context)
        return bool(resolved)

    def _resolve_payload(self, value: Any, context: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for key, val in value.items():
                resolved = self._resolve_payload(val, context)
                if resolved is None and key in {"search_type", "recency_days", "timeout", "max_chars"}:
                    continue
                out[key] = resolved
            return out
        if isinstance(value, list):
            return [self._resolve_payload(v, context) for v in value]
        if isinstance(value, str):
            return self._resolve_template_string(value, context)
        return value

    def _resolve_template_string(self, text: str, context: Dict[str, Any]) -> Any:
        matches = list(self._TEMPLATE_RE.finditer(text))
        if not matches:
            return text

        stripped = text.strip()
        if len(matches) == 1 and matches[0].group(0) == stripped:
            return self._lookup_path(context, matches[0].group(1).strip())

        result = text
        for match in matches:
            key = match.group(1).strip()
            value = self._lookup_path(context, key)
            result = result.replace(match.group(0), "" if value is None else str(value))
        return result

    def _lookup_path(self, data: Dict[str, Any], dotted_path: str) -> Any:
        current: Any = data
        for part in dotted_path.split("."):
            key = part.strip()
            if not key:
                return None
            if isinstance(current, dict):
                current = current.get(key)
                continue
            if isinstance(current, list) and key.isdigit():
                idx = int(key)
                if idx < 0 or idx >= len(current):
                    return None
                current = current[idx]
                continue
            return None
        return current

    def _chunk_nodes(self, nodes: List[DagNode], chunk_size: int) -> List[List[DagNode]]:
        if chunk_size <= 0:
            return [nodes]
        return [nodes[i:i + chunk_size] for i in range(0, len(nodes), chunk_size)]
