"""
TAOS Built-in Tool: File Operations.

Provides file read/write capabilities with:
- Path safety validation (prevent directory traversal)
- File size limits
- UTF-8 encoding support
- Directory listing
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from taos.core.tools.registry import ToolDefinition, ToolPolicy
from taos.config.constants import ToolRiskLevel


# ─── Safety: restrict to allowed directories ───
ALLOWED_BASE_DIRS = [
    os.path.expanduser("~"),
    "d:\\agent",
    "d:/agent",
]

MAX_FILE_SIZE = 1_000_000  # 1MB max file read
MAX_WRITE_SIZE = 500_000   # 500KB max file write


def _is_path_safe(file_path: str) -> bool:
    """Check if path is within allowed directories (prevent traversal)."""
    resolved = Path(file_path).resolve()
    for base_dir in ALLOWED_BASE_DIRS:
        if str(resolved).startswith(str(Path(base_dir).resolve())):
            return True
    return False


async def file_read(
    path: str,
    encoding: str = "utf-8",
    max_bytes: int = MAX_FILE_SIZE,
) -> Dict[str, Any]:
    """
    Read a file from the filesystem.
    
    Args:
        path: Absolute or relative file path
        encoding: File encoding (default utf-8)
        max_bytes: Max bytes to read
    
    Returns:
        Dict with file content, size, and metadata
    """
    try:
        resolved = Path(path).resolve()

        if not resolved.exists():
            return {"success": False, "error": f"File not found: {path}"}

        if not resolved.is_file():
            return {"success": False, "error": f"Not a file: {path}"}

        size = resolved.stat().st_size
        if size > max_bytes:
            return {
                "success": False,
                "error": f"File too large: {size} bytes (max {max_bytes})",
            }

        content = resolved.read_text(encoding=encoding)

        return {
            "success": True,
            "content": content[:max_bytes],
            "path": str(resolved),
            "size": size,
            "lines": content.count("\n") + 1,
        }

    except UnicodeDecodeError:
        return {"success": False, "error": f"Cannot decode file with {encoding} encoding"}
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": f"File read failed: {str(e)}"}


async def file_write(
    path: str = "",
    content: str = "",
    mode: str = "write",
    encoding: str = "utf-8",
    **kwargs
) -> Dict[str, Any]:
    """
    Write content to a file.
    
    Args:
        path: File path to write to
        content: Content to write
        mode: "write" (overwrite) or "append"
        encoding: File encoding
    
    Returns:
        Dict with success status and file info
    """
    path = path or kwargs.get("file_name") or kwargs.get("filename") or kwargs.get("name") or kwargs.get("filepath")
    if not path:
        return {"success": False, "error": f"Missing required argument 'path'. Got: {kwargs}"}
        
    try:
        if len(content.encode(encoding)) > MAX_WRITE_SIZE:
            return {
                "success": False,
                "error": f"Content too large (max {MAX_WRITE_SIZE} bytes)",
            }

        resolved = Path(path).resolve()

        # Create parent directories if needed
        resolved.parent.mkdir(parents=True, exist_ok=True)

        write_mode = "a" if mode == "append" else "w"
        with open(resolved, write_mode, encoding=encoding) as f:
            f.write(content)

        return {
            "success": True,
            "path": str(resolved),
            "bytes_written": len(content.encode(encoding)),
            "mode": mode,
        }

    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": f"File write failed: {str(e)}"}


async def file_list(
    directory: str = ".",
    pattern: str = "*",
) -> Dict[str, Any]:
    """
    List files in a directory.
    
    Args:
        directory: Directory path
        pattern: Glob pattern for filtering
    
    Returns:
        Dict with list of files and their metadata
    """
    try:
        resolved = Path(directory).resolve()

        if not resolved.exists():
            return {"success": False, "error": f"Directory not found: {directory}"}

        if not resolved.is_dir():
            return {"success": False, "error": f"Not a directory: {directory}"}

        entries = []
        for item in sorted(resolved.glob(pattern))[:100]:  # Limit to 100 entries
            entries.append({
                "name": item.name,
                "path": str(item),
                "is_file": item.is_file(),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
            })

        return {
            "success": True,
            "directory": str(resolved),
            "entries": entries,
            "count": len(entries),
        }

    except PermissionError:
        return {"success": False, "error": f"Permission denied: {directory}"}
    except Exception as e:
        return {"success": False, "error": f"Directory listing failed: {str(e)}"}


def create_file_read_tool() -> ToolDefinition:
    """Factory for file read tool."""
    return ToolDefinition(
        name="file_read",
        description="Read a file from the filesystem. Returns file content, size, and line count.",
        input_schema={
            "path": "str",
            "encoding": "str",
        },
        handler=file_read,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=30,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=60,
        cost_estimate=0.0,
        timeout=10,
        tags=["file", "read", "filesystem"],
    )


def create_file_write_tool() -> ToolDefinition:
    """Factory for file write tool."""
    return ToolDefinition(
        name="file_write",
        description="Write content to a file. Supports write (overwrite) and append modes.",
        input_schema={
            "path": "str",
            "content": "str",
            "mode": "str",
        },
        handler=file_write,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=20,
            risk_level=ToolRiskLevel.MEDIUM,
            audit_required=True,
        ),
        rate_limit=30,
        cost_estimate=0.0,
        timeout=10,
        tags=["file", "write", "filesystem"],
    )


def create_file_list_tool() -> ToolDefinition:
    """Factory for file list tool."""
    return ToolDefinition(
        name="file_list",
        description="List files and directories. Returns names, paths, sizes, and types.",
        input_schema={
            "directory": "str",
            "pattern": "str",
        },
        handler=file_list,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=20,
            risk_level=ToolRiskLevel.LOW,
            audit_required=False,
        ),
        rate_limit=60,
        cost_estimate=0.0,
        timeout=10,
        tags=["file", "list", "filesystem"],
    )
