from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from redis.exceptions import RedisError

from apps.core.forms import WorkspaceAuthenticationForm
from apps.core.services.login import LoginUnavailable, login_attempt, login_redis


class WorkspaceLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = WorkspaceAuthenticationForm
    extra_context = {"show_language": True}

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        try:
            with login_attempt(
                request.META.get("REMOTE_ADDR", ""), request.POST.get("username", "")
            ) as attempt:
                if attempt.retry_after:
                    response = render(request, "429.html", status=429)
                    response["Retry-After"] = str(attempt.retry_after)
                    return response
                form: AuthenticationForm = self.get_form()
                attempt.succeeded = form.is_valid()
        except LoginUnavailable:
            return render(request, "503.html", status=503)
        if attempt.succeeded:
            return self.form_valid(form)
        return self.form_invalid(form)


@require_safe
@never_cache
@login_required
@permission_required("core.access_workspace", raise_exception=True)
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "home.html", {"show_workspace": True, "show_language": True})


@never_cache
def forbidden(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(request, "403.html", {"show_logout": request.user.is_authenticated}, status=403)


@never_cache
def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    return render(request, "403.html", status=403)


@require_safe
@never_cache
def health(request: HttpRequest) -> JsonResponse:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        with login_redis() as cache:
            if cache.ping() is not True:
                return JsonResponse({"status": "unavailable"}, status=503)
    except (DatabaseError, RedisError):
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
