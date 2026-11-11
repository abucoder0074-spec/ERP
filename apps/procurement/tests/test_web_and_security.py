from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pypdf import PdfWriter
from pytest_django.fixtures import SettingsWrapper

from apps.inventory.models import AuditLog
from apps.procurement.models import PurchaseRequest, State
from apps.procurement.services.attachments import attach, read_attachment, validate_attachment
from apps.procurement.services.common import TRANSITIONS, check_transition
from apps.procurement.services.invoices import InvoiceData, InvoiceLine, record_invoice
from apps.procurement.services.quotes import open_rfq
from apps.procurement.tests.conftest import Setup
from apps.procurement.tests.test_receiving_invoice import photo

pytestmark = pytest.mark.django_db

ROLES = (
    "Admin",
    "Procurement Manager",
    "Procurement Specialist",
    "Warehouse Keeper",
    "Accountant",
    "Production Planner",
    "Viewer",
)


def test_states_exhaustive() -> None:
    expected = {
        "request": {
            "draft>submitted",
            "submitted>approved",
            "submitted>rejected",
            "approved>converted",
        },
        "rfq": {"open>selected", "open>cancelled"},
        "order": {
            "submitted>active",
            "submitted>rejected",
            "submitted>cancelled",
            "active>cancelled",
            "rejected>cancelled",
            "rejected>submitted",
        },
        "version": {
            "submitted>active",
            "submitted>rejected",
            "submitted>cancelled",
            "active>superseded",
        },
        "step": {"submitted>approved", "submitted>rejected"},
        "invoice": {"mismatch>ready", "mismatch>cancelled", "ready>cancelled"},
    }
    assert expected.keys() == TRANSITIONS.keys()
    for kind, allowed in expected.items():
        for before in State.values:
            for after in State.values:
                if f"{before}>{after}" in allowed:
                    check_transition(kind, before, after)
                else:
                    with pytest.raises(ValidationError):
                        check_transition(kind, before, after)


def test_attachment_content_storage_and_authorization(
    client: Client, setup: Setup, settings: SettingsWrapper
) -> None:
    purchase = setup.request()
    good = photo()
    for name, content in (
        ("../scan.png", good),
        (r"C:\scan.png", good),
        ("scan.exe", good),
        ("scan.pdf", good),
        ("scan.jpg", good),
        ("scan.png", b"invalid"),
        ("empty.png", b""),
    ):
        with pytest.raises(ValidationError):
            validate_attachment(name, content)
    settings.PROCUREMENT_ATTACHMENT_LIMIT = len(good) - 1
    with pytest.raises(ValidationError):
        validate_attachment("scan.png", good)
    settings.PROCUREMENT_ATTACHMENT_LIMIT = 2097152
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = BytesIO()
    writer.write(stream)
    assert validate_attachment("waybill.pdf", stream.getvalue()) == "application/pdf"
    key = uuid4()
    settings.PROCUREMENT_SCAN_HOOK = "apps.procurement.tests.test_web_and_security.reject_scan"
    with pytest.raises(ValidationError):
        attach(setup.actor, key, "request", purchase.pk, "scan.png", good)
    settings.PROCUREMENT_SCAN_HOOK = "apps.procurement.services.attachments.no_op_scan"
    attachment = attach(setup.actor, key, "request", purchase.pk, "scan.png", good)
    assert attach(setup.actor, key, "request", purchase.pk, "scan.png", good) == attachment
    roleless = User.objects.create(username="no-access")
    with pytest.raises(PermissionDenied):
        read_attachment(roleless, attachment.pk)
    with pytest.raises(PermissionDenied):
        attach(roleless, uuid4(), "request", purchase.pk, "scan.png", good)
    client.force_login(roleless)
    url = reverse("procurement_download", args=[attachment.pk])
    assert client.get(url).status_code == 403
    client.force_login(setup.actor)
    response = client.get(url)
    assert response.content == good
    assert str(attachment.storage_name) in response["Content-Disposition"]
    assert "scan.png" not in response["Content-Disposition"]
    assert AuditLog.objects.filter(action="attachment_read").exists()
    with pytest.raises(IntegrityError), transaction.atomic():
        PurchaseRequest.objects.filter(pk=purchase.pk).update(justification="tamper")


