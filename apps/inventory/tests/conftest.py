from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import User

from apps.inventory.models import Batch, Quality
from apps.inventory.services.posting import Receipt, change_quality, receive_goods
from apps.masterdata.models import Item, ItemCategory, Location, Supplier, UnitOfMeasure, Warehouse


@dataclass
class Setup:
    actor: User
    item: Item
    source: Location
    target: Location
    incompatible: Location
    supplier: Supplier

    def receive(
        self,
        number: str,
        quantity: str = "10",
        cost: str = "10.00",
        expiry: date | None = None,
        *,
        approved: bool = True,
    ) -> Batch:
        receive_goods(
            self.actor,
            uuid4(),
            Receipt(
                self.item.pk,
                self.source.pk,
                self.supplier.pk,
                number,
                Decimal(quantity),
                Decimal(cost),
                f"IN-{number}",
                expiry,
            ),
        )
        batch = Batch.objects.get(item=self.item, number=number)
        if approved:
            change_quality(self.actor, batch.pk, Quality.APPROVED)
        return batch


@pytest.fixture
def stock() -> Setup:
    actor = User.objects.create(username="test-operator", is_superuser=True)
    unit = UnitOfMeasure.objects.create(code="KG", name="Kilogramm")
    category = ItemCategory.objects.create(code="RAW", name="Xomashyo", kind="raw")
    item = Item.objects.create(
        code="SUT", name_uz="Sut", storage="chilled", base_unit=unit, category=category
    )
    source_warehouse = Warehouse.objects.create(code="A", name="Toshkent")
    target_warehouse = Warehouse.objects.create(code="B", name="Chirchiq")
    source = Location.objects.create(
        code="A-1", name="Sovitkich", warehouse=source_warehouse, storage="chilled"
    )
    target = Location.objects.create(
        code="B-1", name="Sovitkich", warehouse=target_warehouse, storage="chilled"
    )
    incompatible = Location.objects.create(
        code="B-2", name="Quruq", warehouse=target_warehouse, storage="dry"
    )
    supplier = Supplier.objects.create(code="SUP", name="Chirchiq Sut")
    return Setup(actor, item, source, target, incompatible, supplier)
