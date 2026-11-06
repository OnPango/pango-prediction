from datetime import date

import pytest

from pango_prediction.config import (
    DEFAULT_MAX_SERIES_PER_REQUEST,
    MAX_SERIES_ENV,
    SERVICE_TOKEN_ENV,
    load_settings,
)


def test_a_missing_service_token_refuses_to_load() -> None:
    """A service deployed without a credential would accept any caller."""
    with pytest.raises(RuntimeError, match=SERVICE_TOKEN_ENV):
        load_settings(env={})


def test_a_blank_service_token_refuses_to_load() -> None:
    with pytest.raises(RuntimeError, match=SERVICE_TOKEN_ENV):
        load_settings(env={SERVICE_TOKEN_ENV: "   "})


def test_the_service_token_is_stripped() -> None:
    settings = load_settings(env={SERVICE_TOKEN_ENV: "  secret  "})

    assert settings.service_token == "secret"


def test_the_batch_limit_defaults_when_unset() -> None:
    settings = load_settings(env={SERVICE_TOKEN_ENV: "secret"})

    assert settings.max_series_per_request == DEFAULT_MAX_SERIES_PER_REQUEST


def test_the_batch_limit_is_read_from_the_environment() -> None:
    settings = load_settings(
        env={SERVICE_TOKEN_ENV: "secret", MAX_SERIES_ENV: "25"}
    )

    assert settings.max_series_per_request == 25


def test_a_non_numeric_batch_limit_refuses_to_load() -> None:
    with pytest.raises(RuntimeError, match=MAX_SERIES_ENV):
        load_settings(env={SERVICE_TOKEN_ENV: "secret", MAX_SERIES_ENV: "many"})


def test_a_batch_limit_below_one_refuses_to_load() -> None:
    with pytest.raises(RuntimeError, match=MAX_SERIES_ENV):
        load_settings(env={SERVICE_TOKEN_ENV: "secret", MAX_SERIES_ENV: "0"})


def test_settings_are_immutable() -> None:
    """Frozen dataclass: configuration cannot drift while the service runs."""
    settings = load_settings(env={SERVICE_TOKEN_ENV: "secret"})

    with pytest.raises(AttributeError):
        settings.service_token = "other"  # type: ignore[misc]


def test_the_module_does_not_depend_on_the_process_environment() -> None:
    """`load_settings` takes its source, which is what makes it testable."""
    settings = load_settings(
        env={SERVICE_TOKEN_ENV: f"token-{date(2026, 11, 6).isoformat()}"}
    )

    assert settings.service_token == "token-2026-11-06"
