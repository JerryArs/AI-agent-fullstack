"""配额 / 限流 / 预算（§7.3）。默认内存实现，可关闭。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from mini_llm_gateway.protocol.errors import ErrorCode


class QuotaError(Exception):
    """治理阈值触发；映射为 HTTP 429。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code.value
        self.message = message
        self.status_code = 429
        super().__init__(message)


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimiter:
    """令牌桶：按身份限制 RPM，支持突发 burst。"""

    enabled: bool = False
    requests_per_minute: int = 60
    burst: int = 60
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def check(self, identity: str) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        refill_per_second = self.requests_per_minute / 60.0
        bucket = self._buckets.get(identity)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.burst), updated_at=now)
            self._buckets[identity] = bucket
        else:
            elapsed = now - bucket.updated_at
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * refill_per_second)
            bucket.updated_at = now
        if bucket.tokens < 1.0:
            raise QuotaError(ErrorCode.rate_limited, "请求过于频繁")
        bucket.tokens -= 1.0


@dataclass
class BudgetGuard:
    """成本预算：窗口内累计成本超阈值即拒绝。"""

    enabled: bool = False
    max_cost_usd: float = 0.0
    _spent: float = 0.0

    def check(self) -> None:
        if self.enabled and self._spent >= self.max_cost_usd:
            raise QuotaError(ErrorCode.budget_exceeded, "已超出成本预算")

    def add(self, cost_usd: float) -> None:
        self._spent += cost_usd

    @property
    def spent(self) -> float:
        return self._spent


@dataclass
class QuotaManager:
    """统一的请求前置治理检查。"""

    rate_limiter: RateLimiter
    budget: BudgetGuard

    def check(self, identity: str) -> None:
        self.rate_limiter.check(identity)
        self.budget.check()

    def record_cost(self, cost_usd: float) -> None:
        self.budget.add(cost_usd)
