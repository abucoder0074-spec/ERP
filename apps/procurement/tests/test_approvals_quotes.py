from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError

from apps.inventory.models import AlertKind, AuditLog, Notification
from apps.procurement.models import ApprovalDelegation, ApprovalRule, State
from apps.procurement.services.approvals import decide
from apps.procurement.services.orders import create_order
from apps.procurement.services.quotes import (
    QuoteData,
    cancel_rfq,
    capture_quote,
    latest_valid_price,
    open_rfq,
    select_quote,
)
from apps.procurement.services.requests import request_from_alert, submit_request
from apps.procurement.tests.conftest import Setup

pytestmark = pytest.mark.django_db


def test_thresholds_delegation_self_approval_and_retries(setup: Setup) -> None:
    category = setup.item.category
    managers = Group.objects.get(name="Procurement Manager")
    admin = Group.objects.get(name="Admin")
    ApprovalRule.objects.create(
        category=category, level=1, minimum=1000, maximum=5000, group=managers
    )
    ApprovalRule.objects.create(category=category, level=2, minimum=1000, maximum=5000, group=admin)
    below = setup.request("99.99", approved=True)
    assert below.approval.steps.count() == 1
    purchase = setup.request()
    first, second = purchase.approval.steps.all()
    assert [first.level, second.level] == [1, 2]
    assert submit_request(setup.actor, purchase.pk) == purchase
    with pytest.raises(PermissionDenied):
        decide(setup.actor, first.pk, approve=True)
    with pytest.raises(ValidationError):
        decide(setup.approver, second.pk, approve=True)
    principal = User.objects.create(username="principal")
    principal.groups.add(managers)
    delegate = User.objects.create(username="delegate")
    delegate.groups.add(Group.objects.get(name="Procurement Specialist"))
    delegation = ApprovalDelegation.objects.create(
        principal=principal,
        delegate=delegate,
        starts_on=setup.today - timedelta(days=2),
        ends_on=setup.today - timedelta(days=1),
    )
    with pytest.raises(PermissionDenied):
        decide(delegate, first.pk, approve=True)
    delegation.ends_on = setup.today
    delegation.save(update_fields=["ends_on"])
    result = decide(delegate, first.pk, approve=True)
    assert result.principal_id == principal.pk
    assert decide(delegate, first.pk, approve=True) == result
    principal.groups.add(admin)
    with pytest.raises(ValidationError):
        decide(principal, second.pk, approve=True)
    with pytest.raises(ValidationError):
        decide(setup.approver, second.pk, approve=False)
    decision = decide(setup.approver, second.pk, approve=False, comment="Narxni tekshiring")
    assert decide(setup.approver, second.pk, approve=False, comment="Narxni tekshiring") == decision
    purchase.refresh_from_db()
    assert purchase.state == State.REJECTED
    assert (
        AuditLog.objects.filter(
            action="approval_decision", subject=f"procurement.approvalstep:{second.pk}"
        ).count()
        == 1
    )
    with pytest.raises(ValidationError):
        submit_request(setup.actor, purchase.pk)


def test_quotes_validity_currency_selection_and_conversion(setup: Setup) -> None:
    purchase = setup.request(approved=True)
    with pytest.raises(ValidationError):
        open_rfq(setup.actor, uuid4(), purchase.pk, (setup.supplier.pk,))
    key = uuid4()
    suppliers = (setup.supplier.pk, setup.other.pk)
    rfq = open_rfq(setup.actor, key, purchase.pk, suppliers)
    assert open_rfq(setup.actor, key, purchase.pk, suppliers) == rfq
    line = purchase.lines.get()
    data = QuoteData(
        setup.supplier.pk,
        "UZS",
        Decimal(1),
        setup.today,
        2,
        setup.today,
        setup.today + timedelta(days=1),
        "Naqd",
        {line.pk: Decimal(100)},
    )
    with pytest.raises(ValidationError):
        capture_quote(setup.actor, uuid4(), rfq.pk, replace(data, prices={}))
    quote_key = uuid4()
    cheap = capture_quote(setup.actor, quote_key, rfq.pk, data)
    assert capture_quote(setup.actor, quote_key, rfq.pk, data) == cheap
    usd = capture_quote(
        setup.actor,
        uuid4(),
        rfq.pk,
        replace(
            data,
            supplier_id=setup.other.pk,
            currency="USD",
            rate=Decimal(12),
            prices={line.pk: Decimal(10)},
        ),
    )
    assert usd.total_uzs == Decimal(1200)
    historic = latest_valid_price(setup.other.pk, setup.item.pk, "USD", setup.today)
    assert historic and historic.price == Decimal(10)
    assert latest_valid_price(setup.other.pk, setup.item.pk, "UZS", setup.today) is None
    assert (
        latest_valid_price(setup.supplier.pk, setup.item.pk, "UZS", setup.today + timedelta(days=2))
        is None
    )
    with pytest.raises(ValidationError):
        select_quote(setup.actor, rfq.pk, usd.pk)
    result = select_quote(setup.actor, rfq.pk, usd.pk, "Tezroq")
    assert select_quote(setup.actor, rfq.pk, usd.pk, "Tezroq") == result
    with pytest.raises(ValidationError):
        cancel_rfq(setup.actor, rfq.pk, "Bekor")
    with pytest.raises(ValidationError):
        create_order(
            setup.actor, uuid4(), setup.order_data(), request_id=purchase.pk, quote_id=usd.pk
        )
    data_po = replace(
        setup.order_data(price="10"), supplier_id=setup.other.pk, currency="USD", rate=Decimal(12)
    )
    order = create_order(setup.actor, key, data_po, request_id=purchase.pk, quote_id=usd.pk)
    assert order.versions.get().total_uzs == Decimal(1344)
    purchase.refresh_from_db()
    assert purchase.state == State.CONVERTED
    assert create_order(setup.actor, key, data_po, request_id=purchase.pk, quote_id=usd.pk) == order


def test_alert_replenishment_and_cancelled_rfq(setup: Setup) -> None:
    setup.item.reorder_qty = Decimal(10)
    setup.item.save()
    alert = Notification.objects.create(kind=AlertKind.LOW, condition="low:test", item=setup.item)
    with pytest.raises(ValidationError):
        request_from_alert(setup.actor, alert.pk, uuid4())
    order = setup.order()
    setup.receipt(order, "2")
    purchase = request_from_alert(setup.actor, alert.pk, uuid4())
    assert request_from_alert(setup.actor, alert.pk, uuid4()) == purchase
    assert purchase.lines.get().estimated_unit_cost == Decimal(100)
    other = setup.request(approved=True)
    rfq = open_rfq(setup.actor, uuid4(), other.pk, (setup.supplier.pk, setup.other.pk))
    assert cancel_rfq(setup.actor, rfq.pk, "Talab yo‘q").state == State.CANCELLED
    assert cancel_rfq(setup.actor, rfq.pk, "Talab yo‘q").state == State.CANCELLED
