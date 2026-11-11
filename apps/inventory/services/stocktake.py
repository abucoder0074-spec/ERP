from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils.translation import gettext as _

from apps.inventory.models import (
    Batch,
    MovementType,
    StockBalance,
    StockMovement,
    StockOperation,
    Stocktake,
    StocktakeLine,
    StocktakeStatus,
    WarehouseCost,
)
from apps.inventory.services.common import (
    MAX_QUANTITY,
    QTY,
    audit,
    begin,
    exact,
    lock_items,
    lock_locations,
    require,
)
from apps.inventory.services.posting import (
    compatible,
    extended_value,
    log_posting,
    move,
    proportional_value,
)
from apps.masterdata.models import Warehouse


def _watermark(location_id: int) -> int:
    result: int | None = StockMovement.objects.filter(location_id=location_id).aggregate(
        value=Max("pk")
    )["value"]
    return result or 0


@transaction.atomic
def start_stocktake(actor: User, key: UUID, location_id: int) -> Stocktake:
    """Snapshot expected lots and the latest movement while the location is locked."""
    require(actor, "inventory.count_stocktake")
    location = lock_locations([location_id]).get(location_id)
    if location is None or not location.active:
        raise ValidationError(_("Choose an active location."))
    sheet, created = Stocktake.objects.get_or_create(
        key=key,
        defaults={"location": location, "counter": actor, "watermark": _watermark(location_id)},
    )
    if sheet.location_id != location_id or sheet.counter_id != actor.pk:
        raise ValidationError(_("This posting key is already used for a different request."))
    if created:
        rows = StockBalance.objects.filter(location=location, quantity__gt=0)
        StocktakeLine.objects.bulk_create(
            StocktakeLine(sheet=sheet, batch_id=row.batch_id, expected=row.quantity) for row in rows
        )
        audit(
            actor,
            "stocktake_start",
            sheet,
            {},
            {"location": location_id, "watermark": sheet.watermark},
        )
    return sheet


@transaction.atomic
def count_batch(actor: User, sheet_id: int, batch_id: int, quantity: Decimal) -> Stocktake:
    """Save a counted quantity, including a known lot found unexpectedly."""
    require(actor, "inventory.count_stocktake")
    sheet = Stocktake.objects.select_for_update().get(pk=sheet_id)
    if sheet.counter_id != actor.pk:
        raise PermissionDenied
    if sheet.status != StocktakeStatus.COUNTING:
        raise ValidationError(_("This sheet is no longer accepting counts."))
    quantity = exact(quantity, QTY, MAX_QUANTITY)
    if quantity < 0:
        raise ValidationError(_("Counts cannot be negative."))
    batch = Batch.objects.select_related("item").get(pk=batch_id)
    line, created = StocktakeLine.objects.get_or_create(
        sheet=sheet, batch=batch, defaults={"expected": 0}
    )
    if created:
        compatible(batch.item, sheet.location)
    previous = line.counted
    if previous != quantity:
        line.counted = quantity
        line.save(update_fields=["counted"])
        audit(actor, "stocktake_count", line, {"counted": previous}, {"counted": quantity})
    return sheet


@transaction.atomic
def transition(actor: User, sheet_id: int, status: str) -> Stocktake:
    """Submit, approve or cancel a sheet while enforcing the counter/approver split."""
    sheet = Stocktake.objects.select_for_update().get(pk=sheet_id)
    current = sheet.status
    if status == StocktakeStatus.APPROVED:
        require(actor, "inventory.approve_stocktake")
        if sheet.counter_id == actor.pk:
            raise PermissionDenied
        expected = {StocktakeStatus.SUBMITTED}
    else:
        if sheet.counter_id != actor.pk:
            require(actor, "inventory.approve_stocktake")
        else:
            require(actor, "inventory.count_stocktake")
        if status == StocktakeStatus.SUBMITTED:
            if sheet.counter_id != actor.pk:
                raise PermissionDenied
            if sheet.lines.filter(counted__isnull=True).exists():
                raise ValidationError(_("Enter every count before submitting."))
            expected = {StocktakeStatus.COUNTING}
        elif status == StocktakeStatus.CANCELLED:
            expected = {
                StocktakeStatus.COUNTING,
                StocktakeStatus.SUBMITTED,
                StocktakeStatus.APPROVED,
            }
        else:
            raise ValidationError(_("Invalid stocktake transition."))
    if current == status:
        return sheet
    if current not in expected:
        raise ValidationError(_("Invalid stocktake transition."))
    if status == StocktakeStatus.APPROVED:
        sheet.approver = actor
    sheet.status = status
    sheet.save(update_fields=["status", "approver"])
    audit(actor, "stocktake_status", sheet, {"status": current}, {"status": status})
    return sheet


def _surplus_value(batch: Batch, warehouse_id: int, delta: Decimal) -> Decimal:
    cost = WarehouseCost.objects.filter(
        item_id=batch.item_id, warehouse_id=warehouse_id, quantity__gt=0
    ).first()
    if cost is not None:
        return proportional_value(cost, delta)
    receipt = (
        StockMovement.objects.filter(batch=batch, kind=MovementType.RECEIPT).order_by("-pk").first()
    )
    if receipt is None:
        raise ValidationError(_("The batch has no receipt cost."))
    return extended_value(delta, receipt.unit_cost)


@transaction.atomic
def post_stocktake(actor: User, key: UUID, sheet_id: int) -> StockOperation:
    """Post an approved count exactly once if no stock has moved since the snapshot."""
    require(actor, "inventory.approve_stocktake")
    sheet = Stocktake.objects.select_for_update().get(pk=sheet_id)
    operation, created = begin(actor, key, MovementType.STOCKTAKE, {"sheet": sheet_id})
    if not created:
        return operation
    if sheet.status != StocktakeStatus.APPROVED or sheet.counter_id == actor.pk:
        raise ValidationError(_("An approved stocktake and a different posting user are required."))
    location = lock_locations([sheet.location_id])[sheet.location_id]
    if _watermark(location.pk) != sheet.watermark:
        raise ValidationError(_("Stock moved after the snapshot. Start a new count."))
    lines = list(sheet.lines.select_related("batch").order_by("pk"))
    items = lock_items(line.batch.item_id for line in lines)
    Warehouse.objects.select_for_update(no_key=True).get(pk=location.warehouse_id)
    for line in lines:
        if line.counted is None:
            raise ValidationError(_("Enter every count before posting."))
        delta = line.counted - line.expected
        if delta:
            value = None
            if delta > 0:
                compatible(items[line.batch.item_id], location)
                value = _surplus_value(line.batch, location.warehouse_id, delta)
            move(
                operation,
                MovementType.STOCKTAKE,
                line.batch,
                location,
                delta,
                value,
                reference=str(sheet.pk),
            )
    sheet.operation = operation
    sheet.status = StocktakeStatus.POSTED
    sheet.save(update_fields=["operation", "status"])
    audit(
        actor,
        "stocktake_status",
        sheet,
        {"status": StocktakeStatus.APPROVED},
        {"status": sheet.status},
    )
    log_posting(actor, operation)
    return operation
