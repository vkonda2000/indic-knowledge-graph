"""Rate limits and the daily spend budget, shared by the web app and the MCP server (in memory)."""

import threading
import time
from collections import deque


class RateLimiter:
    """Sliding one-hour window of events per key, kept in memory."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.lock = threading.Lock()
        self.events: dict[str, deque] = {}
        self.calls = 0

    def has_room(self, key: str) -> bool:
        """Whether `allow(key)` would succeed now, without recording anything."""
        now = time.time()
        with self.lock:
            return sum(t > now - 3600 for t in self.events.get(key, ())) < self.per_hour

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            self.calls += 1
            if self.calls % 256 == 0:  # forget clients idle for an hour, so memory stays bounded
                for idle in [k for k, q in self.events.items() if not q or q[-1] <= now - 3600]:
                    del self.events[idle]
            q = self.events.setdefault(key, deque())
            while q and q[0] <= now - 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                return False
            q.append(now)
            return True


class SpendBudget:
    """Estimated Claude spend per UTC day, shared by every visitor, kept in memory."""

    UNKNOWN_COST = 0.25  # charged when an answer reports no cost (unpriced model, or an error)

    def __init__(self, usd_per_day: float):
        self.limit = usd_per_day
        self.lock = threading.Lock()
        self.day, self.spent = "", 0.0

    def _roll(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.spent = today, 0.0

    def available(self) -> bool:
        with self.lock:
            self._roll()
            return self.spent < self.limit

    def charge(self, usd: float | None):
        with self.lock:
            self._roll()
            self.spent += self.UNKNOWN_COST if usd is None else usd
