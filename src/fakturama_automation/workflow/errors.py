class AutomationError(Exception):
    """Base class for all automation failures."""


class ExtractionError(AutomationError):
    """Image extraction could not produce valid structured data."""


class ValidationError(AutomationError):
    """Extracted data failed deterministic validation before touching Fakturama."""


class ManualReviewRequired(AutomationError):
    """An ambiguous or conflicting situation requires human review."""
