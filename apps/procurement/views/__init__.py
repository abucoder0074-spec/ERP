from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.forms import formset_factory
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.inventory.services.common import require
from apps.procurement import forms as ui
from apps.procurement.models import (
    RFQ,
    ApprovalDelegation,
    ApprovalStep,
    Attachment,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderVersion,
    PurchaseRequest,
    State,
    SupplierInvoice,
)
from apps.procurement.services.approvals import decide, eligible_principal
from apps.procurement.services.attachments import attach, owner_permission, read_attachment
from apps.procurement.services.deliveries import overdue_orders, supplier_metrics
from apps.procurement.services.invoices import (
    InvoiceData,
    InvoiceLine,
    record_invoice,
    resolve_invoice,
)
from apps.procurement.services.orders import (
    OrderData,
    OrderLine,
    amend_order,
    cancel_order,
    create_order,
    version_diff,
)
from apps.procurement.services.policies import POLICIES, save_policy
from apps.procurement.services.quotes import (
    QuoteData,
    cancel_rfq,
    capture_quote,
    open_rfq,
    select_quote,
)
from apps.procurement.services.receiving import Delivery, DeliveryLine, post_receipt, post_return
from apps.procurement.services.requests import (
    RequestData,
    RequestLine,
    create_request,
    request_from_alert,
    submit_request,
)

COLLECTIONS = {
    "requests": (PurchaseRequest, _("Purchase requests")),
    "rfqs": (RFQ, _("Requests for quotation")),
    "orders": (PurchaseOrder, _("Purchase orders")),
    "receipts": (GoodsReceipt, _("Goods receipts")),
    "invoices": (SupplierInvoice, _("Supplier invoices")),
}
OWNER = {"requests": "request", "orders": "order", "receipts": "receipt", "invoices": "invoice"}
PERMISSIONS = {
    "request": "add_purchaserequest",
    "submit": "add_purchaserequest",
    "replenish": "add_purchaserequest",
    "rfq": "add_rfq",
    "quote": "add_quote",
    "select": "add_rfq",
    "cancel-rfq": "cancel_order",
    "order": "add_purchaseorder",
    "convert": "add_purchaseorder",
    "amend": "change_purchaseorder",
    "cancel-order": "cancel_order",
    "receive": "add_goodsreceipt",
    "return": "add_supplierreturn",
    "invoice": "add_supplierinvoice",
    "override": "override_match",
    "cancel-invoice": "override_match",
}
TITLES = {
    "request": _("Create purchase request"),
    "submit": _("Submit purchase request"),
    "replenish": _("Create replenishment request"),
    "rfq": _("Record RFQ dispatch"),
    "quote": _("Record quote"),
    "select": _("Select quote"),
    "cancel-rfq": _("Cancel RFQ"),
    "order": _("Create purchase order"),
    "convert": _("Convert request to purchase order"),
    "amend": _("Amend purchase order"),
    "cancel-order": _("Cancel purchase order"),
    "receive": _("Receive against order"),
    "return": _("Return defective goods"),
    "invoice": _("Record supplier invoice"),
    "override": _("Approve match exception"),
    "cancel-invoice": _("Cancel invoice"),
    "decision": _("Approval decision"),
}
REASONS = {
    "quantity": _("Invoice quantity differs from net receipt quantity."),
    "price": _("Invoice price differs from the purchase order."),
    "currency": _("Invoice currency differs from the order version."),
    "unplanned": _("The receipt line has no purchase order line."),
}


