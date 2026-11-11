from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.inventory.models import Batch, Notification, StockOperation
from apps.masterdata.models import Item, ItemCategory, Location, Supplier

USER = settings.AUTH_USER_MODEL


class Currency(models.TextChoices):
    UZS = "UZS", "UZS"
    USD = "USD", "USD"


class State(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    CONVERTED = "converted", _("Converted")
    OPEN = "open", _("Open")
    SELECTED = "selected", _("Selected")
    ACTIVE = "active", _("Active")
    SUPERSEDED = "superseded", _("Superseded")
    CANCELLED = "cancelled", _("Cancelled")
    MISMATCH = "mismatch", _("Mismatch")
    READY = "ready", _("Ready for payment")


class Document(models.Model):
    key = models.UUIDField(unique=True)
    fingerprint = models.CharField(max_length=64)
    created_by = models.ForeignKey(USER, models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        abstract = True
        ordering = ("-pk",)


class PurchaseRequest(Document):
    department = models.CharField(max_length=100)
    needed_by = models.DateField()
    priority = models.CharField(
        max_length=12, choices=[("normal", _("Normal")), ("urgent", _("Urgent"))]
    )
    justification = models.CharField(max_length=500)
    state = models.CharField(max_length=12, choices=State, default=State.DRAFT)
    alert = models.OneToOneField(Notification, models.PROTECT, null=True, blank=True)

    def __str__(self) -> str:
        return f"PR-{self.pk}"


class PurchaseRequestLine(models.Model):
    request = models.ForeignKey(PurchaseRequest, models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    estimated_unit_cost = models.DecimalField(max_digits=15, decimal_places=2)
    preferred_supplier = models.ForeignKey(Supplier, models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("request", "item"), name="request_item_once"),
            models.CheckConstraint(
                condition=Q(quantity__gt=0, estimated_unit_cost__gt=0), name="request_positive_line"
            ),
        ]


class ApprovalRule(models.Model):
    category = models.ForeignKey(ItemCategory, models.PROTECT, null=True, blank=True)
    minimum = models.DecimalField(max_digits=25, decimal_places=2)
    maximum = models.DecimalField(max_digits=25, decimal_places=2, null=True, blank=True)
    level = models.PositiveSmallIntegerField()
    group = models.ForeignKey(Group, models.PROTECT)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(level__gt=0, minimum__gte=0)
                & (Q(maximum__isnull=True) | Q(maximum__gt=F("minimum"))),
                name="approval_rule_range",
            ),
        ]


class ApprovalDelegation(models.Model):
    principal = models.ForeignKey(USER, models.PROTECT, related_name="+")
    delegate = models.ForeignKey(USER, models.PROTECT, related_name="+")
    starts_on = models.DateField()
    ends_on = models.DateField()
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(principal=F("delegate")) & Q(ends_on__gte=F("starts_on")),
                name="delegation_valid",
            ),
            models.UniqueConstraint(
                fields=("principal", "delegate", "starts_on", "ends_on"),
                name="delegation_window_once",
            ),
        ]


class RFQ(Document):
    request = models.OneToOneField(PurchaseRequest, models.PROTECT, related_name="rfq")
    state = models.CharField(max_length=12, choices=State, default=State.OPEN)
    selected = models.OneToOneField("Quote", models.PROTECT, null=True, related_name="+")
    selection_reason = models.CharField(max_length=300, blank=True)


class RFQSupplier(models.Model):
    rfq = models.ForeignKey(RFQ, models.PROTECT, related_name="invitations")
    supplier = models.ForeignKey(Supplier, models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("rfq", "supplier"), name="rfq_supplier_once")
        ]


class CurrencyDocument(Document):
    currency = models.CharField(max_length=3, choices=Currency)
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6)
    rate_date = models.DateField()

    class Meta:
        abstract = True
        ordering = ("-pk",)


class Quote(CurrencyDocument):
    rfq = models.ForeignKey(RFQ, models.PROTECT, related_name="quotes")
    supplier = models.ForeignKey(Supplier, models.PROTECT)
    lead_days = models.PositiveIntegerField()
    valid_from = models.DateField()
    valid_until = models.DateField()
    payment_terms = models.CharField(max_length=240)
    total = models.DecimalField(max_digits=25, decimal_places=2)
    total_uzs = models.DecimalField(max_digits=25, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("rfq", "supplier"), name="quote_supplier_once"),
            models.CheckConstraint(
                condition=Q(valid_until__gte=F("valid_from"), exchange_rate__gt=0),
                name="quote_valid_terms",
            ),
        ]


