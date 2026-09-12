from __future__ import annotations

import os
import time
from dataclasses import dataclass


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PublicDemoPolicy:
    enabled: bool
    analysis_limit: int
    window_seconds: int
    public_feishu_save_enabled: bool

    @classmethod
    def from_env(cls) -> "PublicDemoPolicy":
        return cls(
            enabled=_truthy(os.getenv("PUBLIC_DEMO_MODE"), default=False),
            analysis_limit=max(1, int(os.getenv("ANALYSIS_RATE_LIMIT", "6"))),
            window_seconds=max(10, int(os.getenv("ANALYSIS_RATE_WINDOW_SECONDS", "600"))),
            public_feishu_save_enabled=_truthy(
                os.getenv("FEISHU_PUBLIC_SAVE_ENABLED"), default=False
            ),
        )

    @property
    def can_save_to_feishu(self) -> bool:
        return not self.enabled or self.public_feishu_save_enabled


def consume_analysis_quota(
    timestamps: list[float],
    policy: PublicDemoPolicy,
    *,
    now: float | None = None,
) -> tuple[list[float], int]:
    """Return pruned timestamps and retry seconds; zero means the request is accepted."""
    current = time.time() if now is None else now
    active = [stamp for stamp in timestamps if current - stamp < policy.window_seconds]
    if not policy.enabled:
        return active, 0
    if len(active) >= policy.analysis_limit:
        retry_after = max(1, int(policy.window_seconds - (current - min(active))))
        return active, retry_after
    return [*active, current], 0
