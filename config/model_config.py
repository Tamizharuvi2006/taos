"""
TAOS Model Orchestration Configuration.
Maps each cognitive role (planner, executor, reflector) to a specific LLM.
Supports fallback chains for production resilience.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from taos.config.settings import get_settings


@dataclass(frozen=True)
class ModelConfig:
    """Immutable model configuration for a single cognitive role."""

    model_id: str
    temperature: float = 0.0
    max_tokens: int = 4096
    top_p: float = 1.0
    timeout: int = 30
    fallback_model: Optional[str] = None

    def to_api_params(self) -> dict:
        """Convert to OpenRouter API parameters."""
        return {
            "model": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }


@dataclass(frozen=True)
class ModelOrchestration:
    """
    Production model orchestration layer.
    
    - Planner uses GPT-4.1 for control/strategy generation
    - Executor uses Qwen 3.6 Plus for task execution/tool work
    - Reflector uses GPT-4.1 for evaluation/confidence scoring
    - Fallback chain activates on provider failure
    """

    planner: ModelConfig = field(default_factory=lambda: ModelConfig(
        model_id=get_settings().planner_model,
        temperature=0.0,
        max_tokens=4096,
        timeout=60,
        fallback_model=get_settings().fallback_model,
    ))
    executor: ModelConfig = field(default_factory=lambda: ModelConfig(
        model_id=get_settings().executor_model,
        temperature=0.0,
        max_tokens=2048,
        timeout=30,
        fallback_model=get_settings().fallback_model,
    ))
    reflector: ModelConfig = field(default_factory=lambda: ModelConfig(
        model_id=get_settings().reflection_model,
        temperature=0.0,
        max_tokens=1024,
        timeout=20,
        fallback_model=get_settings().fallback_model,
    ))

    def get_config(self, role: str) -> ModelConfig:
        """Get model config by role name."""
        configs = {
            "planner": self.planner,
            "executor": self.executor,
            "reflector": self.reflector,
        }
        if role not in configs:
            raise ValueError(f"Unknown model role: {role}. Valid: {list(configs.keys())}")
        return configs[role]

    def get_fallback(self, role: str) -> Optional[ModelConfig]:
        """Get fallback model config for a role."""
        config = self.get_config(role)
        if config.fallback_model:
            return ModelConfig(
                model_id=config.fallback_model,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                timeout=config.timeout + 10,
            )
        return None
