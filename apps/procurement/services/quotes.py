from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.services.common import audit, require
from apps.masterdata.models import Supplier
from apps.procurement.models import (
    RFQ,
    PurchaseRequest,
    Quote,
    QuoteLine,
    RFQSupplier,
    State,
    SupplierItemPrice,
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


@transaction.atomic
def open_rfq(actor: User, key: UUID, request_id: int, supplier_ids: tuple[int, ...]) -> RFQ:
    """Record a manual RFQ sent to distinct active suppliers."""
    require(actor, "procurement.add_rfq")
    request = PurchaseRequest.objects.select_for_update().get(pk=request_id)
    suppliers = sorted(set(supplier_ids))
    payload = {"request": request_id, "suppliers": suppliers}
    existing = replay(RFQ, actor, key, payload)
    if existing:
        return existing
    if request.state != State.APPROVED or RFQ.objects.filter(request=request).exists():
        raise ValidationError(_("Choose an approved request without an RFQ."))
    if not 2 <= len(suppliers) <= 100 or Supplier.objects.filter(
        pk__in=suppliers, active=True
    ).count() != len(suppliers):
        raise ValidationError(_("Invite at least two distinct active suppliers."))
    rfq = RFQ.objects.create(**identity(actor, key, payload), request=request)
    RFQSupplier.objects.bulk_create(
        RFQSupplier(rfq=rfq, supplier_id=supplier) for supplier in suppliers
    )
    audit(actor, "rfq_open", rfq, {}, payload)
    return rfq


@dataclass(frozen=True)
class QuoteData:
    supplier_id: int
    currency: str
    rate: Decimal
    rate_date: date
    lead_days: int
    valid_from: date
    valid_until: date
    payment_terms: str
    prices: dict[int, Decimal]


@transaction.atomic
def capture_quote(actor: User, key: UUID, rfq_id: int, data: QuoteData) -> Quote:
    """Record an immutable quote covering every request line and its dated price history."""
    require(actor, "procurement.add_quote")
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    payload = {**asdict(data), "rfq": rfq_id}
    existing = replay(Quote, actor, key, payload)
    if existing:
        return existing
    if (
        rfq.state != State.OPEN
        or not rfq.invitations.filter(supplier_id=data.supplier_id, supplier__active=True).exists()
    ):
        raise ValidationError(_("The RFQ must be open and the supplier invited."))
    if rfq.quotes.filter(supplier_id=data.supplier_id).exists():
        raise ValidationError(_("This supplier already has a quote."))
    if data.valid_until < data.valid_from or not 0 <= data.lead_days <= 3650:
        raise ValidationError(_("Enter valid quote dates and lead time."))
    lines = {line.pk: line for line in rfq.request.lines.all()}
    if lines.keys() != data.prices.keys():
        raise ValidationError(_("Enter a price for every request line."))
    rate = fx(data.currency, data.rate, data.rate_date)
    prices = {pk: amount(price) for pk, price in data.prices.items()}
    total = sum((money(lines[pk].quantity * price) for pk, price in prices.items()), Decimal(0))
    quote = Quote.objects.create(
        **identity(actor, key, payload),
        rfq=rfq,
        supplier_id=data.supplier_id,
        currency=data.currency,
        exchange_rate=rate,
        rate_date=data.rate_date,
        lead_days=data.lead_days,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        payment_terms=note(data.payment_terms, 240),
        total=total,
        total_uzs=money(total * rate),
    )
    quotes = QuoteLine.objects.bulk_create(
        QuoteLine(quote=quote, request_line_id=pk, price=price) for pk, price in prices.items()
    )
    SupplierItemPrice.objects.bulk_create(
        SupplierItemPrice(
            supplier_id=data.supplier_id,
            item_id=lines[line.request_line_id].item_id,
            quote_line=line,
            currency=data.currency,
            price=line.price,
            valid_from=data.valid_from,
            valid_until=data.valid_until,
        )
        for line in quotes
    )
    audit(actor, "quote_capture", quote, {}, {**payload, "total_uzs": quote.total_uzs})
    return quote


def latest_valid_price(
    supplier_id: int, item_id: int, currency: str, on: date
) -> SupplierItemPrice | None:
    """Prefer the most recent valid-from date, then latest recorded quote."""
    return (
        SupplierItemPrice.objects.filter(
            supplier_id=supplier_id,
            item_id=item_id,
            currency=currency,
            valid_from__lte=on,
            valid_until__gte=on,
        )
        .order_by("-valid_from", "-pk")
        .first()
    )


@transaction.atomic
def select_quote(actor: User, rfq_id: int, quote_id: int, reason: str = "") -> RFQ:
    """Select a valid quote and explain any premium over the least-cost eligible quote."""
    require(actor, "procurement.add_rfq")
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    reason = note(reason, 300, required=False)
    if (rfq.state, rfq.selected_id, rfq.selection_reason) == (State.SELECTED, quote_id, reason):
        return rfq
    check_transition("rfq", rfq.state, State.SELECTED)
    today = timezone.localdate()
    eligible = rfq.quotes.filter(
        valid_from__lte=today, valid_until__gte=today, supplier__active=True
    )
    quote = eligible.filter(pk=quote_id).first()
    if quote is None:
        raise ValidationError(_("Choose a currently valid quote."))
    cheapest = eligible.order_by("total_uzs", "pk").first()
    if cheapest and quote.total_uzs > cheapest.total_uzs:
        note(reason, 300)
    rfq.state, rfq.selected, rfq.selection_reason = State.SELECTED, quote, reason
    rfq.save(update_fields=["state", "selected", "selection_reason"])
    audit(actor, "quote_select", rfq, {"state": State.OPEN}, {"quote": quote_id, "reason": reason})
    return rfq


@transaction.atomic
def cancel_rfq(actor: User, rfq_id: int, reason: str) -> RFQ:
    require(actor, "procurement.cancel_order")
    rfq = RFQ.objects.select_for_update().get(pk=rfq_id)
    reason = note(reason, 300)
    if rfq.state == State.CANCELLED:
        return rfq
    check_transition("rfq", rfq.state, State.CANCELLED)
    rfq.state = State.CANCELLED
    rfq.save(update_fields=["state"])
    audit(
        actor,
        "rfq_cancel",
        rfq,
        {"state": State.OPEN},
        {"state": State.CANCELLED, "reason": reason},
    )
    return rfq
