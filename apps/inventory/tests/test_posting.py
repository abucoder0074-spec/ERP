from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from threading import Barrier
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone

from apps.inventory.models import (
    AuditLog,
    MovementType,
    Quality,
    StockBalance,
    StockMovement,
    StockOperation,
    WarehouseCost,
)
from apps.inventory.services.common import positive_quantity
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
from apps.inventory.tests.conftest import Setup
from apps.masterdata.models import UnitOfMeasure, UoMConversion

pytestmark = pytest.mark.django_db


def test_fefo_splits_and_excludes_expired_or_quarantined(stock: Setup) -> None:
    today = timezone.localdate()
    stock.receive("expired", expiry=today - timedelta(days=1))
    stock.receive("quarantine", expiry=today, approved=False)
    stock.receive("undated")
    late = stock.receive("late", expiry=today + timedelta(days=5))
    early = stock.receive("early", expiry=today)
    operation = issue_stock(
        stock.actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal("14"))
    )
    assert list(operation.movements.order_by("ordinal").values_list("batch_id", "quantity")) == [
        (early.pk, Decimal("-10")),
        (late.pk, Decimal("-4")),
    ]
    with pytest.raises(ValidationError):
        issue_stock(stock.actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal("17")))
    assert StockBalance.objects.get(batch=late).quantity == Decimal("6")


def test_override_requires_permission_reason_and_is_audited(stock: Setup) -> None:
    early = stock.receive("early", expiry=timezone.localdate())
    late = stock.receive("late", expiry=timezone.localdate() + timedelta(days=3))
    keeper = User.objects.create(username="keeper")
    keeper.groups.add(Group.objects.get(name="Warehouse Keeper"))
    data = Withdrawal(
        stock.item.pk, stock.source.pk, Decimal(1), batch_id=late.pk, reason="Tekshiruv"
    )
    with pytest.raises(PermissionDenied):
        issue_stock(keeper, uuid4(), data)
    with pytest.raises(ValidationError):
        issue_stock(
            stock.actor,
            uuid4(),
            Withdrawal(stock.item.pk, stock.source.pk, Decimal(1), batch_id=late.pk),
        )
    op = issue_stock(stock.actor, uuid4(), data)
    assert op.movements.get().batch == late
    assert StockBalance.objects.get(batch=early).quantity == Decimal(10)
    assert (
        AuditLog.objects.get(subject=f"inventory.stockoperation:{op.pk}").after["movements"][0][
            "reason"
        ]
        == "Tekshiruv"
    )


def test_weighted_average_transfer_reversal_and_reconciliation(stock: Setup) -> None:
    lot = stock.receive("one", "10", "10")
    stock.receive("two", "10", "20")
    issue = issue_stock(
        stock.actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal(4))
    )
    assert issue.movements.get().unit_cost == Decimal("15.00")
    source = WarehouseCost.objects.get(warehouse=stock.source.warehouse)
    assert (source.quantity, source.carrying_value) == (Decimal(16), Decimal(240))
    transfer_key = uuid4()
    data = Withdrawal(stock.item.pk, stock.source.pk, Decimal(2), batch_id=lot.pk)
    transfer = transfer_stock(stock.actor, transfer_key, data, stock.target.pk)
    assert transfer_stock(stock.actor, transfer_key, data, stock.target.pk) == transfer
    assert list(transfer.movements.order_by("ordinal").values_list("value_delta", flat=True)) == [
        Decimal(-30),
        Decimal(30),
    ]
    target = WarehouseCost.objects.get(warehouse=stock.target.warehouse)
    assert target.carrying_value == Decimal(30)
    reverse_key = uuid4()
    reversal = reverse_movement(stock.actor, reverse_key, transfer.pk, "Joy tuzatildi")
    assert reverse_movement(stock.actor, reverse_key, transfer.pk, "Joy tuzatildi") == reversal
    with pytest.raises(ValidationError):
        reverse_movement(stock.actor, uuid4(), transfer.pk, "Ikkinchi urinish")
    source.refresh_from_db()
    target.refresh_from_db()
    assert (source.quantity, source.carrying_value, target.quantity, target.carrying_value) == (
        Decimal(16),
        Decimal(240),
        Decimal(0),
        Decimal(0),
    )
    assert StockBalance.objects.get(batch=lot, location=stock.source).quantity == Decimal(6)
    out = StringIO()
    call_command("reconcile_stock", stdout=out)
    assert "reconcile" in out.getvalue()
    StockBalance.objects.filter(batch=lot, location=stock.source).update(quantity=Decimal(7))
    error = StringIO()
    with pytest.raises(CommandError, match="1 stock discrepancies"):
        call_command("reconcile_stock", stderr=error)
    assert "ledger=6.000 balance=7.000" in error.getvalue()


