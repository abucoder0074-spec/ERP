from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from PIL import Image
from pytest_django.fixtures import SettingsWrapper

from apps.inventory.models import AuditLog, Quality, StockBalance, StockMovement
from apps.inventory.services.posting import reverse_movement
from apps.inventory.services.reconciliation import discrepancies
from apps.masterdata.models import Item
from apps.procurement.models import DeliveryAlert, PurchaseOrderLine, State
from apps.procurement.services.approvals import decide
from apps.procurement.services.attachments import attach
from apps.procurement.services.deliveries import refresh_delivery_alerts, supplier_metrics
from apps.procurement.services.invoices import (
    InvoiceData,
    InvoiceLine,
    ready_invoices,
    record_invoice,
    resolve_invoice,
)
from apps.procurement.services.orders import amend_order, cancel_order, totals, version_diff
from apps.procurement.services.receiving import Delivery, DeliveryLine, post_receipt, post_return
from apps.procurement.tests.conftest import Setup

pytestmark = pytest.mark.django_db


def photo() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4)).save(stream, format="PNG")
    return stream.getvalue()


def test_partial_receipts_cumulative_tolerance_and_unplanned(setup: Setup) -> None:
    order = setup.order()
    key = uuid4()
    data = Delivery(
        setup.today,
        "",
        (DeliveryLine(setup.item.pk, setup.location.pk, Decimal("4"), "SAME", setup.today),),
    )
    one = post_receipt(setup.actor, key, order.pk, data)
    assert post_receipt(setup.actor, key, order.pk, data) == one
    line = one.lines.select_related("batch").get()
    assert line.batch.quality == Quality.QUARANTINE
    assert line.batch.expiry == setup.today
    two = setup.receipt(order, "6.500", "SAME")
    assert two.lines.get().supplier_batch == "SAME"
    with pytest.raises(ValidationError):
        setup.receipt(order, "0.001", "EXCESS")
    with pytest.raises(ValidationError):
        reverse_movement(setup.actor, uuid4(), line.operation_id, "Bekor")
    item = Item.objects.create(
        code="NEW",
        name_uz="Sut 2",
        category=setup.item.category,
        base_unit=setup.item.base_unit,
        storage="chilled",
    )
    extra = Delivery(
        setup.today,
        "",
        (
            DeliveryLine(
                item.pk,
                setup.location.pk,
                Decimal(1),
                "EXTRA",
                unplanned_price=Decimal(20),
                unplanned_reason="Almashtirish",
            ),
        ),
    )
    keeper = User.objects.create(username="keeper")
    keeper.groups.add(Group.objects.get(name="Warehouse Keeper"))
    with pytest.raises(PermissionDenied):
        post_receipt(keeper, uuid4(), order.pk, extra)
    assert post_receipt(setup.actor, uuid4(), order.pk, extra).lines.get().order_line_id is None
    assert not discrepancies()


def test_amendment_diff_cancellation_and_immutable_terms(setup: Setup) -> None:
    order = setup.order()
    received = setup.receipt(order, "4")
    original = received.version
    with pytest.raises(ValidationError):
        amend_order(setup.actor, uuid4(), order.pk, setup.order_data("3"), "Kamroq")
    with pytest.raises(ValidationError):
        amend_order(setup.actor, uuid4(), order.pk, setup.order_data("12", "110"), "Narx")
    key = uuid4()
    data = setup.order_data("12")
    version = amend_order(setup.actor, key, order.pk, data, "Ko‘proq")
    assert amend_order(setup.actor, key, order.pk, data, "Ko‘proq") == version
    assert version_diff(original, version)["lines"]
    with pytest.raises(IntegrityError), transaction.atomic():
        PurchaseOrderLine.objects.filter(version=original).update(price=1)
    decide(setup.approver, version.approval.steps.get().pk, approve=True)
    original.refresh_from_db()
    order.refresh_from_db()
    assert original.state == State.SUPERSEDED and order.active_version_id == version.pk
    assert cancel_order(setup.actor, order.pk, "Qolgan miqdor bekor").state == State.CANCELLED
    assert cancel_order(setup.actor, order.pk, "Qolgan miqdor bekor").state == State.CANCELLED
    with pytest.raises(ValidationError):
        setup.receipt(order, "1")
    assert received.version_id == original.pk
    assert totals(setup.order_data("1.005", "10.01").lines, Decimal("2.50"), Decimal(12)) == (
        Decimal("10.06"),
        Decimal("1.21"),
        Decimal("28.18"),
    )


