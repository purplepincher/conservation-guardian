"""Custom exceptions for Conservation Guardian."""

from __future__ import annotations


class ConservationGuardianError(Exception):
    """Base exception for all Conservation Guardian errors."""


class BudgetExceededError(ConservationGuardianError):
    """Raised when a workflow exceeds its configured budget limits."""

    def __init__(
        self,
        message: str = "Budget exceeded",
        *,
        metric: str = "",
        current: float = 0.0,
        limit: float = 0.0,
    ) -> None:
        self.metric = metric
        self.current = current
        self.limit = limit
        super().__init__(message)


class InvalidProfileError(ConservationGuardianError):
    """Raised when profile data is invalid or corrupted."""
    pass


class AdapterError(ConservationGuardianError):
    """Raised when a data source adapter encounters an error."""

    def __init__(self, message: str = "Adapter error", *, adapter_name: str = "", cause: Exception | None = None) -> None:
        self.adapter_name = adapter_name
        self.cause = cause
        super().__init__(message)
