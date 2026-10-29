class LLMError(Exception):
    """Base exception for all LLM module errors."""


class LLMConfigurationError(LLMError):
    """Raised when LLM configuration or provider setup is missing/invalid."""


class LLMProviderError(LLMError):
    """Raised when the downstream LLM provider API call fails."""


class StructuredValidationError(LLMError):
    """Raised when structured output fails schema validation after max retries."""


class LLMBudgetExceeded(LLMError):
    """Raised when job or daily budget limits are exceeded."""
