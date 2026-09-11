"""观测指标（§7.4）。轻量内存实现，可渲染 Prometheus 文本，无需额外依赖。"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class Metrics:
    """进程内计数/累加器，导出为 Prometheus 文本格式。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            self._counters[key] += value

    def record_request(self, model: str, status: str, latency_ms: int, cost_usd: float) -> None:
        self.inc("llm_requests_total", {"model": model, "status": status})
        self.inc("llm_latency_ms_sum", {"model": model}, float(latency_ms))
        self.inc("llm_cost_usd_total", {"model": model}, cost_usd)

    def record_fallback(self) -> None:
        self.inc("llm_fallback_total")

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                if labels:
                    label_str = ",".join(f'{k}="{v}"' for k, v in labels)
                    lines.append(f"{name}{{{label_str}}} {value}")
                else:
                    lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"
