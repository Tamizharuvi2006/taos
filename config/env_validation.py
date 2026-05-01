"""
Startup/runtime environment validation helpers for TAOS.
"""

from __future__ import annotations

from typing import Any, Dict

from taos.config.settings import Settings
from taos.core.deployment.env_validator import validate_production_environment


def validate_runtime_environment(settings: Settings) -> Dict[str, Any]:
    return validate_production_environment(settings)
