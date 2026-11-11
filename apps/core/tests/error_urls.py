from django.http import HttpRequest, HttpResponse
from django.urls import path

from config.urls import handler403, urlpatterns

__all__ = ["handler403", "urlpatterns"]


def server_error(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("A test-only server error")


urlpatterns = [*urlpatterns, path("test-error/", server_error)]
