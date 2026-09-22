import hashlib
import hmac
import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from django.conf import settings
from django.core.cache import caches
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.utils.translation import gettext as _

View = Callable[..., HttpResponseBase]
RateLimitValue = int | Callable[[], int]
Response = TypeVar("Response", bound=HttpResponseBase)


def _rate_limit_identity(request: HttpRequest) -> str:
    if request.user.is_authenticated:
        return f"user:{request.user.pk}"
    return f"network:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _rate_limit_key(
    request: HttpRequest,
    *,
    scope: str,
    window_seconds: int,
) -> str:
    bucket = int(time.time()) // window_seconds
    raw_key = f"{scope}:{_rate_limit_identity(request)}:{bucket}"
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        raw_key.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"ife-rate-limit:{digest}"


def enforce_rate_limit(
    request: HttpRequest,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
) -> HttpResponse | None:
    key = _rate_limit_key(request, scope=scope, window_seconds=window_seconds)
    cache = caches[settings.RATE_LIMIT_CACHE_ALIAS]
    if cache.add(key, 1, timeout=window_seconds + 5):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window_seconds + 5)
            count = 1
    if count <= limit:
        return None
    response = HttpResponse(
        _("Too many requests. Wait before trying again."),
        status=429,
        content_type="text/plain",
    )
    response["Retry-After"] = str(window_seconds)
    response["Cache-Control"] = "no-store"
    return response


def rate_limit(
    *,
    scope: str,
    limit: RateLimitValue,
    window_seconds: RateLimitValue,
    methods: tuple[str, ...] = ("POST",),
) -> Callable[[Callable[..., Response]], View]:
    def decorator(view: Callable[..., Response]) -> View:
        @wraps(view)
        def wrapped(
            request: HttpRequest,
            *args: object,
            **kwargs: object,
        ) -> HttpResponseBase:
            if request.method in methods:
                resolved_limit = limit() if callable(limit) else limit
                resolved_window = window_seconds() if callable(window_seconds) else window_seconds
                blocked = enforce_rate_limit(
                    request,
                    scope=scope,
                    limit=resolved_limit,
                    window_seconds=resolved_window,
                )
                if blocked is not None:
                    return blocked
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