def test_service_and_db_reject_negative_and_mutation(stock: Setup) -> None:
    lot = stock.receive("one", "1")
    movement = StockMovement.objects.get(batch=lot)
    with pytest.raises(ValidationError):
        issue_stock(stock.actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal(2)))
    with pytest.raises(ValidationError), transaction.atomic():
        movement.save()
    with pytest.raises(ValidationError):
        movement.delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        StockMovement.objects.filter(pk=movement.pk).update(reason="tamper")
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM inventory_stockmovement WHERE id = %s", [movement.pk])
    with pytest.raises(IntegrityError), transaction.atomic():
        StockBalance.objects.filter(batch=lot).update(quantity=Decimal(-1))
    assert StockOperation.objects.count() == 1


def test_quantities_conversion_and_storage(stock: Setup) -> None:
    unit = UnitOfMeasure.objects.create(code="G", name="Gramm")
    conversion = UoMConversion.objects.create(item=stock.item, unit=unit, factor=Decimal("0.001"))
    assert positive_quantity(stock.item, Decimal("500"), unit.pk) == Decimal("0.500")
    with pytest.raises(ValidationError, match="precision"):
        positive_quantity(stock.item, Decimal("0.5"), unit.pk)
    conversion.active = False
    conversion.save()
    with pytest.raises(ValidationError):
        positive_quantity(stock.item, Decimal(1), unit.pk)
    with pytest.raises(ValidationError):
        positive_quantity(stock.item, Decimal("NaN"), None)
    data = Receipt(
        stock.item.pk,
        stock.incompatible.pk,
        stock.supplier.pk,
        "bad",
        Decimal(1),
        Decimal(10),
        "REF",
    )
    with pytest.raises(ValidationError, match="conditions"):
        receive_goods(stock.actor, uuid4(), data)
    stock.receive("good")
    with pytest.raises(ValidationError, match="conditions"):
        transfer_stock(
            stock.actor,
            uuid4(),
            Withdrawal(stock.item.pk, stock.source.pk, Decimal(1)),
            stock.incompatible.pk,
        )


def test_posting_retries_and_writeoff_threshold(stock: Setup) -> None:
    key = uuid4()
    receipt = Receipt(
        stock.item.pk, stock.source.pk, stock.supplier.pk, "LOT", Decimal(10), Decimal(200000), "IN"
    )
    operation = receive_goods(stock.actor, key, receipt)
    assert receive_goods(stock.actor, key, receipt) == operation
    lot = operation.movements.get().batch
    assert lot.quality == Quality.QUARANTINE
    change_quality(stock.actor, lot.pk, Quality.APPROVED)
    change_quality(stock.actor, lot.pk, Quality.APPROVED)
    with pytest.raises(ValidationError):
        change_quality(stock.actor, lot.pk, Quality.REJECTED)
    with pytest.raises(ValidationError):
        issue_stock(stock.actor, key, Withdrawal(stock.item.pk, stock.source.pk, Decimal(1)))
    data = Withdrawal(
        stock.item.pk, stock.source.pk, Decimal(1), batch_id=lot.pk, reason="Yaroqsiz"
    )
    keeper = User.objects.create(username="keeper")
    keeper.groups.add(Group.objects.get(name="Warehouse Keeper"))
    with pytest.raises(PermissionDenied):
        write_off(keeper, uuid4(), data)
    assert StockBalance.objects.get(batch=lot).quantity == Decimal(10)
    for action in (issue_stock, write_off, return_to_supplier):
        key = uuid4()
        result = action(stock.actor, key, data)
        assert action(stock.actor, key, data) == result
        assert result.movements.count() == 1
    assert StockMovement.objects.filter(kind=MovementType.RECEIPT).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_issues_of_last_unit(stock: Setup) -> None:
    lot = stock.receive("last", "1")
    barrier = Barrier(2)

    def issue() -> bool:
        close_old_connections()
        try:
            actor = User.objects.get(pk=stock.actor.pk)
            barrier.wait(timeout=10)
            issue_stock(actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal(1)))
            return True
        except ValidationError:
            return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(issue) for _ in range(2)]
        assert sorted(job.result(timeout=20) for job in jobs) == [False, True]
    assert StockBalance.objects.get(batch=lot).quantity == 0
    assert StockMovement.objects.filter(kind=MovementType.ISSUE).count() == 1
