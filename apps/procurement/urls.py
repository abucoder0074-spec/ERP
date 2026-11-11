from django.urls import path

from apps.procurement import views

urlpatterns = [
    path("approvals/", views.inbox, name="procurement_inbox"),
    path("approvals/<int:pk>/", views.decision, name="procurement_decision"),
    path("tracking/", views.deliveries, name="procurement_deliveries"),
    path("versions/<int:pk>/", views.versions, name="procurement_version"),
    path("attachments/<int:pk>/", views.download, name="procurement_download"),
    path("policy/<slug:kind>/", views.policy, name="procurement_policy"),
    path("policy/<slug:kind>/<int:pk>/", views.policy, name="procurement_policy_edit"),
    path("action/<slug:action>/", views.action, name="procurement_new"),
    path("action/<slug:action>/<int:pk>/", views.action, name="procurement_action"),
    path("<slug:kind>/<int:pk>/upload/", views.upload, name="procurement_upload"),
    path("<slug:kind>/<int:pk>/", views.detail, name="procurement_detail"),
    path("<slug:kind>/", views.listing, name="procurement_list"),
]
