"""
TAOS Built-in Tools — Package init.

Factory function to register all built-in tools.
"""

from __future__ import annotations

from taos.core.tools.registry import ToolRegistry


def register_all_builtin_tools(registry: ToolRegistry) -> None:
    """Register all built-in tools with the registry."""
    from taos.core.tools.builtin.web_search import create_web_search_tool
    from taos.core.tools.builtin.web_extract import create_web_extract_tool
    from taos.core.tools.builtin.code_executor import create_code_executor_tool
    from taos.core.tools.builtin.http_request import create_http_request_tool
    from taos.core.tools.builtin.retrieve_chunks import create_retrieve_chunks_tool
    from taos.core.tools.builtin.process_document import create_process_document_tool
    from taos.core.tools.builtin.memory_kv import create_memory_get_tool, create_memory_set_tool
    from taos.core.tools.builtin.validate_answer import create_validate_answer_tool
    from taos.core.tools.builtin.file_ops import (
        create_file_read_tool,
        create_file_write_tool,
        create_file_list_tool,
    )

    registry.register(create_web_search_tool())
    registry.register(create_web_extract_tool())
    registry.register(create_code_executor_tool())
    registry.register(create_http_request_tool())
    registry.register(create_retrieve_chunks_tool())
    registry.register(create_process_document_tool())
    registry.register(create_memory_get_tool())
    registry.register(create_memory_set_tool())
    registry.register(create_validate_answer_tool())
    registry.register(create_file_read_tool())
    registry.register(create_file_write_tool())
    registry.register(create_file_list_tool())
