from django.db import connection, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.procurement.models import (
    DeliveryAlert,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    State,
)


def overdue_orders() -> list[PurchaseOrder]:
    """Find active orders whose promised date has passed and whose net receipts are short."""
    orders = (
        PurchaseOrder.objects.filter(
            state=State.ACTIVE, active_version__expected_on__lt=timezone.localdate()
        )
        .select_related(
            "active_version",
            "supplier",
        )
        .prefetch_related("active_version__lines")
    )
    from decimal import Decimal

    from django.db.models import Sum

    totals: dict[tuple[int, int], Decimal] = {}
    rows = (
        GoodsReceiptLine.objects.filter(
            receipt__order__in=orders,
            order_line__isnull=False,
        )
        .annotate(returned=Sum("returns__quantity"))
        .values_list(
            "receipt__order_id",
            "item_id",
            "quantity",
            "returned",
        )
    )
    for order_id, item_id, quantity, returned in rows:
        key = order_id, item_id
        totals[key] = totals.get(key, Decimal(0)) + quantity - (returned or 0)
    result = []
    for order in orders:
        version = order.active_version
        if version and any(
            totals.get((order.pk, line.item_id), 0) < line.quantity for line in version.lines.all()
        ):
            result.append(order)
    return result


def supplier_metrics() -> dict[int, tuple[int, int, int]]:
    """Return delivery count, on-time delivery count and defective line count by supplier."""
    deliveries = (
        GoodsReceipt.objects.order_by()
        .values_list("order__supplier_id")
        .annotate(
            deliveries=Count("pk"),
            on_time=Count("pk", filter=Q(delivered_on__lte=F("version__expected_on"))),
        )
    )
    defects = dict(
        GoodsReceiptLine.objects.filter(returns__isnull=False)
        .values_list(
            "receipt__order__supplier_id",
        )
        .annotate(defects=Count("pk", distinct=True))
    )
    return {
        supplier: (count, on_time, defects.get(supplier, 0))
        for supplier, count, on_time in deliveries
    }


@transaction.atomic
def refresh_delivery_alerts() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [41001917])
    overdue = {
        order.pk: order.active_version_id
        for order in overdue_orders()
        if order.active_version_id is not None
    }
    active = dict(
        DeliveryAlert.objects.filter(resolved_at__isnull=True).values_list("order_id", "version_id")
    )
    resolved = {order for order, version in active.items() if overdue.get(order) != version}
    DeliveryAlert.objects.filter(order_id__in=resolved, resolved_at__isnull=True).update(
        resolved_at=timezone.now()
    )
    return len(
        DeliveryAlert.objects.bulk_create(
            DeliveryAlert(order_id=order, version_id=version)
            for order, version in overdue.items()
            if active.get(order) != version
        )
    )
