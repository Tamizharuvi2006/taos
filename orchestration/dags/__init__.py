"""Built-in micro-DAG definitions for FSM execution."""

from __future__ import annotations

from taos.core.execution.dag_models import DagDefinition
from taos.core.execution.dag_registry import DagRegistry
from taos.orchestration.dags.research_v2 import get_research_v2_dag


def built_in_dags() -> list[DagDefinition]:
    """Return all built-in DAG definitions."""
    return [
        get_research_v2_dag(),
    ]


def register_default_dags(registry: DagRegistry) -> None:
    """Register built-in DAGs into the provided registry."""
    for dag in built_in_dags():
        registry.register(dag)
