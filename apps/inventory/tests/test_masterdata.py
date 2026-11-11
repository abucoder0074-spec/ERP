from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from pytest_django.fixtures import SettingsWrapper

from apps.inventory.models import Batch, StockMovement
from apps.inventory.tests.conftest import Setup
from apps.masterdata.models import Item, Supplier, UnitOfMeasure, Warehouse
from apps.masterdata.services import save_record

pytestmark = pytest.mark.django_db


def test_catalog_validation_and_archival(stock: Setup) -> None:
    stock.receive("LOT")
    item = {
        "code": stock.item.code,
        "name_uz": "Sut",
        "category": stock.item.category_id,
        "base_unit": stock.item.base_unit_id,
        "storage": "dry",
        "min_stock": 0,
        "reorder_qty": 0,
        "active": True,
    }
    form, result = save_record(stock.actor, "items", item, stock.item.pk)
    assert result is None and "fixed after stock" in str(form.errors)
    item["storage"] = "chilled"
    item["active"] = False
    form, result = save_record(stock.actor, "items", item, stock.item.pk)
    assert result is None and "remaining stock" in str(form.errors)
    stock.item.category.active = False
    stock.item.category.save()
    item["active"] = True
    form, result = save_record(stock.actor, "items", item)
    assert result is None and form.errors
    item["code"] = "NEW"
    form, result = save_record(stock.actor, "items", item)
    assert result is None and "active category" in str(form.errors)
    location = {
        "code": stock.source.code,
        "name": stock.source.name,
        "warehouse": stock.source.warehouse_id,
        "storage": "dry",
        "active": True,
    }
    form, result = save_record(stock.actor, "locations", location, stock.source.pk)
    assert result is None and "fixed after stock" in str(form.errors)
    location.update(storage="chilled", active=False)
    form, result = save_record(stock.actor, "locations", location, stock.source.pk)
    assert result is None and "remaining stock" in str(form.errors)
    warehouse = stock.source.warehouse
    form, result = save_record(
        stock.actor,
        "warehouses",
        {"code": warehouse.code, "name": warehouse.name},
        warehouse.pk,
    )
    assert result is None and "remaining stock" in str(form.errors)
    inactive = Warehouse.objects.create(code="inactive", name="Arxiv", active=False)
    location.update(code="OTHER", warehouse=inactive.pk, active=True)
    form, result = save_record(stock.actor, "locations", location)
    assert result is None and "active warehouse" in str(form.errors)
    with pytest.raises(IntegrityError), transaction.atomic():
        Supplier.objects.create(code="TEMP", name="Vaqtinchalik").delete()


def test_conversion_catalog_enforces_valid_active_units(stock: Setup) -> None:
    data = {
        "item": stock.item.pk,
        "unit": stock.item.base_unit_id,
        "factor": Decimal(1),
        "active": True,
    }
    form, result = save_record(stock.actor, "conversions", data)
    assert result is None and "differ" in str(form.errors)
    box = UnitOfMeasure.objects.create(code="BOX", name="Quti", active=False)
    data["unit"] = box.pk
    form, result = save_record(stock.actor, "conversions", data)
    assert result is None and "active item and unit" in str(form.errors)
    box.active = True
    box.save()
    _form, result = save_record(stock.actor, "conversions", data)
    assert result is not None
    _form, saved = save_record(stock.actor, "conversions", data, result.pk)
    assert saved == result


def test_demo_seed_is_repeatable_and_development_only(settings: SettingsWrapper) -> None:
    with pytest.raises(CommandError, match="only in development"):
        call_command("seed_demo")
    settings.DEBUG = True
    out = StringIO()
    for _ in range(2):
        call_command("seed_demo", stdout=out)
    assert Item.objects.filter(code__startswith="DEMO-").count() == 4
    assert Supplier.objects.filter(code__startswith="DEMO-").count() == 3
    assert Batch.objects.filter(quality="quarantine").count() == 1
    assert StockMovement.objects.count() == 5
    assert "5 batches" in out.getvalue()
