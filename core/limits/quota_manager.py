"""Simple in-memory rate limiting and daily quota controls."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple

from taos.config.settings import get_settings


@dataclass
class QuotaDecision:
    allowed: bool
    reason: str = ""
    code: str = ""
    retry_after_seconds: int | None = None
    limit_per_minute: int | None = None
    daily_quota: int | None = None
    route: str = ""
    owner: str = ""
    effective_tier: str = "free"
    perf_mode_applied: bool = False

class QuotaManager:
    """
    Per-user controls:
    - per-minute rate limit
    - daily quota by tier
    """

    DAILY_QUOTA = {
        "free": 200,
        "paid": 5000,
        "enterprise": 50000,
    }
    PER_MINUTE_LIMIT = {
        "free": 100,
        "paid": 120,
        "enterprise": 600,
    }

    def __init__(self) -> None:
        self._minute_windows: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._daily_counts: Dict[Tuple[str, str, int], int] = defaultdict(int)

    def check_and_consume_detailed(
        self,
        user_id: str,
        tier: str = "free",
        *,
        route: str = "",
        owner: str = "",
        perf_test_mode_requested: bool = False,
    ) -> QuotaDecision:
        settings = get_settings()
        safe_user_id = str(user_id or "anonymous").strip() or "anonymous"
        safe_tier = (tier or "free").lower()
        if safe_tier not in self.DAILY_QUOTA:
            safe_tier = "free"

        now = time.time()
        minute_key = (safe_user_id, safe_tier)
        day_key = (safe_user_id, safe_tier, int(now // 86400))
        minute_q = self._minute_windows[minute_key]
        while minute_q and (now - minute_q[0]) > 60:
            minute_q.popleft()

        minute_limit = int(self.PER_MINUTE_LIMIT[safe_tier])
        daily_quota = int(self.DAILY_QUOTA[safe_tier])

        perf_mode_applied = bool(
            settings.is_development
            and bool(settings.auth_allow_dev_bypass)
            and bool(settings.perf_test_mode or perf_test_mode_requested)
            and str(settings.perf_test_user_id or "").strip()
            and safe_user_id == str(settings.perf_test_user_id).strip()
        )
        if perf_mode_applied:
            minute_limit = max(minute_limit, int(max(1, settings.perf_test_per_minute_limit)))

        if len(minute_q) >= minute_limit:
            retry_after = 60
            if minute_q:
                retry_after = max(1, int(60 - (now - minute_q[0])))
            return QuotaDecision(
                allowed=False,
                reason=f"Rate limit exceeded ({minute_limit}/minute)",
                code="RATE_LIMITED",
                retry_after_seconds=retry_after,
                limit_per_minute=minute_limit,
                daily_quota=daily_quota,
                route=str(route or "").strip(),
                owner=str(owner or "").strip(),
                effective_tier=safe_tier,
                perf_mode_applied=perf_mode_applied,
            )

        day_count = self._daily_counts[day_key]
        if day_count >= daily_quota:
            seconds_until_next_day = max(1, int(((int(now // 86400) + 1) * 86400) - now))
            return QuotaDecision(
                allowed=False,
                reason=f"Daily quota exceeded ({daily_quota}/day)",
                code="DAILY_QUOTA_EXCEEDED",
                retry_after_seconds=seconds_until_next_day,
                limit_per_minute=minute_limit,
                daily_quota=daily_quota,
                route=str(route or "").strip(),
                owner=str(owner or "").strip(),
                effective_tier=safe_tier,
                perf_mode_applied=perf_mode_applied,
            )

        minute_q.append(now)
        self._daily_counts[day_key] += 1
        return QuotaDecision(
            allowed=True,
            reason="",
            code="",
            retry_after_seconds=None,
            limit_per_minute=minute_limit,
            daily_quota=daily_quota,
            route=str(route or "").strip(),
            owner=str(owner or "").strip(),
            effective_tier=safe_tier,
            perf_mode_applied=perf_mode_applied,
        )

    def check_and_consume(self, user_id: str, tier: str = "free") -> Tuple[bool, str]:
        decision = self.check_and_consume_detailed(user_id=user_id, tier=tier)
        return bool(decision.allowed), str(decision.reason or "")


_QUOTA = QuotaManager()


def get_quota_manager() -> QuotaManager:
    return _QUOTA
