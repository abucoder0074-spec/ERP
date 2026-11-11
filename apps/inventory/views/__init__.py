from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.inventory.forms import (
    CountForm,
    QualityForm,
    ReasonForm,
    ReceiptForm,
    StocktakeStartForm,
    TransferForm,
    WithdrawalForm,
    pk_of,
)
from apps.inventory.models import (
    AuditLog,
    Batch,
    Notification,
    StockBalance,
    StockMovement,
    StockOperation,
    Stocktake,
    StocktakeStatus,
)
from apps.inventory.services.common import require
from apps.inventory.services.posting import (
    Receipt,
    Withdrawal,
    change_quality,
    issue_stock,
    receive_goods,
    return_to_supplier,
    reverse_movement,
    transfer_stock,
    write_off,
)
from apps.inventory.services.stocktake import (
    count_batch,
    post_stocktake,
    start_stocktake,
    transition,
)
from apps.masterdata.models import Warehouse


def guarded(
    permission: str, *methods: str
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @login_required
        @never_cache
        @require_http_methods(methods)
        @wraps(view)
        def call(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            require(cast(User, request.user), permission)
            return view(request, *args, **kwargs)

        return call

    return decorate


def workspace(request: HttpRequest, template: str, **context: Any) -> HttpResponse:
    return render(request, template, {"show_workspace": True, **context})


def show_form(
    request: HttpRequest, title: str, form: forms.Form, *, scan: bool = False, confirm: bool = False
) -> HttpResponse:
    return workspace(
        request, "workspace/form.html", title=title, form=form, scan=scan, confirm=confirm
    )


@guarded("inventory.view_stockbalance", "GET")
def balances(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:120]
    warehouse = request.GET.get("warehouse", "")[:20]
    rows = (
        StockBalance.objects.filter(quantity__gt=0)
        .select_related(
            "batch__item",
            "batch__item__base_unit",
            "location__warehouse",
        )
        .order_by("batch__item__code", "location__warehouse__code", "batch_id", "pk")
    )
    if query:
        rows = rows.filter(
            Q(batch__item__code__icontains=query)
            | Q(batch__item__name_uz__icontains=query)
            | Q(batch__number__icontains=query)
            | Q(batch__item__barcode__iexact=query)
        )
    if warehouse:
        rows = rows.filter(location__warehouse__code=warehouse)
    page = Paginator(rows, 30).get_page(request.GET.get("page"))
    return workspace(
        request,
        "inventory/balances.html",
        page=page,
        q=query,
        warehouse=warehouse,
        warehouses=Warehouse.objects.all(),
    )


@guarded("inventory.view_stockmovement", "GET")
def movements(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:120]
    rows = StockMovement.objects.select_related("batch__item", "location", "warehouse", "operation")
    if query:
        rows = rows.filter(
            Q(batch__item__code__icontains=query)
            | Q(batch__number__icontains=query)
            | Q(reference__icontains=query)
        )
    return workspace(
        request,
        "inventory/movements.html",
        q=query,
        page=Paginator(rows, 30).get_page(request.GET.get("page")),
    )


POSTINGS = {
    "receive": ("receive_stock", ReceiptForm, "Receive stock"),
    "issue": ("issue_stock", WithdrawalForm, "Issue stock"),
    "transfer": ("transfer_stock", TransferForm, "Transfer stock"),
    "write-off": ("write_off_stock", WithdrawalForm, "Write off stock"),
    "return": ("return_stock", WithdrawalForm, "Return to supplier"),
}


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def post_movement(request: HttpRequest, action: str) -> HttpResponse:
    if action not in POSTINGS:
        raise Http404
    permission, form_class, title = POSTINGS[action]
    actor = cast(User, request.user)
    require(actor, f"inventory.{permission}")
    form = form_class(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if action == "receive":
                receipt = Receipt(
                    data["item"].pk,
                    data["location"].pk,
                    data["supplier"].pk,
                    data["batch_number"],
                    data["quantity"],
                    data["unit_cost"],
                    data["reference"],
                    data["expiry"],
                    pk_of(data, "unit"),
                )
                receive_goods(actor, data["key"], receipt)
            else:
                withdrawal = Withdrawal(
                    data["item"].pk,
                    data["location"].pk,
                    data["quantity"],
                    pk_of(data, "unit"),
                    pk_of(data, "batch"),
                    data["reason"],
                )
                if action == "transfer":
                    transfer_stock(actor, data["key"], withdrawal, data["destination"].pk)
                else:
                    service = {
                        "issue": issue_stock,
                        "write-off": write_off,
                        "return": return_to_supplier,
                    }[action]
                    service(actor, data["key"], withdrawal)
            return redirect("stock_movements")
        except ValidationError as error:
            form.add_error(None, error)
    return show_form(
        request,
        _(title),
        form,
        scan=action in {"receive", "issue"},
        confirm=action in {"write-off", "return"},
    )


@guarded("inventory.reverse_stock", "GET", "POST")
def reverse(request: HttpRequest, pk: int) -> HttpResponse:
    operation = get_object_or_404(StockOperation, pk=pk)
    form = ReasonForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            reverse_movement(
                cast(User, request.user),
                form.cleaned_data["key"],
                operation.pk,
                form.cleaned_data["reason"],
            )
            return redirect("stock_movements")
        except ValidationError as error:
            form.add_error(None, error)
    return show_form(request, _("Reverse posting"), form, confirm=True)


@guarded("inventory.change_batch", "GET", "POST")
def quality(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(Batch, pk=pk)
    form = QualityForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            change_quality(cast(User, request.user), pk, form.cleaned_data["quality"])
            return redirect("stock_balances")
        except ValidationError as error:
            form.add_error(None, error)
    return show_form(request, _("Change batch quality"), form, confirm=True)


@guarded("inventory.view_stocktake", "GET")
def stocktakes(request: HttpRequest) -> HttpResponse:
    rows = Stocktake.objects.select_related("location").order_by("-pk")
    return workspace(
        request,
        "inventory/stocktakes.html",
        page=Paginator(rows, 30).get_page(request.GET.get("page")),
    )


@guarded("inventory.count_stocktake", "GET", "POST")
def stocktake_new(request: HttpRequest) -> HttpResponse:
    form = StocktakeStartForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            sheet = start_stocktake(
                cast(User, request.user), form.cleaned_data["key"], form.cleaned_data["location"].pk
            )
            return redirect("stocktake_detail", pk=sheet.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return show_form(request, _("Start stocktake"), form)


@guarded("inventory.view_stocktake", "GET", "POST")
def stocktake_detail(request: HttpRequest, pk: int) -> HttpResponse:
    sheet = get_object_or_404(Stocktake.objects.select_related("location"), pk=pk)
    actor = cast(User, request.user)
    form = CountForm(request.POST if request.method == "POST" else None)
    errors = []
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "count":
                require(actor, "inventory.count_stocktake")
                if form.is_valid():
                    count_batch(
                        actor, pk, form.cleaned_data["batch"].pk, form.cleaned_data["counted"]
                    )
                    return redirect("stocktake_detail", pk=pk)
            elif action == "post":
                require(actor, "inventory.approve_stocktake")
                post_stocktake(actor, sheet.key, pk)
                return redirect("stocktake_detail", pk=pk)
            elif action in {
                StocktakeStatus.SUBMITTED,
                StocktakeStatus.APPROVED,
                StocktakeStatus.CANCELLED,
            }:
                transition(actor, pk, action)
                return redirect("stocktake_detail", pk=pk)
            else:
                raise Http404
        except ValidationError as error:
            errors = error.messages
    lines = sheet.lines.select_related("batch__item").order_by("batch__item__code", "batch__number")
    return workspace(
        request,
        "inventory/stocktake.html",
        sheet=sheet,
        form=form,
        errors=errors,
        page=Paginator(lines, 30).get_page(request.GET.get("page")),
        is_counter=sheet.counter_id == actor.pk,
    )


@guarded("inventory.view_notification", "GET")
def alerts(request: HttpRequest) -> HttpResponse:
    rows = Notification.objects.filter(resolved_at__isnull=True).select_related("item", "batch")
    return workspace(
        request, "inventory/alerts.html", page=Paginator(rows, 30).get_page(request.GET.get("page"))
    )


@guarded("inventory.view_auditlog", "GET")
def history(request: HttpRequest) -> HttpResponse:
    rows = AuditLog.objects.select_related("actor")
    return workspace(
        request, "inventory/audit.html", page=Paginator(rows, 30).get_page(request.GET.get("page"))
    )
