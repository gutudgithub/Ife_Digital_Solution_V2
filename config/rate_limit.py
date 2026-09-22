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
    bucket: int,
) -> str:
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
    if window_seconds <= 0:
        raise ValueError("Rate-limit windows must be positive.")

    now = time.time()
    current_bucket = int(now // window_seconds)
    elapsed_seconds = now - (current_bucket * window_seconds)
    previous_weight = (window_seconds - elapsed_seconds) / window_seconds
    current_key = _rate_limit_key(request, scope=scope, bucket=current_bucket)
    previous_key = _rate_limit_key(request, scope=scope, bucket=current_bucket - 1)
    cache = caches[settings.RATE_LIMIT_CACHE_ALIAS]
    cache_timeout = (window_seconds * 2) + 5
    if cache.add(current_key, 1, timeout=cache_timeout):
        current_count = 1
    else:
        try:
            current_count = cache.incr(current_key)
        except ValueError:
            cache.set(current_key, 1, timeout=cache_timeout)
            current_count = 1
    cached_previous_count = cache.get(previous_key, 0)
    previous_count = cached_previous_count if isinstance(cached_previous_count, int) else 0
    estimated_count = current_count + (previous_count * previous_weight)
    if estimated_count <= limit:
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
