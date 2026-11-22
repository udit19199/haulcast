"""HTTP and JSON plumbing shared by the provider clients."""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.cookiejar import DefaultCookiePolicy
from time import monotonic

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

Json = object

# Shared so provider calls reuse keep-alive connections. Sized for Gunicorn's 8 threads;
# cookies are refused so the session holds no per-request state.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))


class ProviderError(Exception):
    pass


class NoRouteError(Exception):
    def __init__(self) -> None:
        super().__init__("No truck route was found between these places.")


def get_json(
    url: str,
    params: dict[str, str | int],
    service: str,
    no_route_400: Callable[[Json], bool] | None = None,
) -> Json:
    timeout = settings.PROVIDER_TIMEOUT_S
    started = monotonic()
    try:
        response = _session.get(url, params=params, timeout=timeout)
    except requests.RequestException:
        log_failure(service, None, started)
        raise ProviderError(f"{service} is unavailable right now.") from None

    status = response.status_code
    if status >= 400:
        if status == 400 and no_route_400 is not None:
            try:
                body = response.json()
            except ValueError:
                body = None
            if no_route_400(body):
                raise NoRouteError()
        log_failure(service, status, started)
        if status == 400:
            raise ProviderError(f"{service} rejected the request.")
        if status == 429:
            raise ProviderError(f"{service} is busy right now. Try again in a minute.")
        raise ProviderError(f"{service} is unavailable right now.")

    try:
        return response.json()
    except ValueError:
        log_failure(service, status, started)
        raise ProviderError(f"{service} returned an unreadable response.") from None


def log_failure(service: str, status: int | None, started: float) -> None:
    logger.warning(
        "provider failure service=%s status=%s elapsed_ms=%d",
        service,
        status if status is not None else "none",
        round((monotonic() - started) * 1000),
    )


def on_hour(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def chunks[T](items: list[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
