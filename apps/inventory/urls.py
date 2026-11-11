from django.urls import path

from apps.inventory import views
from apps.inventory.views.imports import upload

urlpatterns = [
    path("import/<slug:kind>/", upload, name="stock_import"),
    path("", views.balances, name="stock_balances"),
    path("movements/", views.movements, name="stock_movements"),
    path("post/<slug:action>/", views.post_movement, name="stock_post"),
    path("reverse/<int:pk>/", views.reverse, name="stock_reverse"),
    path("quality/<int:pk>/", views.quality, name="batch_quality"),
    path("stocktakes/", views.stocktakes, name="stocktakes"),
    path("stocktakes/new/", views.stocktake_new, name="stocktake_new"),
    path("stocktakes/<int:pk>/", views.stocktake_detail, name="stocktake_detail"),
    path("alerts/", views.alerts, name="stock_alerts"),
    path("audit/", views.history, name="stock_audit"),
]
