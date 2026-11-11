from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import AlertKind, Notification, WarehouseCost
from apps.inventory.services.common import audit, require
from apps.masterdata.models import Item, Supplier
from apps.procurement.models import (
    Currency,
    PurchaseRequest,
    PurchaseRequestLine,
    State,
    SupplierItemPrice,
)
from apps.procurement.services.common import amount, check_transition, identity, money, note, replay


@dataclass(frozen=True)
class RequestLine:
    item_id: int
    quantity: Decimal
    estimated_cost: Decimal
    supplier_id: int | None = None


@dataclass(frozen=True)
class RequestData:
    department: str
    needed_by: date
    priority: str
    justification: str
    lines: tuple[RequestLine, ...]


@transaction.atomic
def create_request(
    actor: User, key: UUID, data: RequestData, *, alert_id: int | None = None
) -> PurchaseRequest:
    """Create a complete, auditable draft from explicit UZS estimates."""
    require(actor, "procurement.add_purchaserequest")
    payload = {**asdict(data), "alert": alert_id}
    previous = replay(PurchaseRequest, actor, key, payload)
    if previous:
        return previous
    if data.needed_by < timezone.localdate() or data.priority not in {"normal", "urgent"}:
        raise ValidationError(_("Choose a valid priority and needed-by date."))
    items = {line.item_id for line in data.lines}
    suppliers = {line.supplier_id for line in data.lines if line.supplier_id is not None}
    if (
        not items
        or len(items) != len(data.lines)
        or len(items) > 100
        or Item.objects.filter(pk__in=items, active=True).count() != len(items)
        or Supplier.objects.filter(pk__in=suppliers, active=True).count() != len(suppliers)
    ):
        raise ValidationError(_("Use distinct active items and active suppliers."))
    request, created = PurchaseRequest.objects.get_or_create(
        key=key,
        defaults={
            **identity(actor, key, payload),
            "department": note(data.department, 100),
            "needed_by": data.needed_by,
            "priority": data.priority,
            "justification": note(data.justification, 500),
            "alert_id": alert_id,
        },
    )
    if not created:
        return replay(PurchaseRequest, actor, key, payload) or request
    PurchaseRequestLine.objects.bulk_create(
        PurchaseRequestLine(
            request=request,
            item_id=line.item_id,
            quantity=amount(line.quantity, quantity=True),
            estimated_unit_cost=amount(line.estimated_cost),
            preferred_supplier_id=line.supplier_id,
        )
        for line in data.lines
    )
    audit(actor, "request_create", request, {}, payload)
    return request


@transaction.atomic
def request_from_alert(actor: User, alert_id: int, key: UUID) -> PurchaseRequest:
    """Create one draft per active low-stock condition using a defensible known estimate."""
    require(actor, "procurement.add_purchaserequest")
    alert = (
        Notification.objects.select_for_update(of=("self",)).select_related("item").get(pk=alert_id)
    )
    previous = PurchaseRequest.objects.filter(alert=alert).first()
    if previous:
        return previous
    if alert.kind != AlertKind.LOW or alert.resolved_at is not None:
        raise ValidationError(_("Choose an open low-stock alert."))
    item = alert.item
    today = timezone.localdate()
    price = (
        SupplierItemPrice.objects.filter(
            item=item,
            currency=Currency.UZS,
            supplier__active=True,
            valid_from__lte=today,
            valid_until__gte=today,
        )
        .order_by("-valid_from", "-pk")
        .first()
    )
    stock = WarehouseCost.objects.filter(item=item).aggregate(
        quantity=Sum("quantity"), value=Sum("carrying_value")
    )
    estimate = (
        price.price
        if price
        else money(stock["value"] / stock["quantity"])
        if stock["quantity"]
        else Decimal(0)
    )
    quantity = max(item.reorder_qty, item.min_stock - (stock["quantity"] or 0))
    if quantity <= 0 or estimate <= 0:
        raise ValidationError(
            _("A positive reorder amount and known cost are required. Create a request manually.")
        )
    return create_request(
        actor,
        key,
        RequestData(
            _("Procurement"),
            today,
            "normal",
            _("Replenishment from a low-stock alert."),
            (RequestLine(item.pk, quantity, estimate, price.supplier_id if price else None),),
        ),
        alert_id=alert_id,
    )


@transaction.atomic
def submit_request(actor: User, request_id: int) -> PurchaseRequest:
    """Snapshot the rule assignments and freeze a draft for approval."""
    from apps.procurement.services.approvals import make_case

    require(actor, "procurement.add_purchaserequest")
    request = PurchaseRequest.objects.select_for_update().get(pk=request_id)
    if request.created_by_id != actor.pk:
        raise ValidationError(_("Only the requester can submit this draft."))
    if request.state == State.SUBMITTED:
        return request
    check_transition("request", request.state, State.SUBMITTED)
    lines = list(request.lines.select_related("item"))
    total = sum((money(line.quantity * line.estimated_unit_cost) for line in lines), Decimal(0))
    make_case(actor, total, {line.item.category_id for line in lines}, request=request)
    request.state = State.SUBMITTED
    request.save(update_fields=["state"])
    audit(
        actor,
        "request_submit",
        request,
        {"state": State.DRAFT},
        {"state": request.state, "total": total},
    )
    return request
