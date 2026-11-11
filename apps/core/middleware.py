import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.middleware.locale import LocaleMiddleware
from django.utils.cache import patch_vary_headers
from django.utils.translation import activate

from config.environment import canonical_ip

logger = logging.getLogger("erp.request")


class RequestLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                    timespec="seconds"
                ),
                "level": record.levelname,
                "logger": record.name,
                "request_id": getattr(record, "request_id", None),
                "route": getattr(record, "route_name", None),
                "status": getattr(record, "status_code", None),
            }
        )


class RequestContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = uuid4().hex
        peer = canonical_ip(request.META.get("REMOTE_ADDR", ""))
        if peer in settings.TRUSTED_PROXY_IPS:
            client_ip = canonical_ip(request.META.get("HTTP_X_REAL_IP", ""))
            if client_ip:
                request.META["REMOTE_ADDR"] = client_ip
        else:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)

        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        response["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "route_name": request.resolver_match.view_name if request.resolver_match else None,
                "status_code": response.status_code,
            },
        )
        return response


class WorkspaceLocaleMiddleware(LocaleMiddleware):
    def process_request(self, request: HttpRequest) -> None:
        choice = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        language = choice if choice in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
        activate(language)
        request.LANGUAGE_CODE = language

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        response = super().process_response(request, response)
        patch_vary_headers(response, ("Cookie",))
        return response