def test_defects_returns_delays_and_alerts(setup: Setup) -> None:
    order = setup.order()
    tomorrow = setup.today + timedelta(days=1)
    with patch("django.utils.timezone.localdate", return_value=tomorrow):
        assert refresh_delivery_alerts() == 1
        assert refresh_delivery_alerts() == 0
        data = Delivery(
            tomorrow,
            "Yo‘l yopildi",
            (DeliveryLine(setup.item.pk, setup.location.pk, Decimal(4), "LATE"),),
        )
        with pytest.raises(ValidationError):
            post_receipt(setup.actor, uuid4(), order.pk, replace(data, delay_reason=""))
        receipt = post_receipt(setup.actor, uuid4(), order.pk, data)
    line = receipt.lines.get()
    with pytest.raises(ValidationError):
        post_return(setup.actor, uuid4(), line.pk, Decimal(1), "Shikast", 0)
    file = attach(setup.actor, uuid4(), "receipt", receipt.pk, "damage.png", photo())
    key = uuid4()
    returned = post_return(setup.actor, key, line.pk, Decimal(1), "Shikast", file.pk)
    assert post_return(setup.actor, key, line.pk, Decimal(1), "Shikast", file.pk) == returned
    assert StockBalance.objects.get(batch=line.batch).quantity == Decimal(3)
    assert supplier_metrics()[setup.supplier.pk] == (1, 0, 1)
    assert not discrepancies()
    cancel_order(setup.actor, order.pk, "Qolgan yetkazuv bekor")
    refresh_delivery_alerts()
    assert DeliveryAlert.objects.get(order=order).resolved_at


def test_invoice_exact_tolerances_duplicates_and_override(
    setup: Setup, settings: SettingsWrapper
) -> None:
    settings.PROCUREMENT_QUANTITY_MATCH_PERCENT = Decimal(1)
    order = setup.order()
    lines = [setup.receipt(order, "2", f"B{i}").lines.get() for i in range(1, 5)]

    def record(number: str, index: int, quantity: str, price: str) -> InvoiceData:
        return InvoiceData(
            number,
            setup.today,
            "UZS",
            Decimal(1),
            setup.today,
            (InvoiceLine(lines[index].pk, Decimal(quantity), Decimal(price)),),
        )

    exact = record_invoice(setup.actor, uuid4(), order.pk, record("INV-1", 0, "2", "100"))
    assert exact.state == State.READY
    assert (exact.tax, exact.total, exact.total_uzs) == (Decimal(24), Decimal(224), Decimal(224))
    allowed = record_invoice(setup.actor, uuid4(), order.pk, record("INV-2", 1, "2.020", "101"))
    assert allowed.state == State.READY
    key = uuid4()
    data = record("INV-3", 2, "2.021", "101.01")
    failed = record_invoice(setup.actor, key, order.pk, data)
    assert failed.state == State.MISMATCH
    assert failed.mismatches[0]["reasons"] == ["quantity", "price"]
    assert not ready_invoices().filter(pk=failed.pk).exists()
    assert record_invoice(setup.actor, key, order.pk, data) == failed
    with pytest.raises(ValidationError):
        record_invoice(setup.actor, uuid4(), order.pk, record(" inv-1 ", 3, "2", "100"))
    with pytest.raises(ValidationError):
        record_invoice(setup.actor, uuid4(), order.pk, record("INV-X", 0, "2", "100"))
    with pytest.raises(ValidationError):
        resolve_invoice(setup.actor, failed.pk, "")
    ready = resolve_invoice(setup.actor, failed.pk, "Tafovut tasdiqlandi")
    assert resolve_invoice(setup.actor, failed.pk, "Tafovut tasdiqlandi") == ready
    assert ready_invoices().filter(pk=failed.pk).exists()
    assert AuditLog.objects.filter(action="invoice_resolution").count() == 1
    assert (
        resolve_invoice(setup.actor, failed.pk, "Qayta ko‘rib chiqish", cancel=True).state
        == State.CANCELLED
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_receipts_serialize_order_line(setup: Setup) -> None:
    order = setup.order()
    barrier = Barrier(2)

    def receive(index: int) -> bool:
        try:
            actor = User.objects.get(pk=setup.actor.pk)
            barrier.wait(timeout=10)
            post_receipt(
                actor,
                uuid4(),
                order.pk,
                Delivery(
                    setup.today,
                    "",
                    (DeliveryLine(setup.item.pk, setup.location.pk, Decimal(6), f"CON-{index}"),),
                ),
            )
            return True
        except ValidationError:
            return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(receive, (1, 2))) == [False, True]
    assert StockMovement.objects.count() == 1
    assert not discrepancies()


def test_activation_rechecks_receipts_during_approval(setup: Setup) -> None:
    order = setup.order()
    version = amend_order(
        setup.actor, uuid4(), order.pk, setup.order_data("10", "115"), "Yangi narx"
    )
    setup.receipt(order, "1")
    step = version.approval.steps.get()
    with pytest.raises(ValidationError):
        decide(setup.approver, step.pk, approve=True)
    step.refresh_from_db()
    assert step.state == State.SUBMITTED
    version.refresh_from_db()
    assert version.state == State.SUBMITTED


def test_amendment_retains_received_vat_and_configured_release(
    setup: Setup, settings: SettingsWrapper
) -> None:
    order = setup.order()
    settings.PROCUREMENT_QUARANTINE_COLD = False
    receipt = setup.receipt(order, "2")
    assert receipt.lines.get().batch.quality == Quality.APPROVED
    settings.VAT_RATE_PERCENT = Decimal(15)
    version = amend_order(setup.actor, uuid4(), order.pk, setup.order_data("12"), "Qo‘shimcha")
    assert version.lines.get().vat_rate == Decimal(12)
    assert version.tax == Decimal(144)