def protected(
    *methods: str,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @login_required
        @never_cache
        @require_http_methods(methods)
        @wraps(view)
        def call(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            return view(request, *args, **kwargs)

        return call

    return decorate


def page(request: HttpRequest, template: str, **context: Any) -> HttpResponse:
    return render(request, f"procurement/{template}.html", {"show_workspace": True, **context})


def actor_of(request: HttpRequest) -> User:
    return cast(User, request.user)


def _model(kind: str) -> tuple[Any, Any]:
    if kind not in COLLECTIONS:
        raise Http404
    return COLLECTIONS[kind]


@protected("GET")
def listing(request: HttpRequest, kind: str) -> HttpResponse:
    model, title = _model(kind)
    require(actor_of(request), f"procurement.view_{model._meta.model_name}")
    rows = model._default_manager.all()
    if kind in {"orders", "invoices"}:
        rows = rows.select_related("supplier")
    if kind == "rfqs":
        rows = rows.select_related("request")
    if kind == "receipts":
        rows = rows.select_related("order")
    return page(
        request,
        "list",
        kind=kind,
        title=title,
        page=Paginator(rows, 30).get_page(request.GET.get("page")),
    )


@protected("GET")
def detail(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    model, title = _model(kind)
    require(actor_of(request), f"procurement.view_{model._meta.model_name}")
    record = get_object_or_404(model, pk=pk)
    context: dict[str, Any] = {}
    if kind == "requests":
        context["lines"] = record.lines.select_related("item", "preferred_supplier")
        context["rfq"] = RFQ.objects.filter(request=record).first()
        context["order"] = PurchaseOrder.objects.filter(request=record).first()
    elif kind == "rfqs":
        metrics = supplier_metrics()
        comparison = []
        for quote in record.quotes.select_related("supplier").order_by("total_uzs", "pk"):
            count, on_time, _defects = metrics.get(quote.supplier_id, (0, 0, 0))
            comparison.append(
                (quote, (Decimal(on_time) * 5 / count).quantize(Decimal(".1")) if count else None)
            )
        context["comparison"] = comparison
        context["invitations"] = record.invitations.select_related("supplier")
    elif kind == "orders":
        context["versions"] = record.versions.order_by("-number")
        context["lines"] = (
            record.active_version.lines.select_related("item") if record.active_version else ()
        )
        context["receipts"] = record.receipts.order_by("-pk")
        context["invoices"] = record.invoices.order_by("-pk")
    elif kind == "receipts":
        context["lines"] = record.lines.select_related(
            "item", "batch", "location"
        ).prefetch_related("returns")
    else:
        context["lines"] = record.lines.select_related(
            "receipt_line__item", "receipt_line__receipt"
        )
        context["mismatches"] = [
            (row["receipt_line"], [REASONS[code] for code in row["reasons"]])
            for row in record.mismatches
        ]
    if kind in OWNER:
        context["attachments"] = Attachment.objects.defer("content").filter(
            **{f"{OWNER[kind]}_id": pk}
        )
    return page(request, "detail", record=record, title=title, kind=kind, **context)


def _form(request: HttpRequest, cls: type[forms.Form], **kwargs: Any) -> forms.Form:
    return cls(request.POST if request.method == "POST" else None, **kwargs)


def _rows(request: HttpRequest, cls: type[forms.Form], **kwargs: Any) -> Any:
    factory = formset_factory(cls, extra=1, max_num=100, validate_max=True, absolute_max=101)
    return factory(request.POST if request.method == "POST" else None, prefix="lines", **kwargs)


def _show_action(
    request: HttpRequest,
    action: str,
    form: forms.Form,
    handler: Callable[..., HttpResponse],
    rows: Any = None,
) -> HttpResponse:
    if request.method == "POST" and form.is_valid() and (rows is None or rows.is_valid()):
        try:
            return handler(
                form.cleaned_data,
                [row for row in rows.cleaned_data if row] if rows is not None else [],
            )
        except ObjectDoesNotExist as error:
            raise Http404 from error
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(None, _("This change conflicts with an existing document."))
    return page(
        request, "form", title=TITLES[action], form=form, rows=rows, scanning=action == "receive"
    )


def _go(kind: str, pk: int) -> HttpResponse:
    return redirect("procurement_detail", kind=kind, pk=pk)


def _request_action(request: HttpRequest, action: str, pk: int) -> HttpResponse:
    actor = actor_of(request)
    if action == "request":

        def submit(data: dict[str, Any], rows: list[dict[str, Any]]) -> HttpResponse:
            lines = tuple(
                RequestLine(
                    row["item"].pk,
                    row["quantity"],
                    row["estimated_cost"],
                    row["supplier"].pk if row["supplier"] else None,
                )
                for row in rows
            )
            result = create_request(
                actor,
                data["key"],
                RequestData(
                    data["department"],
                    data["needed_by"],
                    data["priority"],
                    data["justification"],
                    lines,
                ),
            )
            return _go("requests", result.pk)

        return _show_action(
            request, action, _form(request, ui.RequestForm), submit, _rows(request, ui.RequestRow)
        )
    if action == "replenish":
        data = request.POST.copy() if request.method == "POST" else None
        if data is not None and "key" not in data:
            data["key"] = str(uuid5(NAMESPACE_URL, f"muzqaymoq/replenishment/{pk}"))
        return _show_action(
            request,
            action,
            ui.KeyForm(data),
            lambda data, rows: _go(
                "requests",
                request_from_alert(actor, pk, data["key"]).pk,
            ),
        )
    get_object_or_404(PurchaseRequest, pk=pk)
    if action == "submit":
        return _show_action(
            request,
            action,
            _form(request, forms.Form),
            lambda data, rows: _go(
                "requests",
                submit_request(actor, pk).pk,
            ),
        )
    return _show_action(
        request,
        action,
        _form(request, ui.RFQForm),
        lambda data, rows: _go(
            "rfqs",
            open_rfq(
                actor, data["key"], pk, tuple(supplier.pk for supplier in data["suppliers"])
            ).pk,
        ),
    )


def _rfq_action(request: HttpRequest, action: str, pk: int) -> HttpResponse:
    actor = actor_of(request)
    rfq = get_object_or_404(RFQ, pk=pk)
    if action == "quote":
        form = _form(request, ui.QuoteForm)
        prices = list(rfq.request.lines.select_related("item"))
        for line in prices:
            form.fields[f"price_{line.pk}"] = forms.DecimalField(
                label=f"{_('Unit price')} · {line.item}",
                min_value=Decimal(".01"),
                decimal_places=2,
                max_digits=15,
            )

        def save(data: dict[str, Any], rows: list[dict[str, Any]]) -> HttpResponse:
            capture_quote(
                actor,
                data["key"],
                pk,
                QuoteData(
                    data["supplier"].pk,
                    data["currency"],
                    data["rate"],
                    data["rate_date"],
                    data["lead_days"],
                    data["valid_from"],
                    data["valid_until"],
                    data["payment_terms"],
                    {line.pk: data[f"price_{line.pk}"] for line in prices},
                ),
            )
            return _go("rfqs", pk)

        return _show_action(request, action, form, save)
    if action == "select":
        return _show_action(
            request,
            action,
            _form(request, ui.QuoteSelectForm),
            lambda data, rows: _go(
                "rfqs",
                select_quote(actor, pk, data["quote"], data["reason"]).pk,
            ),
        )
    return _show_action(
        request,
        action,
        _form(request, ui.NoteForm),
        lambda data, rows: _go(
            "rfqs",
            cancel_rfq(actor, pk, data["reason"]).pk,
        ),
    )


def _order_action(request: HttpRequest, action: str, pk: int) -> HttpResponse:
    actor = actor_of(request)
    initial: dict[str, Any] = {}
    lines: list[dict[str, Any]] = []
    quote_id = None
    if action == "amend":
        order = get_object_or_404(PurchaseOrder, pk=pk)
        version = order.active_version or order.versions.order_by("-number").first()
        if version:
            initial = {
                "supplier": order.supplier_id,
                "currency": version.currency,
                "rate": version.exchange_rate,
                "rate_date": version.rate_date,
                "expected_on": version.expected_on,
            }
            lines = [dict(row) for row in version.lines.values("item", "quantity", "price")]
    if action == "convert":
        source = get_object_or_404(PurchaseRequest, pk=pk)
        rfq = (
            RFQ.objects.select_related("selected")
            .filter(request=source, state=State.SELECTED)
            .first()
        )
        quote = rfq.selected if rfq else None
        if quote:
            quote_id = quote.pk
            initial = {
                "supplier": quote.supplier_id,
                "currency": quote.currency,
                "rate": quote.exchange_rate,
                "rate_date": quote.rate_date,
                "expected_on": source.needed_by,
            }
            lines = [
                {
                    "item": row.request_line.item_id,
                    "quantity": row.request_line.quantity,
                    "price": row.price,
                }
                for row in quote.lines.select_related("request_line")
            ]
        else:
            lines = [
                {"item": row.item_id, "quantity": row.quantity, "price": row.estimated_unit_cost}
                for row in source.lines.all()
            ]
            initial = {"expected_on": source.needed_by}

    def save(data: dict[str, Any], rows: list[dict[str, Any]]) -> HttpResponse:
        payload = OrderData(
            data["supplier"].pk,
            data["currency"],
            data["rate"],
            data["rate_date"],
            data["expected_on"],
            tuple(OrderLine(row["item"].pk, row["quantity"], row["price"]) for row in rows),
        )
        if action == "amend":
            amend_order(actor, data["key"], pk, payload, data["reason"])
            return _go("orders", pk)
        result = create_order(
            actor,
            data["key"],
            payload,
            request_id=pk if action == "convert" else None,
            quote_id=quote_id,
        )
        return _go("orders", result.pk)

    return _show_action(
        request,
        action,
        _form(request, ui.OrderForm, initial=initial),
        save,
        _rows(request, ui.OrderRow, initial=lines),
    )


def _receipt_action(request: HttpRequest, pk: int) -> HttpResponse:
    actor = actor_of(request)
    get_object_or_404(PurchaseOrder, pk=pk)
    form = ui.ReceiptForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
    )

    @transaction.atomic
    def save(data: dict[str, Any], rows: list[dict[str, Any]]) -> HttpResponse:
        receipt = post_receipt(
            actor,
            data["key"],
            pk,
            Delivery(
                data["delivered_on"],
                data["delay_reason"],
                tuple(
                    DeliveryLine(
                        row["item"].pk,
                        row["location"].pk,
                        row["quantity"],
                        row["batch"],
                        row["expiry"],
                        row["unplanned_price"],
                        row["unplanned_reason"],
                    )
                    for row in rows
                ),
            ),
        )
        photo = data["photo"]
        if photo:
            attach(
                actor, uuid5(data["key"], "photo"), "receipt", receipt.pk, photo.name, photo.read()
            )
        return _go("receipts", receipt.pk)

    return _show_action(request, "receive", form, save, _rows(request, ui.ReceiptRow))


def _invoice_action(request: HttpRequest, pk: int) -> HttpResponse:
    actor = actor_of(request)
    order = get_object_or_404(PurchaseOrder.objects.select_related("active_version"), pk=pk)
    version = order.active_version
    initial = (
        {
            "currency": version.currency,
            "rate": version.exchange_rate,
            "rate_date": version.rate_date,
        }
        if version
        else {}
    )

    def save(data: dict[str, Any], rows: list[dict[str, Any]]) -> HttpResponse:
        result = record_invoice(
            actor,
            data["key"],
            pk,
            InvoiceData(
                data["number"],
                data["invoice_date"],
                data["currency"],
                data["rate"],
                data["rate_date"],
                tuple(
                    InvoiceLine(row["receipt_line"].pk, row["quantity"], row["price"])
                    for row in rows
                ),
            ),
        )
        return _go("invoices", result.pk)

    return _show_action(
        request,
        "invoice",
        _form(request, ui.InvoiceForm, initial=initial),
        save,
        _rows(request, ui.InvoiceRow, form_kwargs={"order_id": pk}),
    )


@protected("GET", "POST")
def action(request: HttpRequest, action: str, pk: int = 0) -> HttpResponse:
    actor = actor_of(request)
    if action not in PERMISSIONS:
        raise Http404
    require(actor, f"procurement.{PERMISSIONS[action]}")
    if action in {"request", "submit", "replenish", "rfq"}:
        return _request_action(request, action, pk)
    if action in {"quote", "select", "cancel-rfq"}:
        return _rfq_action(request, action, pk)
    if action in {"order", "convert", "amend"}:
        return _order_action(request, action, pk)
    if action == "receive":
        return _receipt_action(request, pk)
    if action == "invoice":
        return _invoice_action(request, pk)
    if action == "return":
        line = get_object_or_404(GoodsReceiptLine, pk=pk)
        return _show_action(
            request,
            action,
            _form(request, ui.ReturnForm),
            lambda data, rows: _go(
                "receipts",
                post_return(
                    actor, data["key"], line.pk, data["quantity"], data["reason"], data["photo"]
                ).line.receipt_id,
            ),
        )
    if action == "cancel-order":
        return _show_action(
            request,
            action,
            _form(request, ui.NoteForm),
            lambda data, rows: _go(
                "orders",
                cancel_order(actor, pk, data["reason"]).pk,
            ),
        )
    return _show_action(
        request,
        action,
        _form(request, ui.NoteForm),
        lambda data, rows: _go(
            "invoices",
            resolve_invoice(actor, pk, data["reason"], cancel=action == "cancel-invoice").pk,
        ),
    )


@protected("GET")
def inbox(request: HttpRequest) -> HttpResponse:
    actor = actor_of(request)
    require(actor, "procurement.view_approvalstep")
    today = timezone.localdate()
    groups = (
        set(actor.groups.values_list("pk", flat=True))
        if actor.has_perm("procurement.approve_procurement")
        else set()
    )
    delegated = (
        ApprovalDelegation.objects.filter(
            delegate=actor,
            active=True,
            principal__is_active=True,
            starts_on__lte=today,
            ends_on__gte=today,
        )
        .select_related("principal")
        .prefetch_related("principal__groups")
    )
    for delegation in delegated if actor.has_perm("procurement.act_as_delegate") else ():
        if delegation.principal.has_perm("procurement.approve_procurement"):
            groups.update(group.pk for group in delegation.principal.groups.all())
    earlier = ApprovalStep.objects.filter(
        case_id=OuterRef("case_id"), sequence__lt=OuterRef("sequence")
    ).exclude(state=State.APPROVED)
    steps = (
        ApprovalStep.objects.filter(state=State.SUBMITTED)
        .exclude(case__requester=actor)
        .filter(~Exists(earlier))
        .select_related(
            "case__request",
            "case__version__order",
            "group",
        )
    )
    from django.db.models import Q

    steps = steps.filter(
        Q(case__request__state=State.SUBMITTED) | Q(case__version__state=State.SUBMITTED)
    )
    if not actor.is_superuser:
        steps = steps.filter(group_id__in=groups)
    return page(
        request, "inbox", page=Paginator(steps.order_by("pk"), 30).get_page(request.GET.get("page"))
    )


@protected("GET", "POST")
def decision(request: HttpRequest, pk: int) -> HttpResponse:
    actor = actor_of(request)
    require(actor, "procurement.view_approvalstep")
    step = get_object_or_404(ApprovalStep.objects.select_related("case"), pk=pk)
    if request.method == "GET" and eligible_principal(actor, step, step.case) is None:
        raise PermissionDenied
    return _show_action(
        request,
        "decision",
        _form(request, ui.DecisionForm),
        lambda data, rows: (
            decide(actor, step.pk, approve=data["approve"], comment=data["comment"]),
            redirect("procurement_inbox"),
        )[1],
    )


@protected("GET")
def versions(request: HttpRequest, pk: int) -> HttpResponse:
    require(actor_of(request), "procurement.view_purchaseorder")
    version = get_object_or_404(PurchaseOrderVersion.objects.select_related("order"), pk=pk)
    previous = version.order.versions.filter(number__lt=version.number).order_by("-number").first()
    return page(
        request,
        "version",
        version=version,
        lines=version.lines.select_related("item"),
        diff=version_diff(previous, version) if previous else None,
    )


@protected("GET")
def deliveries(request: HttpRequest) -> HttpResponse:
    require(actor_of(request), "procurement.view_goodsreceipt")
    metrics = supplier_metrics()
    from apps.masterdata.models import Supplier

    suppliers = Supplier.objects.in_bulk(metrics)
    stats = [
        (suppliers[pk], count, (Decimal(on_time) * 100 / count).quantize(Decimal(".01")), defects)
        for pk, (count, on_time, defects) in metrics.items()
    ]
    receipts = GoodsReceipt.objects.select_related("order__supplier", "version")
    return page(
        request,
        "deliveries",
        overdue=overdue_orders(),
        stats=stats,
        page=Paginator(receipts, 30).get_page(request.GET.get("page")),
    )


@protected("GET", "POST")
def upload(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    if kind not in OWNER:
        raise Http404
    owner = OWNER[kind]
    actor = actor_of(request)
    owner_permission(actor, owner, write=True)
    model, _title = _model(kind)
    get_object_or_404(model, pk=pk)
    form = ui.AttachmentForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
    )
    if form.is_valid():
        file = form.cleaned_data["file"]
        try:
            attach(actor, form.cleaned_data["key"], owner, pk, file.name, file.read())
            return _go(kind, pk)
        except ValidationError as error:
            form.add_error("file", error)
    return page(request, "form", title=_("Upload attachment"), form=form)


@protected("GET")
def download(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        file = read_attachment(actor_of(request), pk)
    except Attachment.DoesNotExist as error:
        raise Http404 from error
    suffix = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}[file.mime_type]
    return HttpResponse(
        bytes(file.content),
        content_type=file.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{file.storage_name}.{suffix}"'},
    )


@protected("GET", "POST")
def policy(request: HttpRequest, kind: str, pk: int | None = None) -> HttpResponse:
    actor = actor_of(request)
    if kind not in POLICIES:
        raise Http404
    model, form_type = POLICIES[kind]
    require(actor, f"procurement.change_{model._meta.model_name}")
    if pk is None:
        if request.method != "GET":
            return HttpResponse(status=405)
        rows = model._default_manager.select_related().order_by("pk")
        return page(
            request, "policy", kind=kind, page=Paginator(rows, 30).get_page(request.GET.get("page"))
        )
    if request.method == "POST":
        try:
            form, saved = save_policy(actor, kind, pk, request.POST)
        except ObjectDoesNotExist as error:
            raise Http404 from error
        if saved:
            return redirect("procurement_policy", kind=kind)
    else:
        cls: Any = form_type
        form = cls(instance=get_object_or_404(model, pk=pk) if pk else None)
    return page(request, "form", title=_("Approval configuration"), form=form)
