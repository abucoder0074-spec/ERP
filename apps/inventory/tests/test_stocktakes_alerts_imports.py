from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from openpyxl import Workbook

from apps.inventory.models import AlertKind, Notification, StockBalance, StocktakeStatus
from apps.inventory.services.alerts import refresh_alerts
from apps.inventory.services.imports import import_records, read_rows
from apps.inventory.services.posting import Withdrawal, issue_stock
from apps.inventory.services.stocktake import (
    count_batch,
    post_stocktake,
    start_stocktake,
    transition,
)
from apps.inventory.tests.conftest import Setup
from apps.masterdata.models import Item, Supplier

pytestmark = pytest.mark.django_db


def test_stocktake_approval_posting_and_stale_snapshot(stock: Setup) -> None:
    lot = stock.receive("LOT", "7")
    found = stock.receive("FOUND", "1")
    issue_stock(
        stock.actor,
        uuid4(),
        Withdrawal(stock.item.pk, stock.source.pk, Decimal(1), batch_id=found.pk, reason="Sinov"),
    )
    manager = User.objects.create(username="approver", is_superuser=True)
    start_key = uuid4()
    sheet = start_stocktake(stock.actor, start_key, stock.source.pk)
    assert start_stocktake(stock.actor, start_key, stock.source.pk) == sheet
    with pytest.raises(ValidationError):
        transition(stock.actor, sheet.pk, StocktakeStatus.SUBMITTED)
    count_batch(stock.actor, sheet.pk, lot.pk, Decimal(5))
    count_batch(stock.actor, sheet.pk, found.pk, Decimal(1))
    transition(stock.actor, sheet.pk, StocktakeStatus.SUBMITTED)
    with pytest.raises(PermissionDenied):
        transition(stock.actor, sheet.pk, StocktakeStatus.APPROVED)
    transition(manager, sheet.pk, StocktakeStatus.APPROVED)
    key = uuid4()
    operation = post_stocktake(manager, key, sheet.pk)
    assert post_stocktake(manager, key, sheet.pk) == operation
    assert StockBalance.objects.get(batch=lot).quantity == Decimal(5)
    assert StockBalance.objects.get(batch=found).quantity == Decimal(1)
    assert sorted(operation.movements.values_list("quantity", flat=True)) == [
        Decimal(-2),
        Decimal(1),
    ]
    stale = start_stocktake(stock.actor, uuid4(), stock.source.pk)
    count_batch(stock.actor, stale.pk, lot.pk, Decimal(5))
    count_batch(stock.actor, stale.pk, found.pk, Decimal(1))
    transition(stock.actor, stale.pk, StocktakeStatus.SUBMITTED)
    transition(manager, stale.pk, StocktakeStatus.APPROVED)
    issue_stock(stock.actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal(1)))
    with pytest.raises(ValidationError, match="snapshot"):
        post_stocktake(manager, uuid4(), stale.pk)
    transition(manager, stale.pk, StocktakeStatus.CANCELLED)


def test_alerts_are_deduplicated_resolved_and_reopened(stock: Setup) -> None:
    Item.objects.filter(pk=stock.item.pk).update(min_stock=20, max_stock=30)
    today = timezone.localdate()
    stock.receive("soon", "3", expiry=today + timedelta(days=2))
    stock.receive("expired", "2", expiry=today - timedelta(days=1))
    assert refresh_alerts() == 3
    assert refresh_alerts() == 0
    assert set(Notification.objects.values_list("kind", flat=True)) == {
        AlertKind.LOW,
        AlertKind.EXPIRING,
        AlertKind.EXPIRED,
    }
    stock.receive("surplus", "30")
    assert refresh_alerts() == 1
    assert Notification.objects.get(kind=AlertKind.LOW).resolved_at is not None
    issue_stock(stock.actor, uuid4(), Withdrawal(stock.item.pk, stock.source.pk, Decimal(20)))
    assert refresh_alerts() == 1
    assert Notification.objects.filter(kind=AlertKind.LOW, resolved_at__isnull=True).count() == 1


def test_import_validation_excel_csv_commit_and_row_errors(stock: Setup) -> None:
    key = uuid4()
    content = b"code,name,lead_time_days\nNEW,Yangi,2\nBAD,Buzuq,-5\n"
    failed = import_records(stock.actor, "suppliers", "suppliers.csv", content, key, dry_run=False)
    assert [error.row for error in failed.errors] == [3]
    assert not Supplier.objects.filter(code="NEW").exists()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["code", "name"])
    sheet.append(["NEW", "Yangi"])
    stream = BytesIO()
    workbook.save(stream)
    content = stream.getvalue()
    preview = import_records(stock.actor, "suppliers", "suppliers.xlsx", content, key, dry_run=True)
    assert not preview.errors and not preview.committed
    assert not Supplier.objects.filter(code="NEW").exists()
    assert import_records(
        stock.actor, "suppliers", "suppliers.xlsx", content, key, dry_run=False
    ).committed
    assert Supplier.objects.get(code="NEW").name == "Yangi"
    items = b"code,name_uz,category,base_unit,storage\nYOG,Yog,RAW,KG,chilled\n"
    assert import_records(stock.actor, "items", "items.csv", items, key, dry_run=False).committed
    opening = (
        b"item,location,supplier,batch_number,quantity,unit_cost,reference\n"
        b"YOG,A-1,SUP,B1,2.500,12.00,R1\n"
    )
    assert not import_records(stock.actor, "opening", "open.csv", opening, key, dry_run=True).errors
    assert not StockBalance.objects.exists()
    for _ in range(2):
        assert import_records(
            stock.actor, "opening", "open.csv", opening, key, dry_run=False
        ).committed
    assert StockBalance.objects.get().quantity == Decimal("2.500")
    with pytest.raises(ValidationError, match="could not be read"):
        read_rows("broken.xlsx", b"not excel")
