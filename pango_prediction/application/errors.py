"""Stable per-series outcome codes.

These live in the application layer, not in the HTTP adapter: the use case
decides which one applies, and the adapter only renders it. Putting them in
`adapters/inbound` would force the application to import from an adapter.

The values are the stable codes of contract v1 §16. Pango Core branches on
them, so they are renamed only with a contract version bump.
"""

from enum import StrEnum


class SeriesErrorCode(StrEnum):
    INVALID_SERIES = "INVALID_SERIES"
    """The input itself is unusable: bad dates, negative demand, bad quantities."""

    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    """Not enough observable history to forecast the series."""

    UNKNOWN_AVAILABILITY = "UNKNOWN_AVAILABILITY"
    """Availability could not be established well enough to trust the demand."""

    MODEL_FAILED = "MODEL_FAILED"
    """The selected model raised while forecasting this series."""

    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    """No model is available for this series under the active policy."""

    CHECKPOINT_NOT_AVAILABLE = "CHECKPOINT_NOT_AVAILABLE"
    """A neural model was selected but its checkpoint could not be loaded."""

    UNSUPPORTED_DATA = "UNSUPPORTED_DATA"
    """The series is well formed but outside what the service can handle."""
