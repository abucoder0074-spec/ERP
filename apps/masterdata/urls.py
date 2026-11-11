from django.urls import path

from apps.masterdata.views import catalog, catalog_index

urlpatterns = [
    path("", catalog_index, name="catalog_index"),
    path("<slug:slug>/", catalog, name="catalog_list"),
    path("<slug:slug>/new/", catalog, {"action": "create"}, name="catalog_create"),
    path("<slug:slug>/<int:pk>/", catalog, {"action": "detail"}, name="catalog_detail"),
    path("<slug:slug>/<int:pk>/edit/", catalog, {"action": "edit"}, name="catalog_edit"),
]
