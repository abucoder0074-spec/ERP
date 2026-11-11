from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import (
    Batch,
    MovementType,
    Quality,
    StockBalance,
    StockMovement,
    StockOperation,
    WarehouseCost,
)
from apps.inventory.services.common import (
    MAX_COST,
    MAX_QUANTITY,
    MONEY,
    QTY,
    VALUE,
    audit,
    begin,
    exact,
    lock_items,
    lock_locations,
    positive_quantity,
    require,
)
from apps.masterdata.models import Item, Location, Storage, Supplier, Warehouse


@dataclass(frozen=True)
class Receipt:
    item_id: int
    location_id: int
    supplier_id: int
    batch_number: str
    quantity: Decimal
    unit_cost: Decimal
    reference: str
    expiry: date | None = None
    unit_id: int | None = None
    initial_quality: str | None = None

    def posting_payload(self) -> dict[str, Any]:
        payload = vars(self).copy()
        if self.initial_quality is None:
            payload.pop("initial_quality")
        return payload


@dataclass(frozen=True)
class Withdrawal:
    item_id: int
    location_id: int
    quantity: Decimal
    unit_id: int | None = None
    batch_id: int | None = None
    reason: str = ""


def _item_and_location(item_id: int, location_id: int) -> tuple[Item, Location]:
    location = lock_locations([location_id]).get(location_id)
    item = lock_items([item_id]).get(item_id)
    if item is None or location is None:
        raise ValidationError(_("Unknown item or location."))
    return item, location


def compatible(item: Item, location: Location) -> None:
    if (
        not item.active
        or not location.active
        or not Warehouse.objects.filter(pk=location.warehouse_id, active=True).exists()
    ):
        raise ValidationError(_("Use an active item, warehouse and location."))
    if item.storage != location.storage:
        raise ValidationError(_("The item and location storage conditions differ."))


def _position(batch: Batch, location: Location) -> tuple[StockBalance, WarehouseCost]:
    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        batch=batch, location=location
    )
    cost, _ = WarehouseCost.objects.select_for_update().get_or_create(
        item_id=batch.item_id, warehouse_id=location.warehouse_id
    )
    return balance, cost