class QuoteLine(models.Model):
    quote = models.ForeignKey(Quote, models.PROTECT, related_name="lines")
    request_line = models.ForeignKey(PurchaseRequestLine, models.PROTECT)
    price = models.DecimalField(max_digits=15, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("quote", "request_line"), name="quote_line_once"),
            models.CheckConstraint(condition=Q(price__gt=0), name="quote_positive_price"),
        ]


class SupplierItemPrice(models.Model):
    supplier = models.ForeignKey(Supplier, models.PROTECT)
    item = models.ForeignKey(Item, models.PROTECT)
    quote_line = models.OneToOneField(QuoteLine, models.PROTECT)
    currency = models.CharField(max_length=3, choices=Currency)
    price = models.DecimalField(max_digits=15, decimal_places=2)
    valid_from = models.DateField()
    valid_until = models.DateField()

    class Meta:
        indexes = [models.Index(fields=("supplier", "item", "currency", "-valid_from"))]
        constraints = [
            models.CheckConstraint(
                condition=Q(valid_until__gte=F("valid_from"), price__gt=0),
                name="supplier_price_valid",
            )
        ]


class PurchaseOrder(Document):
    request = models.OneToOneField(PurchaseRequest, models.PROTECT, null=True, related_name="order")
    quote = models.OneToOneField(Quote, models.PROTECT, null=True)
    supplier = models.ForeignKey(Supplier, models.PROTECT)
    active_version = models.OneToOneField(
        "PurchaseOrderVersion", models.PROTECT, null=True, related_name="+"
    )
    state = models.CharField(max_length=12, choices=State, default=State.SUBMITTED)
    cancellation_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("-pk",)
        permissions = [
            ("approve_procurement", "Can decide procurement approval steps"),
            ("act_as_delegate", "Can act as a procurement approval delegate"),
            ("cancel_order", "Can cancel procurement orders"),
            ("receive_unplanned", "Can receive unplanned items"),
            ("override_match", "Can override an invoice mismatch"),
        ]

    def __str__(self) -> str:
        return f"PO-{self.pk}"


class PurchaseOrderVersion(CurrencyDocument):
    order = models.ForeignKey(PurchaseOrder, models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    state = models.CharField(max_length=12, choices=State, default=State.SUBMITTED)
    expected_on = models.DateField(db_index=True)
    reason = models.CharField(max_length=300, blank=True)
    subtotal = models.DecimalField(max_digits=25, decimal_places=2)
    tax = models.DecimalField(max_digits=25, decimal_places=2)
    total_uzs = models.DecimalField(max_digits=25, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("order", "number"), name="order_version_number"),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(state=State.SUBMITTED),
                name="one_pending_order_version",
            ),
            models.CheckConstraint(
                condition=Q(number__gt=0, exchange_rate__gt=0), name="valid_order_version"
            ),
        ]


class PurchaseOrderLine(models.Model):
    version = models.ForeignKey(PurchaseOrderVersion, models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    price = models.DecimalField(max_digits=15, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("version", "item"), name="order_item_once"),
            models.CheckConstraint(
                condition=Q(quantity__gt=0, price__gt=0, vat_rate__gte=0, vat_rate__lte=100),
                name="valid_order_line",
            ),
        ]


class ApprovalCase(models.Model):
    request = models.OneToOneField(
        PurchaseRequest, models.PROTECT, null=True, related_name="approval"
    )
    version = models.OneToOneField(
        PurchaseOrderVersion, models.PROTECT, null=True, related_name="approval"
    )
    requester = models.ForeignKey(USER, models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=25, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(request__isnull=False, version__isnull=True)
                | Q(request__isnull=True, version__isnull=False),
                name="one_approval_document",
            )
        ]


class ApprovalStep(models.Model):
    case = models.ForeignKey(ApprovalCase, models.PROTECT, related_name="steps")
    sequence = models.PositiveIntegerField()
    level = models.PositiveSmallIntegerField()
    group = models.ForeignKey(Group, models.PROTECT)
    state = models.CharField(max_length=12, choices=State, default=State.SUBMITTED)
    actor = models.ForeignKey(USER, models.PROTECT, null=True, related_name="+")
    principal = models.ForeignKey(USER, models.PROTECT, null=True, related_name="+")
    comment = models.CharField(max_length=300, blank=True)
    decided_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ("sequence",)
        constraints = [
            models.UniqueConstraint(fields=("case", "sequence"), name="approval_sequence_once"),
            models.UniqueConstraint(
                fields=("case", "actor"),
                condition=Q(actor__isnull=False),
                name="approval_actor_once",
            ),
            models.UniqueConstraint(
                fields=("case", "principal"),
                condition=Q(principal__isnull=False),
                name="approval_principal_once",
            ),
        ]


