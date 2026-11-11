from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from django.contrib.auth.models import Group, User
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from apps.masterdata.models import Item, Location, Supplier
from apps.procurement.models import SupplierInvoice
from apps.procurement.services.approvals import decide
from apps.procurement.services.invoices import InvoiceData, InvoiceLine, record_invoice
from apps.procurement.services.orders import OrderData, OrderLine, create_order
from apps.procurement.services.quotes import QuoteData, capture_quote, open_rfq, select_quote
from apps.procurement.services.receiving import Delivery, DeliveryLine, post_receipt
from apps.procurement.services.requests import (
    RequestData,
    RequestLine,
    create_request,
    submit_request,
)


def key(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"muzqaymoq/phase2/{name}")


@transaction.atomic
def seed_procurement(actor: User) -> SupplierInvoice:
    """Seed a real request-to-mismatched-invoice flow on a disposable development database."""
    existing = SupplierInvoice.objects.filter(key=key("invoice")).first()
    if existing:
        return existing
    approver, created = User.objects.get_or_create(username="phase2_demo_approver")
    group = Group.objects.get(name="Procurement Manager")
    if created:
        approver.set_unusable_password()
        approver.save(update_fields=["password"])
        approver.groups.add(group)
    elif approver.has_usable_password() or not approver.groups.filter(pk=group.pk).exists():
        raise CommandError("The reserved procurement demo approver already exists.")
    today = timezone.localdate()
    item = Item.objects.get(code="DEMO-SUT")
    location = Location.objects.get(code="DEMO-SOV")
    supplier = Supplier.objects.get(code="DEMO-SUTCHI")
    other = Supplier.objects.get(code="DEMO-QADOQ")
    quantity, price = Decimal(10), Decimal(8200)
    due = today + timedelta(days=3)
    purchase = create_request(
        actor,
        key("request"),
        RequestData(
            "Ta’minot",
            due,
            "normal",
            "Sovitilgan sut zaxirasini to‘ldirish.",
            (RequestLine(item.pk, quantity, price, supplier.pk),),
        ),
    )
    submit_request(actor, purchase.pk)
    decide(approver, purchase.approval.steps.get().pk, approve=True)
    rfq = open_rfq(actor, key("rfq"), purchase.pk, (supplier.pk, other.pk))
    line = purchase.lines.get()
    selected = capture_quote(
        actor,
        key("quote-a"),
        rfq.pk,
        QuoteData(
            supplier.pk,
            "UZS",
            Decimal(1),
            today,
            1,
            today,
            due,
            "Yetkazilgandan keyin",
            {line.pk: price},
        ),
    )
    capture_quote(
        actor,
        key("quote-b"),
        rfq.pk,
        QuoteData(
            other.pk,
            "UZS",
            Decimal(1),
            today,
            3,
            today,
            due,
            "Yetkazilgandan keyin",
            {line.pk: Decimal(8600)},
        ),
    )
    select_quote(actor, rfq.pk, selected.pk)
    order = create_order(
        actor,
        key("order"),
        OrderData(
            supplier.pk,
            "UZS",
            Decimal(1),
            today,
            due,
            (OrderLine(item.pk, quantity, price),),
        ),
        request_id=purchase.pk,
        quote_id=selected.pk,
    )
    decide(approver, order.versions.get().approval.steps.get().pk, approve=True)
    receipt = post_receipt(
        actor,
        key("receipt"),
        order.pk,
        Delivery(
            today,
            "",
            (
                DeliveryLine(
                    item.pk, location.pk, Decimal(4), "DEMO-PROC-SUT", today + timedelta(days=5)
                ),
            ),
        ),
    )
    return record_invoice(
        actor,
        key("invoice"),
        order.pk,
        InvoiceData(
            "DEMO-HF-001",
            today,
            "UZS",
            Decimal(1),
            today,
            (InvoiceLine(receipt.lines.get().pk, Decimal(4), Decimal(8500)),),
        ),
    )