def extended_value(quantity: Decimal, cost: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        return quantity * cost


def proportional_value(cost: WarehouseCost, quantity: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        return (cost.carrying_value * quantity / cost.quantity).quantize(
            VALUE, rounding=ROUND_HALF_UP
        )


def outbound_value(cost: WarehouseCost, quantity: Decimal) -> Decimal:
    if quantity > cost.quantity:
        raise ValidationError(_("Insufficient stock."))
    if quantity == cost.quantity:
        return cost.carrying_value
    return proportional_value(cost, quantity)


def move(
    operation: StockOperation,
    kind: str,
    batch: Batch,
    location: Location,
    quantity: Decimal,
    value: Decimal | None = None,
    *,
    reason: str = "",
    reference: str = "",
) -> StockMovement:
    """Apply one immutable movement and its two materialized positions inside a posting."""
    balance, cost = _position(batch, location)
    if quantity < 0 and value is None:
        value = outbound_value(cost, -quantity).copy_negate()
    if value is None or not quantity:
        raise ValidationError(_("A movement requires quantity and carrying value."))
    balance.quantity = exact(balance.quantity + quantity, QTY, MAX_QUANTITY)
    cost.quantity = exact(cost.quantity + quantity, QTY, MAX_QUANTITY)
    with localcontext() as context:
        context.prec = 60
        cost.carrying_value += value
    if min(balance.quantity, cost.quantity, cost.carrying_value) < 0:
        raise ValidationError(_("Insufficient stock or carrying value."))
    if not cost.quantity and cost.carrying_value:
        raise ValidationError(_("This correction would leave carrying value without stock."))
    with localcontext() as context:
        context.prec = 60
        unit_cost = exact(
            (value / quantity).copy_abs().quantize(MONEY, rounding=ROUND_HALF_UP), MONEY, MAX_COST
        )
    balance.save(update_fields=["quantity"])
    cost.save(update_fields=["quantity", "carrying_value"])
    return StockMovement.objects.create(
        operation=operation,
        ordinal=operation.movements.count(),
        kind=kind,
        batch=batch,
        location=location,
        warehouse_id=location.warehouse_id,
        quantity=quantity,
        unit_cost=unit_cost,
        value_delta=value,
        reason=reason,
        reference=reference,
        created_by_id=operation.created_by_id,
    )


def log_posting(actor: User, operation: StockOperation) -> None:
    rows = list(
        operation.movements.order_by("ordinal").values(
            "id", "batch_id", "location_id", "quantity", "value_delta", "reason"
        )
    )
    changes: dict[tuple[int, int], Decimal] = {}
    for row in rows:
        key = (row["batch_id"], row["location_id"])
        changes[key] = changes.get(key, Decimal(0)) + row["quantity"]
    positions = StockBalance.objects.filter(
        batch_id__in={batch for batch, _ in changes},
        location_id__in={location for _, location in changes},
    ).values_list("batch_id", "location_id", "quantity")
    before, after = [], []
    for batch, location, quantity in positions:
        key = (batch, location)
        if key in changes:
            before.append(
                {"batch": batch, "location": location, "quantity": quantity - changes[key]}
            )
            after.append({"batch": batch, "location": location, "quantity": quantity})
    audit(
        actor,
        operation.kind,
        operation,
        {"balances": before},
        {"balances": after, "movements": rows, "reverses": operation.reverses_id},
    )


@transaction.atomic
def receive_goods(actor: User, key: UUID, data: Receipt) -> StockOperation:
    """Receive against a traceable lot. Newly received batches start in quarantine."""
    require(actor, "inventory.receive_stock")
    operation, created = begin(actor, key, MovementType.RECEIPT, data.posting_payload())
    if not created:
        return operation
    item, location = _item_and_location(data.item_id, data.location_id)
    Warehouse.objects.select_for_update(no_key=True).get(pk=location.warehouse_id)
    compatible(item, location)
    quantity = positive_quantity(item, data.quantity, data.unit_id)
    unit_cost = exact(data.unit_cost, MONEY, MAX_COST)
    number, reference = data.batch_number.strip(), data.reference.strip()
    if not (0 < len(number) <= 80 and 0 < len(reference) <= 120 and unit_cost >= 0):
        raise ValidationError(_("Enter a batch, receipt reference and nonnegative cost."))
    supplier = Supplier.objects.select_for_update().filter(pk=data.supplier_id, active=True).first()
    if supplier is None:
        raise ValidationError(_("Use an active supplier."))
    quality = data.initial_quality or Quality.QUARANTINE
    if quality != Quality.QUARANTINE:
        require(actor, "procurement.add_goodsreceipt")
        quarantine = (
            settings.PROCUREMENT_QUARANTINE_DRY
            if item.storage == Storage.DRY
            else settings.PROCUREMENT_QUARANTINE_COLD
        )
        if quality != Quality.APPROVED or quarantine:
            raise ValidationError(_("This receipt must start in quarantine."))
    batch, new_batch = Batch.objects.get_or_create(
        item=item,
        number=number,
        defaults={
            "supplier": supplier,
            "expiry": data.expiry,
            "receipt_reference": reference,
            "quality": quality,
        },
    )
    if (batch.supplier_id, batch.expiry, batch.receipt_reference) != (
        supplier.pk,
        data.expiry,
        reference,
    ):
        raise ValidationError(_("This batch number already has different receipt details."))
    if not new_batch and batch.quality != Quality.QUARANTINE:
        raise ValidationError(_("Only quarantined batches can accept further receipts."))
    move(
        operation,
        MovementType.RECEIPT,
        batch,
        location,
        quantity,
        extended_value(quantity, unit_cost),
        reference=reference,
    )
    log_posting(actor, operation)
    return operation


def _eligible(
    item: Item, location: Location, data: Withdrawal, *, issue: bool
) -> list[StockBalance]:
    rows = (
        StockBalance.objects.select_for_update(of=("self",))
        .select_related("batch")
        .filter(location=location, batch__item=item, quantity__gt=0)
    )
    if issue:
        today = timezone.localdate()
        rows = rows.filter(batch__quality=Quality.APPROVED, batch__expiry__gte=today) | rows.filter(
            batch__quality=Quality.APPROVED, batch__expiry__isnull=True
        )
    if data.batch_id is not None:
        rows = rows.filter(batch_id=data.batch_id)
    return list(rows.order_by(F("batch__expiry").asc(nulls_last=True), "batch_id"))


def _take(
    actor: User, operation: StockOperation, data: Withdrawal, *, destination_id: int | None = None
) -> None:
    locations = lock_locations(
        [data.location_id] if destination_id is None else [data.location_id, destination_id]
    )
    item = lock_items([data.item_id]).get(data.item_id)
    source = locations.get(data.location_id)
    target = locations.get(destination_id) if destination_id is not None else None
    if (
        item is None
        or source is None
        or (destination_id is not None and (target is None or source == target))
    ):
        raise ValidationError(_("Choose a valid item and different locations."))
    if target is not None:
        Warehouse.objects.select_for_update(no_key=True).get(pk=target.warehouse_id)
        compatible(item, target)
    quantity = positive_quantity(item, data.quantity, data.unit_id)
    reason = data.reason.strip()
    if len(reason) > 240:
        raise ValidationError(_("The reason is too long."))
    issue = operation.kind == MovementType.ISSUE
    if issue and data.batch_id is not None:
        require(actor, "inventory.override_fefo")
    if (
        issue
        and data.batch_id is not None
        or operation.kind in {MovementType.WRITE_OFF, MovementType.RETURN}
    ) and not reason:
        raise ValidationError(_("A reason is required."))
    if issue:
        compatible(item, source)
    if operation.kind == MovementType.RETURN and data.batch_id is None:
        raise ValidationError(_("Select a batch to return to its supplier."))
    rows = _eligible(item, source, data, issue=issue)
    if sum((row.quantity for row in rows), Decimal(0)) < quantity:
        raise ValidationError(_("Insufficient eligible stock."))
    total_value = Decimal(0)
    for row in rows:
        amount = min(quantity, row.quantity)
        kind = MovementType.TRANSFER_OUT if target is not None else operation.kind
        outgoing = move(operation, kind, row.batch, source, -amount, reason=reason)
        total_value -= outgoing.value_delta
        if target is not None:
            move(
                operation,
                MovementType.TRANSFER_IN,
                row.batch,
                target,
                amount,
                outgoing.value_delta.copy_negate(),
                reason=reason,
            )
        quantity -= amount
        if quantity == 0:
            break
    if operation.kind == MovementType.WRITE_OFF and total_value > settings.WRITE_OFF_LIMIT_UZS:
        require(actor, "inventory.large_write_off")
    log_posting(actor, operation)


@transaction.atomic
def _withdraw(
    actor: User, key: UUID, data: Withdrawal, kind: str, permission: str, **extra: Any
) -> StockOperation:
    require(actor, permission)
    operation, created = begin(actor, key, kind, {**vars(data), **extra})
    if created:
        _take(actor, operation, data, **extra)
    return operation


def issue_stock(actor: User, key: UUID, data: Withdrawal) -> StockOperation:
    """Issue approved, valid stock in FEFO order unless an authorized batch is specified."""
    return _withdraw(actor, key, data, MovementType.ISSUE, "inventory.issue_stock")


def transfer_stock(actor: User, key: UUID, data: Withdrawal, destination_id: int) -> StockOperation:
    """Move physical stock and its exact carrying value between locations."""
    return _withdraw(
        actor,
        key,
        data,
        MovementType.TRANSFER_OUT,
        "inventory.transfer_stock",
        destination_id=destination_id,
    )


def write_off(actor: User, key: UUID, data: Withdrawal) -> StockOperation:
    """Post a reasoned loss subject to the actor's write-off value limit."""
    return _withdraw(actor, key, data, MovementType.WRITE_OFF, "inventory.write_off_stock")


def return_to_supplier(actor: User, key: UUID, data: Withdrawal) -> StockOperation:
    """Return stock from a specific supplier lot."""
    return _withdraw(actor, key, data, MovementType.RETURN, "inventory.return_stock")


@transaction.atomic
def reverse_movement(actor: User, key: UUID, operation_id: int, reason: str) -> StockOperation:
    """Compensate a whole posting in reverse order, preserving original quantities and value."""
    require(actor, "inventory.reverse_stock")
    reason = reason.strip()
    if not reason or len(reason) > 240:
        raise ValidationError(_("A reason is required."))
    original = StockOperation.objects.select_for_update().filter(pk=operation_id).first()
    if original is None or original.kind == MovementType.REVERSAL:
        raise ValidationError(_("Choose an original posting to reverse."))
    if (
        apps.get_model("procurement", "GoodsReceiptLine")
        ._default_manager.filter(operation=original)
        .exists()
        or apps.get_model("procurement", "SupplierReturn")
        ._default_manager.filter(operation=original)
        .exists()
    ):
        raise ValidationError(_("Use procurement corrections for this stock posting."))
    prior = StockOperation.objects.filter(reverses=original).first()
    if prior is not None and prior.idempotency_key != key:
        raise ValidationError(_("This posting has already been reversed."))
    operation, created = begin(
        actor,
        key,
        MovementType.REVERSAL,
        {"operation": operation_id, "reason": reason},
        reverses=original,
    )
    if not created:
        return operation
    rows = list(original.movements.select_related("batch").order_by("-ordinal"))
    locations = lock_locations(row.location_id for row in rows)
    items = lock_items(row.batch.item_id for row in rows)
    warehouses = {location.warehouse_id for location in locations.values()}
    list(Warehouse.objects.select_for_update(no_key=True).filter(pk__in=warehouses).order_by("pk"))
    for row in rows:
        location = locations[row.location_id]
        if row.quantity < 0:
            compatible(items[row.batch.item_id], location)
        move(
            operation,
            MovementType.REVERSAL,
            row.batch,
            location,
            -row.quantity,
            row.value_delta.copy_negate(),
            reason=reason,
            reference=str(row.pk),
        )
    log_posting(actor, operation)
    return operation


@transaction.atomic
def change_quality(actor: User, batch_id: int, status: str) -> Batch:
    """Only allow a quarantined lot to be approved or rejected."""
    require(actor, "inventory.change_batch")
    batch = Batch.objects.select_related("item").get(pk=batch_id)
    lock_items([batch.item_id])
    batch.refresh_from_db()
    if batch.quality == status and status in {Quality.APPROVED, Quality.REJECTED}:
        return batch
    if batch.quality != Quality.QUARANTINE or status not in {Quality.APPROVED, Quality.REJECTED}:
        raise ValidationError(_("Only quarantined batches can change quality status."))
    previous = batch.quality
    batch.quality = status
    batch.save(update_fields=["quality"])
    audit(actor, "batch_quality", batch, {"quality": previous}, {"quality": status})
    return batch
