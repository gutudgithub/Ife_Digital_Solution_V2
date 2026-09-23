import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from config.rate_limit import enforce_rate_limit

security_logger = logging.getLogger("ife.security")


class SecurityHeadersMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", settings.CONTENT_SECURITY_POLICY)
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if response.status_code in {400, 403, 429}:
            security_logger.warning(
                "Security-relevant request outcome.",
                extra={
                    "security_event": "request_rejected",
                    "request_path": request.path,
                    "request_method": request.method,
                    "response_status": response.status_code,
                    "actor_id": str(request.user.pk) if request.user.is_authenticated else None,
                },
            )
        return response


class AuthenticationRateLimitMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and request.path == "/login/":
            blocked = enforce_rate_limit(
                request,
                scope="login",
                limit=settings.LOGIN_RATE_LIMIT,
                window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
            )
            if blocked is not None:
                return blocked
        return self.get_response(request)
