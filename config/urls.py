from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.i18n import set_language

from apps.core.views import WorkspaceLoginView, forbidden, health, home
from apps.core.views.pwa import manifest, offline, service_worker

urlpatterns = [
    path("stock/", include("apps.inventory.urls")),
    path("catalog/", include("apps.masterdata.urls")),
    path("manifest.webmanifest", manifest, name="manifest"),
    path("service-worker.js", service_worker, name="service_worker"),
    path("offline/", offline, name="offline"),
    path("", home, name="home"),
    path("login/", WorkspaceLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("language/", set_language, name="set_language"),
    path("health/", health, name="health"),
]

handler403 = forbidden
