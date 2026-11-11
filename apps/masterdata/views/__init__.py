from functools import reduce
from operator import or_
from typing import cast

from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.inventory.services.common import audit, require
from apps.masterdata.forms import CATALOGS
from apps.masterdata.models import Supplier
from apps.masterdata.services import save_record

TITLES = {
    "items": _("Items"),
    "categories": _("Categories"),
    "units": _("Units"),
    "warehouses": _("Warehouses"),
    "locations": _("Locations"),
    "suppliers": _("Suppliers"),
    "conversions": _("Unit conversions"),
}


@login_required
@require_http_methods(["GET"])
@never_cache
def catalog_index(request: HttpRequest) -> HttpResponse:
    if not any(
        request.user.has_perm(f"masterdata.view_{model._meta.model_name}")
        for model, _, _ in CATALOGS.values()
    ):
        raise PermissionDenied
    return render(request, "masterdata/index.html", {"show_workspace": True})


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def catalog(
    request: HttpRequest, slug: str, action: str = "list", pk: int | None = None
) -> HttpResponse:
    if slug not in CATALOGS:
        raise Http404
    model, form_class, search = CATALOGS[slug]
    actor = cast(User, request.user)
    name = model._meta.model_name
    require(actor, f"masterdata.view_{name}")
    context = {
        "show_workspace": True,
        "slug": slug,
        "title": TITLES[slug],
    }
    if action == "list":
        if request.method != "GET":
            return HttpResponse(status=405)
        query = request.GET.get("q", "").strip()[:120]
        active = request.GET.get("active", "yes")
        rows = model._default_manager.all()
        if active in {"yes", "no"}:
            rows = rows.filter(active=active == "yes")
        if query:
            rows = rows.filter(
                reduce(or_, (Q(**{f"{field}__icontains": query}) for field in search))
            )
        page = Paginator(rows, 30).get_page(request.GET.get("page"))
        return render(
            request,
            "masterdata/list.html",
            {
                **context,
                "page": page,
                "q": query,
                "active": active,
                "can_add": actor.has_perm(f"masterdata.add_{name}"),
            },
        )
    record = get_object_or_404(model, pk=pk) if pk is not None else None
    if action == "detail":
        if request.method != "GET":
            return HttpResponse(status=405)
        if record is None:
            raise Http404
        if isinstance(record, Supplier) and record.tin:
            audit(actor, "supplier_view", record, {}, {})
        form = form_class(instance=record)
        fields = []
        for field in form:
            value = field.value()
            if isinstance(field.field, forms.ModelChoiceField) and field.field.queryset is not None:
                choice = field.field.queryset.filter(pk=value).first()
                value = field.field.label_from_instance(choice) if choice else None
            elif isinstance(field.field, forms.ChoiceField):
                display = getattr(record, f"get_{field.name}_display", None)
                if display is not None:
                    value = display()
            elif isinstance(value, bool):
                value = _("Active") if value else _("Archived")
            fields.append((field.label, value))
        return render(
            request,
            "masterdata/detail.html",
            {
                **context,
                "record": record,
                "fields": fields,
                "can_edit": actor.has_perm(f"masterdata.change_{name}"),
            },
        )
    require(actor, f"masterdata.{'change' if record is not None else 'add'}_{name}")
    if request.method == "GET" and isinstance(record, Supplier) and record.tin:
        audit(actor, "supplier_view", record, {}, {})
    form = form_class(instance=record)
    if request.method == "POST":
        try:
            form, saved = save_record(actor, slug, request.POST, pk)
            if saved is not None:
                return redirect("catalog_detail", slug=slug, pk=saved.pk)
        except (IntegrityError, ValidationError):
            form = form_class(request.POST, instance=record)
            form.is_valid()
            form.add_error(None, _("The record conflicts with an existing record."))
    return render(
        request, "workspace/form.html", {**context, "form": form, "confirm": bool(record)}
    )
