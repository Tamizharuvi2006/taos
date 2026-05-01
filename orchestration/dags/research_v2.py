"""Research micro-DAG used by FSM as a controlled execution primitive."""

from __future__ import annotations

from taos.core.execution.dag_models import DagDefinition, DagNode, DagNodeKind


def get_research_v2_dag() -> DagDefinition:
    """
    Build the default research DAG.

    Flow:
    1. Search for current sources.
    2. Extract top source text.
    3. Build a structured research brief.
    """
    return DagDefinition(
        name="research_v2",
        version="1.0.0",
        description="Parallel research search + ranked extraction + structured synthesis payload",
        nodes=[
            DagNode(
                id="search_latest",
                kind=DagNodeKind.TOOL,
                action="Search the web for the latest updates",
                tool="web_search",
                inputs={
                    "query": "{{input.latest_query}}",
                    "num_results": 5,
                    "search_type": "{{input.search_type}}",
                    "recency_days": "{{input.recency_days}}",
                },
                retry_limit=2,
                timeout_seconds=20,
                required=True,
            ),
            DagNode(
                id="search_context",
                kind=DagNodeKind.TOOL,
                action="Search for broader context and analysis",
                tool="web_search",
                inputs={
                    "query": "{{input.context_query}}",
                    "num_results": 5,
                    "search_type": "search",
                },
                retry_limit=2,
                timeout_seconds=20,
                required=False,
            ),
            DagNode(
                id="search_official",
                kind=DagNodeKind.TOOL,
                action="Search for official or primary-source updates",
                tool="web_search",
                inputs={
                    "query": "{{input.official_query}}",
                    "num_results": 4,
                    "search_type": "{{input.search_type}}",
                    "recency_days": "{{input.recency_days}}",
                },
                retry_limit=1,
                timeout_seconds=20,
                required=False,
            ),
            DagNode(
                id="collect_sources",
                kind=DagNodeKind.TRANSFORM,
                action="Rank and diversify search results",
                inputs={
                    "mode": "rank_sources",
                    "source_paths": [
                        "search_latest.results",
                        "search_context.results",
                        "search_official.results",
                    ],
                    "max_results": 6,
                },
                depends_on=["search_latest", "search_context", "search_official"],
                retry_limit=0,
                timeout_seconds=5,
                required=True,
            ),
            DagNode(
                id="extract_top_1",
                kind=DagNodeKind.TOOL,
                action="Extract readable text from top result",
                tool="web_extract",
                inputs={
                    "url": "{{collect_sources.results.0.link}}",
                    "max_chars": 2800,
                },
                depends_on=["collect_sources"],
                retry_limit=1,
                timeout_seconds=20,
                required=False,
            ),
            DagNode(
                id="extract_top_2",
                kind=DagNodeKind.TOOL,
                action="Extract readable text from second-ranked result",
                tool="web_extract",
                inputs={
                    "url": "{{collect_sources.results.1.link}}",
                    "max_chars": 2600,
                },
                depends_on=["collect_sources"],
                retry_limit=1,
                timeout_seconds=20,
                required=False,
            ),
            DagNode(
                id="extract_top_3",
                kind=DagNodeKind.TOOL,
                action="Extract readable text from third-ranked result",
                tool="web_extract",
                inputs={
                    "url": "{{collect_sources.results.2.link}}",
                    "max_chars": 2400,
                },
                depends_on=["collect_sources"],
                retry_limit=1,
                timeout_seconds=20,
                required=False,
            ),
            DagNode(
                id="compose",
                kind=DagNodeKind.TRANSFORM,
                action="Compose research brief payload",
                inputs={
                    "mode": "research_brief",
                    "goal": "{{input.query}}",
                    "extract_paths": ["extract_top_1", "extract_top_2", "extract_top_3"],
                },
                depends_on=["collect_sources", "extract_top_1", "extract_top_2", "extract_top_3"],
                retry_limit=0,
                timeout_seconds=5,
                required=True,
            ),
        ],
        output_node_ids=["compose"],
        max_runtime_seconds=120,
    )
