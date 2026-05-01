"""Registry for FSM-executed micro-DAG definitions."""

from __future__ import annotations

from typing import Dict, List, Optional

from taos.core.execution.dag_models import DagDefinition


class DagNotFoundError(Exception):
    """Raised when a requested DAG is not registered."""


class DagRegistry:
    """In-memory DAG definition registry."""

    def __init__(self) -> None:
        self._dags: Dict[str, DagDefinition] = {}

    def register(self, dag: DagDefinition) -> None:
        self._dags[dag.name] = dag

    def has(self, name: str) -> bool:
        return name in self._dags

    def get(self, name: str) -> DagDefinition:
        if name not in self._dags:
            raise DagNotFoundError(
                f"DAG '{name}' not found. Available DAGs: {self.list_names()}"
            )
        return self._dags[name]

    def list_names(self) -> List[str]:
        return sorted(self._dags.keys())


_DEFAULT_DAG_REGISTRY: Optional[DagRegistry] = None


def get_default_dag_registry() -> DagRegistry:
    """Lazily initialize default registry with built-in DAGs."""
    global _DEFAULT_DAG_REGISTRY
    if _DEFAULT_DAG_REGISTRY is None:
        registry = DagRegistry()
        # Late import to avoid import cycles.
        from taos.orchestration.dags import register_default_dags

        register_default_dags(registry)
        _DEFAULT_DAG_REGISTRY = registry
    return _DEFAULT_DAG_REGISTRY
