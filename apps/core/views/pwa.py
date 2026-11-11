from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import override
from django.views.decorators.http import require_safe


@require_safe
def manifest(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "id": reverse("home"),
            "name": "Muzqaymoq ERP",
            "short_name": "Muzqaymoq",
            "start_url": reverse("home"),
            "display": "standalone",
            "background_color": "#f5f5f4",
            "theme_color": "#152821",
            "icons": [
                {
                    "src": static(f"icon-{size}.png"),
                    "sizes": f"{size}x{size}",
                    "type": "image/png",
                }
                for size in (192, 512)
            ],
        },
        content_type="application/manifest+json",
    )


@require_safe
def service_worker(request: HttpRequest) -> HttpResponse:
    script = settings.BASE_DIR / "static" / "service-worker.js"
    response = HttpResponse(script.read_bytes(), content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response


@require_safe
def offline(request: HttpRequest) -> HttpResponse:
    with override(settings.LANGUAGE_CODE):
        response = render(request, "offline.html")
    response["Content-Language"] = settings.LANGUAGE_CODE
    return response
