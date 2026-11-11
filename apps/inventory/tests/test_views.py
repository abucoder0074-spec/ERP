from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.inventory.models import AuditLog, StockBalance
from apps.inventory.services.stocktake import start_stocktake
from apps.inventory.tests.conftest import Setup
from apps.masterdata.models import Supplier

pytestmark = pytest.mark.django_db

ROLES = [
    "Admin",
    "Procurement Manager",
    "Procurement Specialist",
    "Warehouse Keeper",
    "Accountant",
    "Production Planner",
    "Viewer",
]


@pytest.mark.parametrize("role", ROLES)
def test_role_action_matrix(client: Client, stock: Setup, role: str) -> None:
    user = User.objects.create(username="group-member")
    user.groups.add(Group.objects.get(name=role))
    client.force_login(user)
    lot = stock.receive("LOT", approved=False)
    operation = lot.stockmovement_set.get().operation
    sheet = start_stocktake(stock.actor, uuid4(), stock.source.pk)
    supervisor = role in {"Admin", "Procurement Manager"}
    warehouse = supervisor or role == "Warehouse Keeper"
    procurement = supervisor or role == "Procurement Specialist"
    routes = {
        reverse("catalog_index"): True,
        reverse("catalog_list", args=["items"]): True,
        reverse("catalog_detail", args=["suppliers", stock.supplier.pk]): True,
        reverse("catalog_create", args=["suppliers"]): procurement,
        reverse("catalog_edit", args=["items", stock.item.pk]): procurement,
        reverse("catalog_create", args=["locations"]): supervisor,
        reverse("stock_balances"): True,
        reverse("stock_movements"): True,
        reverse("stock_alerts"): True,
        reverse("stocktakes"): True,
        reverse("stocktake_detail", args=[sheet.pk]): True,
        reverse("stock_audit"): supervisor,
        reverse("stocktake_new"): warehouse,
        reverse("stock_import", args=["suppliers"]): procurement,
        reverse("stock_import", args=["opening"]): supervisor,
        reverse("batch_quality", args=[lot.pk]): supervisor,
        reverse("stock_reverse", args=[operation.pk]): supervisor,
    }
    for action in ("receive", "issue", "transfer", "write-off", "return"):
        routes[reverse("stock_post", args=[action])] = warehouse
    for url, allowed in routes.items():
        assert client.get(url).status_code == (200 if allowed else 403), url
    for url in (
        reverse("stocktake_new"),
        reverse("stock_post", args=["receive"]),
        reverse("stock_post", args=["issue"]),
        reverse("stock_post", args=["transfer"]),
        reverse("stock_post", args=["write-off"]),
        reverse("stock_post", args=["return"]),
    ):
        assert client.post(url, {}).status_code == (200 if warehouse else 403), url
    for url in (
        reverse("batch_quality", args=[lot.pk]),
        reverse("stock_reverse", args=[operation.pk]),
    ):
        assert client.post(url, {}).status_code == (200 if supervisor else 403), url
    sheet_url = reverse("stocktake_detail", args=[sheet.pk])
    assert client.post(sheet_url, {"action": "approved"}).status_code == (
        200 if supervisor else 403
    )
    assert client.post(sheet_url, {"action": "post"}).status_code == (200 if supervisor else 403)
    assert (
        client.post(sheet_url, {"action": "count", "batch": "LOT", "counted": 1}).status_code == 403
    )


def test_balance_and_movement_query_counts_stay_constant(client: Client, stock: Setup) -> None:
    viewer = User.objects.create(username="read-only")
    viewer.groups.add(Group.objects.get(name="Viewer"))
    client.force_login(viewer)
    stock.receive("A")
    for _ in range(2):
        with CaptureQueriesContext(connection) as balance:
            assert client.get(reverse("stock_balances")).status_code == 200
        with CaptureQueriesContext(connection) as history:
            assert client.get(reverse("stock_movements")).status_code == 200
        assert len(balance) == 7
        assert len(history) == 6
        for suffix in ("B", "C", "D"):
            if not stock.item.batch_set.filter(number=suffix).exists():
                stock.receive(suffix)


def test_workspace_posts_and_master_soft_delete(client: Client, stock: Setup) -> None:
    client.force_login(stock.actor)
    payload = {
        "key": uuid4(),
        "item": stock.item.code,
        "location": stock.source.pk,
        "supplier": stock.supplier.pk,
        "batch_number": "Q1",
        "quantity": "3",
        "unit_cost": "12.00",
        "reference": "K-1",
    }
    receipt = reverse("stock_post", args=["receive"])
    assert client.post(receipt, payload).status_code == 302
    assert client.post(receipt, payload).status_code == 302
    assert StockBalance.objects.get().quantity == Decimal(3)
    supplier = Supplier.objects.create(code="TEMP", name="Vaqtinchalik", tin="123456789")
    edit = reverse("catalog_edit", args=["suppliers", supplier.pk])
    assert (
        client.post(
            edit, {"code": "TEMP", "name": "Vaqtinchalik", "tin": "123456789", "lead_time_days": 0}
        ).status_code
        == 302
    )
    supplier.refresh_from_db()
    assert not supplier.active
    event = AuditLog.objects.get(
        action="master_change", subject=f"masterdata.supplier:{supplier.pk}"
    )
    assert event.before["active"] and not event.after["active"]
    assert "tin" not in event.after
    assert client.get(reverse("catalog_list", args=["suppliers"]) + "?active=no").status_code == 200


def test_anon_and_roleless_are_denied(client: Client) -> None:
    for name in ("catalog_index", "stock_balances", "stock_audit"):
        assert client.get(reverse(name)).status_code == 302
    client.force_login(User.objects.create(username="no-role"))
    for name in ("catalog_index", "stock_balances", "stock_audit"):
        assert client.get(reverse(name)).status_code == 403