class GoodsReceipt(Document):
    order = models.ForeignKey(PurchaseOrder, models.PROTECT, related_name="receipts")
    version = models.ForeignKey(PurchaseOrderVersion, models.PROTECT)
    delivered_on = models.DateField(db_index=True)
    delay_reason = models.CharField(max_length=300, blank=True)

    def __str__(self) -> str:
        return f"GR-{self.pk}"


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, models.PROTECT, related_name="lines")
    order_line = models.ForeignKey(PurchaseOrderLine, models.PROTECT, null=True)
    item = models.ForeignKey(Item, models.PROTECT)
    batch = models.ForeignKey(Batch, models.PROTECT)
    location = models.ForeignKey(Location, models.PROTECT)
    operation = models.OneToOneField(StockOperation, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    price = models.DecimalField(max_digits=15, decimal_places=2)
    supplier_batch = models.CharField(max_length=80)
    unplanned_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0, price__gt=0), name="valid_goods_receipt_line"
            )
        ]

    def __str__(self) -> str:
        return f"{self.receipt} / {self.item} / {self.quantity}"


class SupplierReturn(Document):
    line = models.ForeignKey(GoodsReceiptLine, models.PROTECT, related_name="returns")
    operation = models.OneToOneField(StockOperation, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    reason = models.CharField(max_length=240)
    photo = models.ForeignKey("Attachment", models.PROTECT)


class SupplierInvoice(CurrencyDocument):
    order = models.ForeignKey(PurchaseOrder, models.PROTECT, related_name="invoices")
    supplier = models.ForeignKey(Supplier, models.PROTECT)
    number = models.CharField(max_length=80)
    invoice_date = models.DateField()
    state = models.CharField(max_length=12, choices=State)
    mismatches = models.JSONField(default=list)
    override_reason = models.CharField(max_length=300, blank=True)
    cancellation_reason = models.CharField(max_length=300, blank=True)
    tax = models.DecimalField(max_digits=25, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=25, decimal_places=2)
    total_uzs = models.DecimalField(max_digits=25, decimal_places=2)

    class Meta:
        ordering = ("-pk",)
        constraints = [
            models.UniqueConstraint(fields=("supplier", "number"), name="supplier_invoice_number"),
            models.CheckConstraint(condition=Q(exchange_rate__gt=0), name="invoice_positive_rate"),
            models.CheckConstraint(
                condition=~Q(state=State.READY) | Q(mismatches=[]) | ~Q(override_reason=""),
                name="invoice_ready_requires_resolution",
            ),
        ]

    def __str__(self) -> str:
        return self.number


class SupplierInvoiceLine(models.Model):
    invoice = models.ForeignKey(SupplierInvoice, models.PROTECT, related_name="lines")
    receipt_line = models.ForeignKey(GoodsReceiptLine, models.PROTECT, related_name="invoice_lines")
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    price = models.DecimalField(max_digits=15, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("invoice", "receipt_line"), name="invoice_receipt_line_once"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0, price__gt=0), name="valid_invoice_line"
            ),
        ]


class Attachment(Document):
    request = models.ForeignKey(PurchaseRequest, models.PROTECT, null=True)
    order = models.ForeignKey(PurchaseOrder, models.PROTECT, null=True)
    receipt = models.ForeignKey(GoodsReceipt, models.PROTECT, null=True)
    invoice = models.ForeignKey(SupplierInvoice, models.PROTECT, null=True)
    storage_name = models.UUIDField(unique=True)
    mime_type = models.CharField(max_length=30)
    content = models.BinaryField(editable=False)
    size = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    request__isnull=False,
                    order__isnull=True,
                    receipt__isnull=True,
                    invoice__isnull=True,
                )
                | Q(
                    request__isnull=True,
                    order__isnull=False,
                    receipt__isnull=True,
                    invoice__isnull=True,
                )
                | Q(
                    request__isnull=True,
                    order__isnull=True,
                    receipt__isnull=False,
                    invoice__isnull=True,
                )
                | Q(
                    request__isnull=True,
                    order__isnull=True,
                    receipt__isnull=True,
                    invoice__isnull=False,
                ),
                name="one_attachment_document",
            )
        ]


class DeliveryAlert(models.Model):
    order = models.ForeignKey(PurchaseOrder, models.PROTECT, related_name="delivery_alerts")
    version = models.ForeignKey(PurchaseOrderVersion, models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(resolved_at__isnull=True),
                name="one_open_delivery_alert",
            )
        ]
