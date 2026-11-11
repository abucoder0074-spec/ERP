from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.utils import timezone

from apps.masterdata.models import Item, ItemCategory, Location, Supplier, UnitOfMeasure, Warehouse
from apps.procurement.models import ApprovalRule, GoodsReceipt, PurchaseOrder, PurchaseRequest
from apps.procurement.services.approvals import decide
from apps.procurement.services.orders import OrderData, OrderLine, create_order
from apps.procurement.services.receiving import Delivery, DeliveryLine, post_receipt
from apps.procurement.services.requests import (
    RequestData,
    RequestLine,
    create_request,
    submit_request,
)


@dataclass
class Setup:
    actor: User
    approver: User
    item: Item
    location: Location
    supplier: Supplier
    other: Supplier
    today: date

    def request(self, value: str = "100", *, approved: bool = False) -> PurchaseRequest:
        request = create_request(
            self.actor,
            uuid4(),
            RequestData(
                "Ta’minot",
                self.today,
                "normal",
                "Sut",
                (RequestLine(self.item.pk, Decimal(10), Decimal(value)),),
            ),
        )
        submit_request(self.actor, request.pk)
        if approved:
            decide(self.approver, request.approval.steps.get().pk, approve=True)
            request.refresh_from_db()
        return request

    def order_data(self, quantity: str = "10", price: str = "100") -> OrderData:
        return OrderData(
            self.supplier.pk,
            "UZS",
            Decimal(1),
            self.today,
            self.today,
            (OrderLine(self.item.pk, Decimal(quantity), Decimal(price)),),
        )

    def order(self, *, approved: bool = True) -> PurchaseOrder:
        order = create_order(self.actor, uuid4(), self.order_data())
        if approved:
            decide(self.approver, order.versions.get().approval.steps.get().pk, approve=True)
            order.refresh_from_db()
        return order

    def receipt(self, order: PurchaseOrder, quantity: str, batch: str = "LOT") -> GoodsReceipt:
        return post_receipt(
            self.actor,
            uuid4(),
            order.pk,
            Delivery(
                self.today,
                "",
                (
                    DeliveryLine(
                        self.item.pk,
                        self.location.pk,
                        Decimal(quantity),
                        batch,
                        self.today + timedelta(days=4),
                    ),
                ),
            ),
        )


@pytest.fixture
def setup() -> Setup:
    actor = User.objects.create(username="phase2-actor", is_superuser=True)
    approver = User.objects.create(username="phase2-approver", is_superuser=True)
    group, _ = Group.objects.get_or_create(name="Procurement Manager")
    ApprovalRule.objects.get_or_create(group=group, level=1, minimum=0, maximum=None, category=None)
    warehouse = Warehouse.objects.create(code="T", name="Toshkent")
    location = Location.objects.create(
        code="C", name="Sovitkich", storage="chilled", warehouse=warehouse
    )
    item = Item.objects.create(
        code="SUT",
        name_uz="Sut",
        storage="chilled",
        category=ItemCategory.objects.create(code="XA", name="Xomashyo", kind="raw"),
        base_unit=UnitOfMeasure.objects.create(code="KG", name="Kilogramm"),
    )
    supplier = Supplier.objects.create(code="S1", name="Chirchiq")
    other = Supplier.objects.create(code="S2", name="Yangiyo‘l")
    return Setup(actor, approver, item, location, supplier, other, timezone.localdate())
