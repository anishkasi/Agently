class MyAgentError(Exception):
    """Base exception for MyAgent."""


class ConfigurationError(MyAgentError):
    """Raised when required configuration/environment is missing or invalid."""


class DependencyError(MyAgentError):
    """Raised when an external dependency (DB, Redis, LLM) fails."""


class ValidationError(MyAgentError):
    """Raised for invalid input to services or adapters."""


