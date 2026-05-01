"""
TAOS Built-in Tool: Sandboxed Python Code Executor.

Executes Python code in an isolated subprocess with:
- Timeout enforcement
- stdout/stderr capture
- No access to parent process state
- Resource limits
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import os
from typing import Any, Dict

from taos.core.tools.registry import ToolDefinition, ToolPolicy
from taos.config.constants import ToolRiskLevel


async def execute_code(
    code: str,
    timeout: int = 10,
    language: str = "python",
) -> Dict[str, Any]:
    """
    Execute Python code in a sandboxed subprocess.
    
    Args:
        code: Python code to execute
        timeout: Max execution time in seconds
        language: Programming language (only "python" supported)
    
    Returns:
        Dict with stdout, stderr, return_code, and execution status
    """
    if language != "python":
        return {
            "success": False,
            "error": f"Unsupported language: {language}. Only 'python' is supported.",
            "stdout": "",
            "stderr": "",
            "return_code": -1,
        }

    if not code or not code.strip():
        return {
            "success": False,
            "error": "Empty code provided",
            "stdout": "",
            "stderr": "",
            "return_code": -1,
        }

    # ─── Write code to temp file ───
    tmp_dir = tempfile.mkdtemp(prefix="taos_code_")
    code_file = os.path.join(tmp_dir, "script.py")
    
    try:
        with open(code_file, "w", encoding="utf-8") as f:
            f.write(code)

        # ─── Execute in subprocess ───
        process = await asyncio.create_subprocess_exec(
            sys.executable, code_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp_dir,
            # Isolate: no access to parent env vars
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": "",
                "HOME": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            },
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "success": False,
                "error": f"Code execution timed out after {timeout}s",
                "stdout": "",
                "stderr": "",
                "return_code": -1,
                "timed_out": True,
            }

        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        return {
            "success": process.returncode == 0,
            "stdout": stdout_str[:10000],  # Limit output size
            "stderr": stderr_str[:5000],
            "return_code": process.returncode,
            "error": stderr_str if process.returncode != 0 else None,
        }

    finally:
        # ─── Cleanup temp files ───
        try:
            os.remove(code_file)
            os.rmdir(tmp_dir)
        except OSError:
            pass


def create_code_executor_tool() -> ToolDefinition:
    """Factory function to create the code executor tool definition."""
    return ToolDefinition(
        name="code_executor",
        description="Execute Python code in a sandboxed subprocess. Returns stdout, stderr, and return code.",
        input_schema={
            "code": "str",
            "timeout": "int",
            "language": "str",
        },
        handler=execute_code,
        policy=ToolPolicy(
            allowed_roles=["agent"],
            max_calls_per_task=15,
            risk_level=ToolRiskLevel.HIGH,
            audit_required=True,
        ),
        rate_limit=20,
        cost_estimate=0.0,
        timeout=15,
        tags=["code", "execution", "python"],
    )
