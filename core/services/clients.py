"""HTTP clients for distributed TAOS agent services with retry/fallback support."""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from taos.config.settings import get_settings
from taos.core.services.circuit_breaker import CircuitBreaker
from taos.core.services.contracts import (
    PlannerServiceRequest,
    PlannerServiceResponse,
    StepServiceRequest,
    StepServiceResponse,
)
from taos.core.state.state_schema import GlobalState, PlanObject, PlanStep, StepResult
from taos.infra.logging.logger import TAOSLogger


class ServiceClientError(Exception):
    pass


class AgentServiceClient:
    """Client wrapper for planner/research/execution microservices."""

    def __init__(
        self,
        logger: Optional[TAOSLogger] = None,
    ) -> None:
        self._settings = get_settings()
        self._logger = logger
        self._breaker = CircuitBreaker(
            failure_threshold=int(getattr(self._settings, "service_circuit_failures", 5)),
            cooldown_seconds=int(getattr(self._settings, "service_circuit_cooldown_seconds", 60)),
        )

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "microservices_enabled", False))

    @property
    def planner_enabled(self) -> bool:
        return self.enabled and bool(getattr(self._settings, "enable_planner_service", False))

    @property
    def research_enabled(self) -> bool:
        return self.enabled and bool(getattr(self._settings, "enable_research_service", False))

    @property
    def execution_enabled(self) -> bool:
        return self.enabled and bool(getattr(self._settings, "enable_execution_service", False))

    async def generate_plan(
        self,
        request: PlannerServiceRequest,
        request_id: Optional[str] = None,
    ) -> tuple[PlanObject, float]:
        response = await self._call_with_breaker(
            service_key="planner",
            url=f"{self._settings.planner_service_url.rstrip('/')}/planner/generate",
            payload=request.model_dump(),
            request_id=request_id,
        )
        parsed = PlannerServiceResponse.model_validate(response)
        return PlanObject.model_validate(parsed.plan), float(parsed.planning_cost)

    async def run_execution_step(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
        request_id: Optional[str] = None,
    ) -> StepResult:
        req = StepServiceRequest(
            step=step.model_dump(),
            state=state.model_dump(),
            step_index=step_index,
        )
        response = await self._call_with_breaker(
            service_key="execution",
            url=f"{self._settings.execution_service_url.rstrip('/')}/execution/run",
            payload=req.model_dump(),
            request_id=request_id,
        )
        parsed = StepServiceResponse.model_validate(response)
        return StepResult.model_validate(parsed.step_result)

    async def run_research_step(
        self,
        step: PlanStep,
        state: GlobalState,
        step_index: int,
        request_id: Optional[str] = None,
    ) -> StepResult:
        req = StepServiceRequest(
            step=step.model_dump(),
            state=state.model_dump(),
            step_index=step_index,
        )
        response = await self._call_with_breaker(
            service_key="research",
            url=f"{self._settings.research_service_url.rstrip('/')}/research/run",
            payload=req.model_dump(),
            request_id=request_id,
        )
        parsed = StepServiceResponse.model_validate(response)
        return StepResult.model_validate(parsed.step_result)

    async def _post_json(
        self,
        *,
        url: str,
        payload: dict,
        request_id: Optional[str],
    ) -> dict:
        retries = max(0, int(getattr(self._settings, "service_retries", 2)))
        timeout = float(getattr(self._settings, "service_timeout_seconds", 8.0))
        headers = {}
        if request_id:
            headers["X-Request-ID"] = request_id

        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as exc:
                last_exc = exc
                if self._logger:
                    self._logger.warning(
                        "service.client_retry",
                        url=url,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                if attempt < retries:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
        raise ServiceClientError(f"Service call failed: {url} | {last_exc}")

    async def _call_with_breaker(
        self,
        *,
        service_key: str,
        url: str,
        payload: dict,
        request_id: Optional[str],
    ) -> dict:
        if not self._breaker.allow(service_key):
            raise ServiceClientError(f"Circuit open for service '{service_key}'")
        try:
            out = await self._post_json(url=url, payload=payload, request_id=request_id)
            self._breaker.on_success(service_key)
            return out
        except Exception as e:
            self._breaker.on_failure(service_key)
            raise ServiceClientError(str(e)) from e