def reject_scan(content: bytes, media_type: str) -> bool:
    return False


@pytest.mark.parametrize("role", ROLES)
def test_role_actions(client: Client, setup: Setup, role: str) -> None:
    member = User.objects.create(username="role")
    member.groups.add(Group.objects.get(name=role))
    client.force_login(member)
    order = setup.order()
    purchase = setup.request()
    receipt = setup.receipt(order, "1")
    approved = setup.request(approved=True)
    rfq = open_rfq(setup.actor, uuid4(), approved.pk, (setup.supplier.pk, setup.other.pk))
    line_id = receipt.lines.get().pk
    invoice = record_invoice(
        setup.actor,
        uuid4(),
        order.pk,
        InvoiceData(
            "ROLE-1",
            setup.today,
            "UZS",
            Decimal(1),
            setup.today,
            (InvoiceLine(line_id, Decimal(1), Decimal(105)),),
        ),
    )
    attachment = attach(setup.actor, uuid4(), "request", purchase.pk, "proof.png", photo())
    maker = role in {"Admin", "Procurement Manager", "Procurement Specialist"}
    canceller = role in {"Admin", "Procurement Manager"}
    receiver = role in {"Admin", "Procurement Manager", "Warehouse Keeper"}
    finance = role in {"Admin", "Accountant"}
    for kind, pk in (
        ("requests", purchase.pk),
        ("rfqs", rfq.pk),
        ("orders", order.pk),
        ("receipts", receipt.pk),
        ("invoices", invoice.pk),
    ):
        assert client.get(reverse("procurement_list", args=[kind])).status_code == 200
        assert client.get(reverse("procurement_detail", args=[kind, pk])).status_code == 200
    assert client.get(reverse("procurement_inbox")).status_code == 200
    assert client.get(reverse("procurement_deliveries")).status_code == 200
    assert (
        client.get(reverse("procurement_version", args=[order.active_version_id])).status_code
        == 200
    )
    assert client.get(reverse("procurement_download", args=[attachment.pk])).status_code == 200
    action_checks = (
        ("submit", purchase.pk, maker),
        ("replenish", 1, maker),
        ("rfq", purchase.pk, maker),
        ("quote", rfq.pk, maker),
        ("select", rfq.pk, maker),
        ("cancel-rfq", rfq.pk, canceller),
        ("convert", approved.pk, maker),
        ("amend", order.pk, maker),
        ("cancel-order", order.pk, canceller),
        ("receive", order.pk, receiver),
        ("return", line_id, receiver),
        ("invoice", order.pk, finance),
        ("override", invoice.pk, finance),
        ("cancel-invoice", invoice.pk, finance),
    )
    checks = [
        (reverse("procurement_new", args=["request"]), maker),
        (reverse("procurement_new", args=["order"]), maker),
        (
            reverse("procurement_decision", args=[purchase.approval.steps.get().pk]),
            role == "Procurement Manager",
        ),
        (reverse("procurement_policy", args=["rules"]), role == "Admin"),
        (reverse("procurement_policy", args=["delegations"]), role == "Admin"),
        (reverse("procurement_upload", args=["requests", purchase.pk]), maker),
        (reverse("procurement_upload", args=["orders", order.pk]), maker),
        (reverse("procurement_upload", args=["receipts", receipt.pk]), receiver),
        (reverse("procurement_upload", args=["invoices", invoice.pk]), finance),
    ]
    checks.extend(
        (reverse("procurement_action", args=[action, pk]), allowed)
        for action, pk, allowed in action_checks
    )
    for url, allowed in checks:
        assert client.get(url).status_code == (200 if allowed else 403), url


