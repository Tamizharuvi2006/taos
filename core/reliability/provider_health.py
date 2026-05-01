from __future__ import annotations

from typing import Any, Dict

from taos.core.reliability.provider_circuit import ProviderCircuit, create_provider_circuit


class ProviderHealthRegistry:
    def __init__(self) -> None:
        self._circuits: Dict[str, ProviderCircuit] = {}

    def circuit(self, name: str) -> ProviderCircuit:
        key = str(name or "unknown").strip().lower()
        if key not in self._circuits:
            self._circuits[key] = create_provider_circuit(key)
        return self._circuits[key]

    def allow_request(self, name: str) -> bool:
        return self.circuit(name).allow_request()

    def record_success(self, name: str) -> None:
        self.circuit(name).record_success()

    def record_failure(self, name: str, error: Any, *, count_failure: bool = True) -> None:
        self.circuit(name).record_failure(error, count_failure=count_failure)

    def mark_fallback(self, name: str, *, cache_used: bool = False) -> None:
        self.circuit(name).mark_fallback(cache_used=cache_used)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {name: circuit.snapshot() for name, circuit in sorted(self._circuits.items())}

    def reset(self) -> None:
        self._circuits.clear()


GLOBAL_PROVIDER_HEALTH = ProviderHealthRegistry()


def provider_health_snapshot() -> Dict[str, Dict[str, Any]]:
    return GLOBAL_PROVIDER_HEALTH.snapshot()
