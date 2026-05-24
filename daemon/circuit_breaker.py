# daemon/circuit_breaker.py
"""Circuit breaker pattern for external service resilience."""

from __future__ import annotations

import time
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger("vault-memoryd.circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject calls
    HALF_OPEN = "half_open" # Testing recovery


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and service is unavailable."""
    pass


# Global registry for circuit breakers (populated during lifespan)
_circuit_breakers: dict[str, CircuitBreaker] = {}


def register_circuit_breaker(cb: CircuitBreaker):
    """Register a circuit breaker in the global registry."""
    _circuit_breakers[cb.name] = cb


def get_circuit_breaker(name: str) -> Optional[CircuitBreaker]:
    """Get a circuit breaker by name."""
    return _circuit_breakers.get(name)


def get_all_circuit_breakers() -> dict[str, dict]:
    """Get all circuit breaker states for health checks."""
    return {name: cb.get_state() for name, cb in _circuit_breakers.items()}


class CircuitBreaker:
    """Circuit breaker for external services (embedder, Weaviate, Ollama).

    Conservative defaults: 5 failures before opening, 120s recovery timeout.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 120.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self.last_failure_time and (time.time() - self.last_failure_time) >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker '%s' transitioning to HALF_OPEN", self.name)
                return True
            return False
        # HALF_OPEN: allow one test call
        return True

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker '%s' recovered to CLOSED", self.name)
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker '%s' OPEN after %d failures",
                self.name, self.failure_count
            )

    async def execute(self, fn, *args, **kwargs):
        if not self.can_execute():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is OPEN — service unavailable"
            )
        try:
            result = await fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise

    def get_state(self) -> dict:
        """Return circuit breaker state for health checks."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self.last_failure_time,
        }
