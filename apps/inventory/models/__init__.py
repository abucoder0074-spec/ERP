from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.masterdata.models import Item, Location, Supplier, Warehouse


class Quality(models.TextChoices):
    QUARANTINE = "quarantine", _("Quarantine")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")


class MovementType(models.TextChoices):
    RECEIPT = "receipt", _("Receipt")
    ISSUE = "issue", _("Issue")
    TRANSFER_OUT = "transfer_out", _("Transfer out")
    TRANSFER_IN = "transfer_in", _("Transfer in")
    WRITE_OFF = "write_off", _("Write-off")
    STOCKTAKE = "stocktake_adjustment", _("Stocktake adjustment")
    RETURN = "return_to_supplier", _("Supplier return")
    REVERSAL = "reversal", _("Reversal")


class Batch(models.Model):
    item = models.ForeignKey(Item, models.PROTECT)
    number = models.CharField(max_length=80)
    expiry = models.DateField(null=True, blank=True)
    supplier = models.ForeignKey(Supplier, models.PROTECT)
    receipt_reference = models.CharField(max_length=120)
    quality = models.CharField(max_length=10, choices=Quality, default=Quality.QUARANTINE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("item", "number"), name="unique_item_batch")]
        indexes = [models.Index(fields=("item", "quality", "expiry"))]

    def __str__(self) -> str:
        return f"{self.item_id} · {self.number}"


class StockOperation(models.Model):
    idempotency_key = models.UUIDField(unique=True)
    kind = models.CharField(max_length=24, choices=MovementType)
    fingerprint = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    reverses = models.OneToOneField("self", models.PROTECT, null=True, related_name="reversed_by")

    class Meta:
        permissions = [
            ("receive_stock", "Can receive stock"),
            ("issue_stock", "Can issue stock"),
            ("transfer_stock", "Can transfer stock"),
            ("write_off_stock", "Can write off stock"),
            ("large_write_off", "Can write off stock above threshold"),
            ("return_stock", "Can return stock to supplier"),
            ("override_fefo", "Can override FEFO"),
            ("reverse_stock", "Can reverse stock postings"),
            ("import_opening", "Can import opening balances"),
        ]


class StockMovement(models.Model):
    operation = models.ForeignKey(StockOperation, models.PROTECT, related_name="movements")
    ordinal = models.PositiveIntegerField()
    idempotency_key = models.UUIDField(default=uuid4, unique=True)
    kind = models.CharField(max_length=24, choices=MovementType)
    batch = models.ForeignKey(Batch, models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, models.PROTECT)
    location = models.ForeignKey(Location, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=15, decimal_places=2)
    value_delta = models.DecimalField(max_digits=30, decimal_places=5)
    reason = models.CharField(max_length=240, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("operation", "ordinal"), name="movement_operation_ordinal"
            ),
            models.CheckConstraint(condition=~Q(quantity=0), name="movement_nonzero_quantity"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="movement_nonnegative_cost"),
        ]
        ordering = ("-pk",)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError(_("Stock movements are immutable."))
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Stock movements are immutable."))


class StockBalance(models.Model):
    batch = models.ForeignKey(Batch, models.PROTECT)
    location = models.ForeignKey(Location, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("batch", "location"), name="balance_batch_location"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="balance_nonnegative"),
        ]


class WarehouseCost(models.Model):
    item = models.ForeignKey(Item, models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    carrying_value = models.DecimalField(max_digits=30, decimal_places=5, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("item", "warehouse"), name="cost_item_warehouse"),
            models.CheckConstraint(
                condition=Q(quantity__gte=0, carrying_value__gte=0)
                & (Q(quantity__gt=0) | Q(carrying_value=0)),
                name="cost_nonnegative_no_residual",
            ),
        ]


class StocktakeStatus(models.TextChoices):
    COUNTING = "counting", _("Counting")
    SUBMITTED = "submitted", _("Submitted")
    APPROVED = "approved", _("Approved")
    POSTED = "posted", _("Posted")
    CANCELLED = "cancelled", _("Cancelled")


class Stocktake(models.Model):
    location = models.ForeignKey(Location, models.PROTECT)
    counter = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT, related_name="+")
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.PROTECT, null=True, related_name="+"
    )
    watermark = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=10, choices=StocktakeStatus, default=StocktakeStatus.COUNTING
    )
    operation = models.OneToOneField(StockOperation, models.PROTECT, null=True)
    key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("count_stocktake", "Can count stocktakes"),
            ("approve_stocktake", "Can approve and post stocktakes"),
        ]


class StocktakeLine(models.Model):
    sheet = models.ForeignKey(Stocktake, models.PROTECT, related_name="lines")
    batch = models.ForeignKey(Batch, models.PROTECT)
    expected = models.DecimalField(max_digits=15, decimal_places=3)
    counted = models.DecimalField(max_digits=15, decimal_places=3, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("sheet", "batch"), name="stocktake_unique_batch"),
            models.CheckConstraint(
                condition=Q(counted__gte=0) | Q(counted__isnull=True), name="stocktake_nonnegative"
            ),
        ]

    @property
    def variance(self) -> Decimal | None:
        return self.counted - self.expected if self.counted is not None else None


class AlertKind(models.TextChoices):
    LOW = "low", _("Below minimum")
    HIGH = "high", _("Above maximum")
    EXPIRING = "expiring", _("Expiring soon")
    EXPIRED = "expired", _("Expired stock")


class Notification(models.Model):
    kind = models.CharField(max_length=10, choices=AlertKind)
    condition = models.CharField(max_length=90)
    item = models.ForeignKey(Item, models.PROTECT)
    batch = models.ForeignKey(Batch, models.PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("condition",),
                condition=Q(resolved_at__isnull=True),
                name="one_unresolved_stock_alert",
            ),
        ]
        ordering = ("-pk",)


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    action = models.CharField(max_length=40)
    subject = models.CharField(max_length=100)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-pk",)