def test_http_receiving_and_invoice(client: Client, setup: Setup) -> None:
    client.force_login(setup.actor)
    order = setup.order()
    data = {
        "key": uuid4(),
        "delivered_on": setup.today.isoformat(),
        "delay_reason": "",
        "lines-TOTAL_FORMS": 1,
        "lines-INITIAL_FORMS": 0,
        "lines-0-item": setup.item.code,
        "lines-0-location": setup.location.pk,
        "lines-0-quantity": "2",
        "lines-0-batch": "HTTP",
        "photo": SimpleUploadedFile("proof.png", photo()),
    }
    response = client.post(reverse("procurement_action", args=["receive", order.pk]), data)
    assert response.status_code == 302
    receipt = order.receipts.get()
    line = receipt.lines.get()
    response = client.post(
        reverse("procurement_action", args=["invoice", order.pk]),
        {
            "key": uuid4(),
            "number": "HTTP-1",
            "invoice_date": setup.today.isoformat(),
            "currency": "UZS",
            "rate": "1",
            "rate_date": setup.today.isoformat(),
            "lines-TOTAL_FORMS": 1,
            "lines-INITIAL_FORMS": 0,
            "lines-0-receipt_line": line.pk,
            "lines-0-quantity": "2",
            "lines-0-price": "109",
        },
    )
    assert response.status_code == 302
    invoice = order.invoices.get()
    assert invoice.state == State.MISMATCH
    assert (
        client.get(reverse("procurement_detail", args=["invoices", invoice.pk])).status_code == 200
    )


def test_list_queries_do_not_grow_with_rows(client: Client, setup: Setup) -> None:
    manager = User.objects.create(username="procurement-list-manager")
    manager.groups.add(Group.objects.get(name="Procurement Manager"))
    client.force_login(manager)
    urls = [
        reverse("procurement_list", args=[kind])
        for kind in ("requests", "rfqs", "orders", "receipts", "invoices")
    ] + [reverse("procurement_inbox"), reverse("procurement_deliveries")]
    snapshots: list[list[int]] = []
    for index in range(3):
        purchase = setup.request(approved=True)
        open_rfq(setup.actor, uuid4(), purchase.pk, (setup.supplier.pk, setup.other.pk))
        setup.request()
        order = setup.order()
        receipt = setup.receipt(order, "1", batch=f"LIST-{index}")
        record_invoice(
            setup.actor,
            uuid4(),
            order.pk,
            InvoiceData(
                f"LIST-{index}",
                setup.today,
                "UZS",
                Decimal(1),
                setup.today,
                (InvoiceLine(receipt.lines.get().pk, Decimal(1), Decimal(100)),),
            ),
        )
        counts = []
        for url in urls:
            with CaptureQueriesContext(connection) as captured:
                assert client.get(url).status_code == 200
            counts.append(len(captured))
        snapshots.append(counts)
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert snapshots[0][2] == 6


@pytest.mark.django_db(transaction=True)
def test_phase2_migration_round_trip() -> None:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("procurement", None)])
        assert "procurement_purchaseorder" not in connection.introspection.table_names()
        MigrationExecutor(connection).migrate(latest)
        assert "procurement_purchaseorder" in connection.introspection.table_names()
        assert (
            Group.objects.get(name="Accountant")
            .permissions.filter(codename="override_match")
            .exists()
        )
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_http_create_submit_and_approval_policy(client: Client, setup: Setup) -> None:
    client.force_login(setup.actor)
    policy = client.post(
        reverse("procurement_policy_edit", args=["rules", 0]),
        {
            "category": "",
            "minimum": 30000,
            "maximum": 50000,
            "level": 2,
            "group": Group.objects.get(name="Admin").pk,
            "active": "on",
        },
    )
    assert policy.status_code == 302
    assert AuditLog.objects.filter(action="approval_policy").exists()
    created = client.post(
        reverse("procurement_new", args=["request"]),
        {
            "key": uuid4(),
            "department": "Ta’minot",
            "needed_by": setup.today.isoformat(),
            "priority": "normal",
            "justification": "Sut",
            "lines-TOTAL_FORMS": 1,
            "lines-INITIAL_FORMS": 0,
            "lines-0-item": setup.item.pk,
            "lines-0-quantity": "2",
            "lines-0-estimated_cost": "200",
        },
    )
    assert created.status_code == 302
    record = PurchaseRequest.objects.get()
    assert client.post(reverse("procurement_action", args=["submit", record.pk])).status_code == 302
    step = record.approval.steps.get()
    assert client.get(reverse("procurement_decision", args=[step.pk])).status_code == 403
    client.force_login(setup.approver)
    assert client.get(reverse("procurement_inbox")).status_code == 200
    assert (
        client.post(reverse("procurement_decision", args=[step.pk]), {"approve": "yes"}).status_code
        == 302
    )
    record.refresh_from_db()
    assert record.state == State.APPROVED
