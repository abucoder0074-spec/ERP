from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid5

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import Batch, Quality
from apps.inventory.services.common import audit, lock_items, lock_locations, require
from apps.inventory.services.posting import Receipt, Withdrawal, receive_goods, return_to_supplier
from apps.masterdata.models import Storage, Supplier, Warehouse
from apps.procurement.models import (
    Attachment,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    State,
    SupplierReturn,
)
from apps.procurement.services.common import amount, identity, money, note, replay
from apps.procurement.services.orders import net_receipts, receipt_cap


@dataclass(frozen=True)
class DeliveryLine:
    item_id: int
    location_id: int
    quantity: Decimal
    batch: str
    expiry: date | None = None
    unplanned_price: Decimal | None = None
    unplanned_reason: str = ""


@dataclass(frozen=True)
class Delivery:
    delivered_on: date
    delay_reason: str
    lines: tuple[DeliveryLine, ...]


@transaction.atomic
def post_receipt(actor: User, key: UUID, order_id: int, data: Delivery) -> GoodsReceipt:
    """Post a delivery and its stock operations under one order lock and transaction."""
    require(actor, "procurement.add_goodsreceipt", "inventory.receive_stock")
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    payload = {**asdict(data), "order": order_id}
    existing = replay(GoodsReceipt, actor, key, payload)
    if existing:
        return existing
    version = order.active_version
    if order.state != State.ACTIVE or version is None or not 1 <= len(data.lines) <= 100:
        raise ValidationError(_("An active order and at least one delivery line are required."))
    if data.delivered_on > timezone.localdate():
        raise ValidationError(_("Delivery cannot be dated in the future."))
    delay_reason = note(data.delay_reason, 300, required=data.delivered_on > version.expected_on)
    planned = {line.item_id: line for line in version.lines.all()}
    received = net_receipts(order)
    locations = lock_locations(line.location_id for line in data.lines)
    items = lock_items(line.item_id for line in data.lines)
    list(
        Warehouse.objects.select_for_update(no_key=True)
        .filter(pk__in={loc.warehouse_id for loc in locations.values()})
        .order_by("pk")
    )
    Supplier.objects.select_for_update().get(pk=order.supplier_id)
    receipt = GoodsReceipt.objects.create(
        **identity(actor, key, payload),
        order=order,
        version=version,
        delivered_on=data.delivered_on,
        delay_reason=delay_reason,
    )
    for index, line in enumerate(data.lines):
        quantity = amount(line.quantity, quantity=True)
        item = items.get(line.item_id)
        if item is None or line.location_id not in locations:
            raise ValidationError(_("Choose a valid item and location."))
        order_line = planned.get(line.item_id)
        reason = ""
        if order_line is None:
            require(actor, "procurement.receive_unplanned")
            reason = note(line.unplanned_reason, 300)
            if line.unplanned_price is None:
                raise ValidationError(_("An unplanned receipt needs a unit price."))
            price = amount(line.unplanned_price)
        else:
            price = order_line.price
            cumulative = received.get(line.item_id, Decimal(0)) + quantity
            if cumulative > receipt_cap(order_line.quantity):
                raise ValidationError(_("This receipt exceeds the cumulative order tolerance."))
            received[line.item_id] = cumulative
        supplier_batch = note(line.batch, 80)
        number = supplier_batch
        if Batch.objects.filter(item=item, number=number).exists():
            suffix = f"/GR{receipt.pk}-{index + 1}"
            number = supplier_batch[: 80 - len(suffix)] + suffix
        quarantine = (
            settings.PROCUREMENT_QUARANTINE_DRY
            if item.storage == Storage.DRY
            else settings.PROCUREMENT_QUARANTINE_COLD
        )
        operation = receive_goods(
            actor,
            uuid5(key, f"stock:{index}"),
            Receipt(
                item.pk,
                line.location_id,
                order.supplier_id,
                number,
                quantity,
                money(price * version.exchange_rate),
                str(receipt),
                line.expiry,
                initial_quality=Quality.QUARANTINE if quarantine else Quality.APPROVED,
            ),
        )
        movement = operation.movements.get()
        GoodsReceiptLine.objects.create(
            receipt=receipt,
            order_line=order_line,
            item=item,
            batch_id=movement.batch_id,
            location_id=line.location_id,
            operation=operation,
            quantity=quantity,
            price=price,
            supplier_batch=supplier_batch,
            unplanned_reason=reason,
        )
    audit(actor, "goods_receipt_post", receipt, {}, payload)
    return receipt


@transaction.atomic
def post_return(
    actor: User, key: UUID, line_id: int, quantity: Decimal, reason: str, photo_id: int
) -> SupplierReturn:
    """Return defective stock to the supplier and preserve its receipt evidence."""
    require(actor, "procurement.add_supplierreturn", "inventory.return_stock")
    line = GoodsReceiptLine.objects.select_related("receipt").get(pk=line_id)
    order = PurchaseOrder.objects.select_for_update().get(pk=line.receipt.order_id)
    payload = {"line": line_id, "quantity": quantity, "reason": reason, "photo": photo_id}
    existing = replay(SupplierReturn, actor, key, payload)
    if existing:
        return existing
    quantity = amount(quantity, quantity=True)
    reason = note(reason, 240)
    returned = line.returns.aggregate(total=Sum("quantity"))["total"] or Decimal(0)
    if quantity + returned > line.quantity:
        raise ValidationError(_("Return quantity exceeds the remaining receipt quantity."))
    if line.invoice_lines.exclude(invoice__state=State.CANCELLED).exists():
        raise ValidationError(_("Cancel the linked invoice before returning this stock."))
    photo = (
        Attachment.objects.defer("content")
        .filter(pk=photo_id, receipt=line.receipt, mime_type__startswith="image/")
        .first()
    )
    if photo is None:
        raise ValidationError(_("Attach a photo to this receipt before returning stock."))
    operation = return_to_supplier(
        actor,
        uuid5(key, "stock:return"),
        Withdrawal(
            line.item_id,
            line.location_id,
            quantity,
            batch_id=line.batch_id,
            reason=reason,
        ),
    )
    document = SupplierReturn.objects.create(
        **identity(actor, key, payload),
        line=line,
        operation=operation,
        quantity=quantity,
        reason=reason,
        photo=photo,
    )
    audit(
        actor,
        "supplier_return_post",
        document,
        {"received": line.quantity - returned},
        {
            **payload,
            "remaining": line.quantity - returned - quantity,
            "order": order.pk,
        },
    )
    return document
