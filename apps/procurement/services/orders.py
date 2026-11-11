from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID, uuid5

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.services.common import audit, require
from apps.masterdata.models import Item, Supplier
from apps.procurement.models import (
    RFQ,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderVersion,
    PurchaseRequest,
    State,
)
from apps.procurement.services.approvals import make_case
from apps.procurement.services.common import (
    amount,
    check_transition,
    fx,
    identity,
    money,
    note,
    replay,
)


@dataclass(frozen=True)
class OrderLine:
    item_id: int
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class OrderData:
    supplier_id: int
    currency: str
    rate: Decimal
    rate_date: date
    expected_on: date
    lines: tuple[OrderLine, ...]


def totals(
    lines: tuple[OrderLine, ...],
    rate: Decimal,
    vat: Decimal,
    retained_vat: dict[int, Decimal] | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Round each line net and VAT in document currency, then convert the gross sum."""
    with localcontext() as ctx:
        ctx.prec = 60
        net = [money(line.quantity * line.price) for line in lines]
        subtotal = money(sum(net, Decimal(0)))
        rates = retained_vat or {}
        tax = money(
            sum(
                (
                    money(value * rates.get(line.item_id, vat) / 100)
                    for line, value in zip(lines, net, strict=True)
                ),
                Decimal(0),
            )
        )
        return subtotal, tax, money((subtotal + tax) * rate)


def net_receipts(order: PurchaseOrder) -> dict[int, Decimal]:
    result: dict[int, Decimal] = {}
    rows = (
        GoodsReceiptLine.objects.filter(receipt__order=order, order_line__isnull=False)
        .annotate(
            returned=Sum("returns__quantity"),
        )
        .values_list("item_id", "quantity", "returned")
    )
    for item_id, quantity, returned in rows:
        result[item_id] = result.get(item_id, Decimal(0)) + quantity - (returned or 0)
    return result


def receipt_cap(quantity: Decimal) -> Decimal:
    return quantity * (100 + settings.PROCUREMENT_OVER_DELIVERY_PERCENT) / 100


def _version(
    actor: User,
    order: PurchaseOrder,
    key: UUID,
    data: OrderData,
    number: int,
    reason: str,
    payload: dict[str, object],
) -> PurchaseOrderVersion:
    if order.supplier_id != data.supplier_id or data.expected_on < timezone.localdate():
        raise ValidationError(_("Check the supplier and expected delivery date."))
    rate = fx(data.currency, data.rate, data.rate_date)
    ids = {line.item_id for line in data.lines}
    items = Item.objects.filter(pk__in=ids, active=True).in_bulk()
    if not ids or len(ids) != len(data.lines) or len(items) != len(ids) or len(ids) > 100:
        raise ValidationError(_("Use distinct active order items."))
    lines = tuple(
        OrderLine(line.item_id, amount(line.quantity, quantity=True), amount(line.price))
        for line in data.lines
    )
    vat = money(settings.VAT_RATE_PERCENT)
    previous = order.active_version
    received = net_receipts(order) if previous else {}
    retained_vat = (
        {
            line.item_id: line.vat_rate
            for line in previous.lines.all()
            if received.get(line.item_id, 0) > 0
        }
        if previous
        else {}
    )
    subtotal, tax, total = totals(lines, rate, vat, retained_vat)
    version = PurchaseOrderVersion.objects.create(
        **identity(actor, key, payload),
        order=order,
        number=number,
        currency=data.currency,
        exchange_rate=rate,
        rate_date=data.rate_date,
        expected_on=data.expected_on,
        reason=reason,
        subtotal=subtotal,
        tax=tax,
        total_uzs=total,
    )
    PurchaseOrderLine.objects.bulk_create(
        PurchaseOrderLine(
            version=version,
            item_id=line.item_id,
            quantity=line.quantity,
            price=line.price,
            vat_rate=retained_vat.get(line.item_id, vat),
        )
        for line in lines
    )
    _check_amendment(order, version)
    make_case(actor, total, {item.category_id for item in items.values()}, version=version)
    audit(actor, "order_version_submit", version, {}, {**payload, "total_uzs": total})
    return version


@transaction.atomic
def create_order(
    actor: User,
    key: UUID,
    data: OrderData,
    *,
    request_id: int | None = None,
    quote_id: int | None = None,
) -> PurchaseOrder:
    """Create a manual or sourced order with a pending first version and explicit approvals."""
    require(actor, "procurement.add_purchaseorder")
    payload = {**asdict(data), "request": request_id, "quote": quote_id}
    existing = replay(PurchaseOrder, actor, key, payload)
    if existing:
        return existing
    request = (
        PurchaseRequest.objects.select_for_update().get(pk=request_id)
        if request_id is not None
        else None
    )
    if request and request.state != State.APPROVED:
        raise ValidationError(_("Choose an approved purchase request."))
    if request:
        requested = dict(request.lines.values_list("item_id", "quantity"))
        if {line.item_id: line.quantity for line in data.lines} != requested:
            raise ValidationError(_("Order quantities and items must match the approved request."))
    if quote_id is not None:
        rfq = (
            RFQ.objects.select_for_update(of=("self",))
            .select_related("selected")
            .filter(request=request)
            .first()
        )
        quote = rfq.selected if rfq and rfq.state == State.SELECTED else None
        today = timezone.localdate()
        if (
            quote is None
            or quote.pk != quote_id
            or not quote.valid_from <= today <= quote.valid_until
        ):
            raise ValidationError(_("Choose the current selected quote."))
        if (quote.supplier_id, quote.currency, quote.exchange_rate, quote.rate_date) != (
            data.supplier_id,
            data.currency,
            data.rate,
            data.rate_date,
        ):
            raise ValidationError(_("Order terms must match the selected quote."))
        quoted = dict(quote.lines.values_list("request_line__item_id", "price"))
        if {line.item_id: line.price for line in data.lines} != quoted:
            raise ValidationError(_("Order prices must match the selected quote."))
    elif request and RFQ.objects.filter(request=request).exclude(state=State.CANCELLED).exists():
        raise ValidationError(_("Select a quote before converting this request."))
    if not Supplier.objects.filter(pk=data.supplier_id, active=True).exists():
        raise ValidationError(_("Use an active supplier."))
    order, created = PurchaseOrder.objects.get_or_create(
        key=key,
        defaults={
            **identity(actor, key, payload),
            "request": request,
            "quote_id": quote_id,
            "supplier_id": data.supplier_id,
        },
    )
    if not created:
        return replay(PurchaseOrder, actor, key, payload) or order
    _version(actor, order, uuid5(key, "initial"), data, 1, "", payload)
    if request:
        check_transition("request", request.state, State.CONVERTED)
        request.state = State.CONVERTED
        request.save(update_fields=["state"])
        audit(
            actor,
            "request_convert",
            request,
            {"state": State.APPROVED},
            {"state": State.CONVERTED, "order": order.pk},
        )
    return order


def _check_amendment(order: PurchaseOrder, version: PurchaseOrderVersion) -> None:
    active = order.active_version
    if active is None:
        return
    received = net_receipts(order)
    old, new = ({line.item_id: line for line in v.lines.all()} for v in (active, version))
    for item_id, quantity in received.items():
        if quantity <= 0:
            continue
        previous, updated = old[item_id], new.get(item_id)
        if (
            updated is None
            or quantity > receipt_cap(updated.quantity)
            or (previous.price, previous.vat_rate) != (updated.price, updated.vat_rate)
            or (active.currency, active.exchange_rate, active.rate_date)
            != (version.currency, version.exchange_rate, version.rate_date)
        ):
            raise ValidationError(
                _("An amendment must preserve already received stock and its commercial terms.")
            )


@transaction.atomic
def amend_order(
    actor: User, key: UUID, order_id: int, data: OrderData, reason: str
) -> PurchaseOrderVersion:
    require(actor, "procurement.change_purchaseorder")
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    reason = note(reason, 300)
    payload = {**asdict(data), "order": order_id, "reason": reason}
    existing = replay(PurchaseOrderVersion, actor, key, payload)
    if existing:
        return existing
    if (
        order.state not in {State.ACTIVE, State.REJECTED}
        or order.versions.filter(state=State.SUBMITTED).exists()
    ):
        raise ValidationError(
            _("An active or rejected order without a pending amendment is required.")
        )
    number = (order.versions.aggregate(last=Max("number"))["last"] or 0) + 1
    version = _version(actor, order, key, data, number, reason, payload)
    if order.state == State.REJECTED:
        check_transition("order", order.state, State.SUBMITTED)
        order.state = State.SUBMITTED
        order.save(update_fields=["state"])
    return version


def finish_version(actor: User, order: PurchaseOrder, version_id: int, approved: bool) -> None:
    """Called with the parent order locked by the approval service."""
    version = order.versions.get(pk=version_id)
    status = State.ACTIVE if approved else State.REJECTED
    check_transition("version", version.state, status)
    if approved:
        _check_amendment(order, version)
        previous = order.active_version
        if previous is not None:
            check_transition("version", previous.state, State.SUPERSEDED)
            previous.state = State.SUPERSEDED
            previous.save(update_fields=["state"])
            audit(
                actor,
                "order_supersede",
                previous,
                {"state": State.ACTIVE},
                {"state": State.SUPERSEDED},
            )
        order.active_version = version
    if order.state != State.ACTIVE:
        check_transition("order", order.state, status)
        order.state = status
    version.state = status
    version.save(update_fields=["state"])
    order.save(update_fields=["state", "active_version"])
    audit(actor, "order_version_decision", version, {"state": State.SUBMITTED}, {"state": status})


@transaction.atomic
def cancel_order(actor: User, order_id: int, reason: str) -> PurchaseOrder:
    require(actor, "procurement.cancel_order")
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    reason = note(reason, 300)
    if order.state == State.CANCELLED and order.cancellation_reason == reason:
        return order
    check_transition("order", order.state, State.CANCELLED)
    before = order.state
    order.versions.filter(state=State.SUBMITTED).update(state=State.CANCELLED)
    order.state, order.cancellation_reason = State.CANCELLED, reason
    order.save(update_fields=["state", "cancellation_reason"])
    audit(
        actor,
        "order_cancel",
        order,
        {"state": before},
        {"state": State.CANCELLED, "reason": reason},
    )
    return order


def version_diff(
    previous: PurchaseOrderVersion, current: PurchaseOrderVersion
) -> dict[str, object]:
    """Return header and line differences between two immutable order versions."""
    if previous.order_id != current.order_id:
        raise ValidationError(_("Compare versions of the same order."))
    fields = (
        "currency",
        "exchange_rate",
        "rate_date",
        "expected_on",
        "subtotal",
        "tax",
        "total_uzs",
    )
    changes = {
        field: (getattr(previous, field), getattr(current, field))
        for field in fields
        if getattr(previous, field) != getattr(current, field)
    }
    before = {
        row[0]: row[1:]
        for row in previous.lines.values_list("item__code", "quantity", "price", "vat_rate")
    }
    after = {
        row[0]: row[1:]
        for row in current.lines.values_list("item__code", "quantity", "price", "vat_rate")
    }
    return {
        "terms": changes,
        "lines": {
            code: (before.get(code), after.get(code))
            for code in sorted(before.keys() | after.keys())
            if before.get(code) != after.get(code)
        },
    }
