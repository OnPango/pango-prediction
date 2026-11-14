"""Request-level HTTP failures.

Per-series failures are not here: they travel inside a 200 response as a
status and a stable code (plan B17), because one bad series must not invalidate
a batch. These are the failures that affect the whole request.

Contract v1 §17 lists the status codes. FastAPI answers a schema violation with
422, which is the contract's code for a semantically invalid request.
"""

from fastapi import HTTPException, status


def unauthorized() -> HTTPException:
    """Missing or malformed service credential."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid service credential is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def batch_too_large(count: int, limit: int) -> HTTPException:
    """More series than the service accepts in one call (contract v1 §19)."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"The request carries {count} series, more than the limit of "
            f"{limit}. Split the job into several calls."
        ),
    )


def horizon_mismatch(message: str) -> HTTPException:
    """A horizon other than the one the loaded policy is calibrated for.

    The application decides that this is a fault and words it; this only
    chooses the status code.
    """
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=message,
    )
