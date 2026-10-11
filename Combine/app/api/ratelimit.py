"""Simple in-process sliding-window rate limiter.

Keyed by user id when authenticated, else client IP. Suitable for a single
API instance (demo); use a shared store (e.g. Redis) behind a load balancer.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                retry = int(window - (now - hits[0])) + 1
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many requests; please wait and try again",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth:
        return "tok:" + hashlib.sha256(auth.encode()).hexdigest()[:32]
    return "ip:" + (request.client.host if request.client else "unknown")


def rate_limit(bucket: str, per_minute_attr: str):
    """FastAPI dependency factory; limit read from Settings by attribute name."""

    def dep(request: Request, settings: Settings = Depends(get_settings)) -> None:
        limiter.check(f"{bucket}:{client_key(request)}", getattr(settings, per_minute_attr))

    return dep
