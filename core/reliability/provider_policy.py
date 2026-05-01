from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    failure_threshold: int
    cooldown_seconds: int
    fallback_allowed: bool
    cache_fallback_allowed: bool


DEFAULT_PROVIDER_POLICIES: Dict[str, ProviderPolicy] = {
    "openrouter": ProviderPolicy(
        name="openrouter",
        timeout_seconds=20.0,
        max_retries=1,
        retry_backoff_seconds=1.5,
        failure_threshold=3,
        cooldown_seconds=60,
        fallback_allowed=True,
        cache_fallback_allowed=False,
    ),
    "serper": ProviderPolicy(
        name="serper",
        timeout_seconds=8.0,
        max_retries=1,
        retry_backoff_seconds=1.0,
        failure_threshold=3,
        cooldown_seconds=60,
        fallback_allowed=True,
        cache_fallback_allowed=True,
    ),
    "npm_registry": ProviderPolicy(
        name="npm_registry",
        timeout_seconds=2.5,
        max_retries=1,
        retry_backoff_seconds=0.5,
        failure_threshold=3,
        cooldown_seconds=60,
        fallback_allowed=True,
        cache_fallback_allowed=True,
    ),
    "web_extract": ProviderPolicy(
        name="web_extract",
        timeout_seconds=6.0,
        max_retries=0,
        retry_backoff_seconds=0.5,
        failure_threshold=3,
        cooldown_seconds=60,
        fallback_allowed=True,
        cache_fallback_allowed=False,
    ),
    "firebase": ProviderPolicy(
        name="firebase",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.0,
        failure_threshold=3,
        cooldown_seconds=60,
        fallback_allowed=True,
        cache_fallback_allowed=False,
    ),
}


def get_provider_policy(name: str) -> ProviderPolicy:
    key = str(name or "").strip().lower()
    return DEFAULT_PROVIDER_POLICIES.get(
        key,
        ProviderPolicy(
            name=key or "unknown",
            timeout_seconds=10.0,
            max_retries=0,
            retry_backoff_seconds=0.5,
            failure_threshold=3,
            cooldown_seconds=60,
            fallback_allowed=True,
            cache_fallback_allowed=False,
        ),
    )
