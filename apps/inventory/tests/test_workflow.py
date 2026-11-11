from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.inventory.models import Batch, Quality, StockBalance, Stocktake, StocktakeStatus
from apps.inventory.tests.conftest import Setup

pytestmark = pytest.mark.django_db


def test_opening_import_quality_and_stocktake_through_workspace(
    client: Client, stock: Setup
) -> None:
    manager = User.objects.create(username="manager")
    manager.groups.add(Group.objects.get(name="Procurement Manager"))
    keeper = User.objects.create(username="keeper")
    keeper.groups.add(Group.objects.get(name="Warehouse Keeper"))
    content = (
        b"item,location,supplier,batch_number,quantity,unit_cost,reference\n"
        b"SUT,A-1,SUP,LOT,5.000,12.00,R1\n"
    )
    import_url = reverse("stock_import", args=["opening"])
    key = uuid4()
    client.force_login(manager)
    for action in ("preview", "commit"):
        response = client.post(
            import_url,
            {
                "key": key,
                "action": action,
                "file": SimpleUploadedFile("opening.csv", content),
            },
        )
        assert response.status_code == 200
        assert response.context["result"].committed == (action == "commit")
        assert not response.context["result"].errors
        assert Batch.objects.exists() == (action == "commit")
    lot = Batch.objects.get(number="LOT")
    quality_url = reverse("batch_quality", args=[lot.pk])
    assert client.post(quality_url, {"quality": Quality.APPROVED}).status_code == 302
    retry = client.post(
        import_url,
        {
            "key": key,
            "action": "commit",
            "file": SimpleUploadedFile("opening.csv", content),
        },
    )
    assert retry.context["result"].committed
    assert StockBalance.objects.get().quantity == Decimal(5)
    new_key = client.post(
        import_url,
        {
            "key": uuid4(),
            "action": "preview",
            "file": SimpleUploadedFile("opening.csv", content),
        },
    )
    assert new_key.context["result"].errors[0].row == 2
    client.force_login(keeper)
    assert (
        client.post(
            reverse("stocktake_new"),
            {
                "key": uuid4(),
                "location": stock.source.pk,
            },
        ).status_code
        == 302
    )
    sheet = Stocktake.objects.get()
    detail = reverse("stocktake_detail", args=[sheet.pk])
    assert b"data-scan-field" in client.get(detail).content
    assert (
        client.post(detail, {"action": "count", "batch": "SUT:LOT", "counted": "4"}).status_code
        == 302
    )
    assert client.post(detail, {"action": "submitted"}).status_code == 302
    client.force_login(manager)
    for action in ("approved", "post", "post"):
        assert client.post(detail, {"action": action}).status_code == 302
    sheet.refresh_from_db()
    assert sheet.status == StocktakeStatus.POSTED
    assert sheet.operation is not None and sheet.operation.movements.count() == 1
    assert StockBalance.objects.get().quantity == Decimal(4)
