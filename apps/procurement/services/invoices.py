from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.services.common import audit, require
from apps.procurement.models import (
    GoodsReceiptLine,
    PurchaseOrder,
    State,
    SupplierInvoice,
    SupplierInvoiceLine,
)
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
class InvoiceLine:
    receipt_line_id: int
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class InvoiceData:
    number: str
    invoice_date: date
    currency: str
    rate: Decimal
    rate_date: date
    lines: tuple[InvoiceLine, ...]


def outside(actual: Decimal, expected: Decimal, tolerance: Decimal) -> bool:
    return abs(actual - expected) * 100 > expected * tolerance


@transaction.atomic
def record_invoice(actor: User, key: UUID, order_id: int, data: InvoiceData) -> SupplierInvoice:
    """Reserve exact receipt lines and determine readiness from quantity and price tolerances."""
    require(actor, "procurement.add_supplierinvoice")
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    payload = {**asdict(data), "order": order_id}
    existing = replay(SupplierInvoice, actor, key, payload)
    if existing:
        return existing
    number = note(data.number, 80).upper()
    if data.invoice_date > timezone.localdate():
        raise ValidationError(_("Invoice date cannot be in the future."))
    rate = fx(data.currency, data.rate, data.rate_date)
    if SupplierInvoice.objects.filter(supplier_id=order.supplier_id, number=number).exists():
        raise ValidationError(_("This supplier invoice number already exists."))
    ids = {line.receipt_line_id for line in data.lines}
    receipts = (
        GoodsReceiptLine.objects.filter(receipt__order=order)
        .select_related(
            "receipt__version",
            "order_line",
        )
        .annotate(returned=Sum("returns__quantity"))
        .in_bulk(ids)
    )
    if not 1 <= len(ids) <= 100 or len(ids) != len(data.lines) or len(ids) != len(receipts):
        raise ValidationError(_("Use distinct receipt lines from this order."))
    if (
        SupplierInvoiceLine.objects.filter(receipt_line_id__in=ids)
        .exclude(invoice__state=State.CANCELLED)
        .exists()
    ):
        raise ValidationError(_("A receipt line already belongs to another invoice."))
    mismatches = []
    total = Decimal(0)
    tax = Decimal(0)
    for line in data.lines:
        quantity, price = amount(line.quantity, quantity=True), amount(line.price)
        receipt = receipts[line.receipt_line_id]
        net = receipt.quantity - (receipt.returned or 0)
        if net <= 0:
            raise ValidationError(_("A fully returned receipt cannot be invoiced."))
        net_amount = money(quantity * price)
        vat = receipt.order_line.vat_rate if receipt.order_line else settings.VAT_RATE_PERCENT
        line_tax = money(net_amount * vat / 100)
        tax += line_tax
        total += net_amount + line_tax
        reasons = []
        if receipt.order_line is None:
            reasons.append("unplanned")
        if outside(quantity, net, settings.PROCUREMENT_QUANTITY_MATCH_PERCENT):
            reasons.append("quantity")
        if receipt.receipt.version.currency != data.currency:
            reasons.append("currency")
        elif outside(price, receipt.price, settings.PROCUREMENT_PRICE_MATCH_PERCENT):
            reasons.append("price")
        if reasons:
            mismatches.append({"receipt_line": receipt.pk, "reasons": reasons})
    try:
        with transaction.atomic():
            invoice = SupplierInvoice.objects.create(
                **identity(actor, key, payload),
                order=order,
                supplier_id=order.supplier_id,
                number=number,
                invoice_date=data.invoice_date,
                currency=data.currency,
                exchange_rate=rate,
                rate_date=data.rate_date,
                state=State.MISMATCH if mismatches else State.READY,
                mismatches=mismatches,
                total=money(total),
                tax=money(tax),
                total_uzs=money(total * rate),
            )
    except IntegrityError as error:
        raise ValidationError(
            _("This supplier invoice number or posting key already exists.")
        ) from error
    SupplierInvoiceLine.objects.bulk_create(
        SupplierInvoiceLine(
            invoice=invoice,
            receipt_line_id=line.receipt_line_id,
            quantity=line.quantity,
            price=line.price,
        )
        for line in data.lines
    )
    audit(
        actor,
        "invoice_match",
        invoice,
        {},
        {"state": invoice.state, "mismatches": mismatches, "total": total},
    )
    return invoice


@transaction.atomic
def resolve_invoice(
    actor: User, invoice_id: int, reason: str, *, cancel: bool = False
) -> SupplierInvoice:
    """Move a mismatch to readiness with a recorded override, or release it by cancelling."""
    require(actor, "procurement.override_match")
    order_id = SupplierInvoice.objects.values_list("order_id", flat=True).get(pk=invoice_id)
    PurchaseOrder.objects.select_for_update().get(pk=order_id)
    invoice = SupplierInvoice.objects.select_for_update().get(pk=invoice_id)
    reason = note(reason, 300)
    target = State.CANCELLED if cancel else State.READY
    field = "cancellation_reason" if cancel else "override_reason"
    if invoice.state == target and getattr(invoice, field) == reason:
        return invoice
    previous = invoice.state
    check_transition("invoice", previous, target)
    invoice.state = target
    setattr(invoice, field, reason)
    invoice.save(update_fields=["state", field])
    audit(
        actor,
        "invoice_resolution",
        invoice,
        {"state": previous},
        {"state": target, "reason": reason},
    )
    return invoice


def ready_invoices() -> QuerySet[SupplierInvoice]:
    """Provide the Phase 3 readiness boundary over immutable posted invoice values."""
    return SupplierInvoice.objects.filter(state=State.READY)
