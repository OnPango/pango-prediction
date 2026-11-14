"""Runtime configuration, read from the environment.

Deliberately built on the standard library: the service needs three values, and
a settings library would be a dependency bought for convenience.

The service credential is required. A prediction service deployed without one
would accept any caller, so loading fails loudly instead of starting open.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

SERVICE_NAME = "pango-prediction"
SERVICE_VERSION = "0.1.0"
CONTRACT_VERSION = "1.2"
"""The newest envelope this service serves.

1.1 added the demand profile and the business-day covariate to the response.
Both are additive, which section 3 of `docs/contract-v1.md` keeps inside 1.x,
so a client that ignores fields it does not know is unaffected.

1.2 adds the `no_forecast` status, and a status is not additive: a client that
knows `ok` and `error` may branch on exactly those two. The envelope a client
receives therefore follows the version it declares, and this one goes only to
a client that declares 1.2.
"""

LEGACY_CONTRACT_VERSION = "1.1"
"""The envelope served to a client that declares 1.0 or 1.1.

Such a client is told about a declined series the way it was before 1.2
existed: as the error it already handles.
"""

SERVICE_TOKEN_ENV = "PANGO_PREDICTION_SERVICE_TOKEN"
MAX_SERIES_ENV = "PANGO_PREDICTION_MAX_SERIES_PER_REQUEST"
LOG_LEVEL_ENV = "PANGO_PREDICTION_LOG_LEVEL"

DEFAULT_MAX_SERIES_PER_REQUEST = 500
DEFAULT_LOG_LEVEL = "INFO"


@dataclass(frozen=True, slots=True)
class Settings:
    """Values the service needs at runtime.

    ``service_token`` is the shared credential Pango Core presents. It is never
    an end-user JWT: this service does not know PANGO users or roles.
    """

    service_token: str
    max_series_per_request: int
    log_level: str = DEFAULT_LOG_LEVEL
    """Level for the service's own records, which the contract's section 20
    request log is emitted at. Defaulted so a test need not supply it."""


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from ``env``, defaulting to the process environment."""
    source = os.environ if env is None else env

    token = source.get(SERVICE_TOKEN_ENV, "").strip()

    if not token:
        raise RuntimeError(
            f"{SERVICE_TOKEN_ENV} is not set. The service refuses to start "
            "without a service credential."
        )

    raw_max = source.get(MAX_SERIES_ENV, "").strip()

    if not raw_max:
        max_series = DEFAULT_MAX_SERIES_PER_REQUEST
    else:
        try:
            max_series = int(raw_max)
        except ValueError as error:
            raise RuntimeError(f"{MAX_SERIES_ENV} must be an integer") from error

    if max_series < 1:
        raise RuntimeError(f"{MAX_SERIES_ENV} must be at least 1")

    level = source.get(LOG_LEVEL_ENV, "").strip().upper() or DEFAULT_LOG_LEVEL

    if level not in logging.getLevelNamesMapping():
        raise RuntimeError(
            f"{LOG_LEVEL_ENV} must be a logging level name, got {level!r}"
        )

    return Settings(
        service_token=token,
        max_series_per_request=max_series,
        log_level=level,
    )
