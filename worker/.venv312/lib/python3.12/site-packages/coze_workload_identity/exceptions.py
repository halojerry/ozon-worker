"""Custom exceptions for the Workload Identity SDK."""


class WorkloadIdentityError(Exception):
    """Base exception for all workload identity SDK errors."""
    pass


class ConfigurationError(WorkloadIdentityError):
    """Raised when required configuration is missing or invalid."""
    pass


class TokenExchangeError(WorkloadIdentityError):
    """Raised when token exchange fails."""
    pass


class TokenRetrievalError(WorkloadIdentityError):
    """Raised when ID token retrieval fails."""
    pass