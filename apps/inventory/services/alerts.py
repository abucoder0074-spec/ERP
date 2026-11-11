from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.inventory.models import AlertKind, Notification, StockBalance
from apps.masterdata.models import Item


@transaction.atomic
def refresh_alerts() -> int:
    """Maintain one open notification per active condition, including across beat workers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [41001916])
    now = timezone.now()
    today = timezone.localdate(now)
    cutoff = today + timedelta(days=settings.STOCK_EXPIRY_WARNING_DAYS)
    rows = StockBalance.objects.filter(quantity__gt=0)
    totals = dict(rows.values_list("batch__item_id").annotate(total=Sum("quantity")))
    conditions: dict[str, tuple[str, int, int | None]] = {}
    for item in Item.objects.filter(active=True).iterator():
        quantity = totals.get(item.pk, Decimal(0))
        if quantity < item.min_stock:
            conditions[f"low:{item.pk}"] = (AlertKind.LOW, item.pk, None)
        if item.max_stock is not None and quantity > item.max_stock:
            conditions[f"high:{item.pk}"] = (AlertKind.HIGH, item.pk, None)
    lots = (
        rows.filter(batch__expiry__lte=cutoff)
        .values_list("batch_id", "batch__item_id", "batch__expiry")
        .distinct()
    )
    for batch_id, item_id, expiry in lots:
        assert expiry is not None
        kind = AlertKind.EXPIRED if expiry < today else AlertKind.EXPIRING
        conditions[f"{kind}:{batch_id}"] = (kind, item_id, batch_id)
    active = Notification.objects.filter(resolved_at__isnull=True)
    existing = set(active.values_list("condition", flat=True))
    active.filter(condition__in=existing - conditions.keys()).update(resolved_at=now)
    created = Notification.objects.bulk_create(
        Notification(condition=key, kind=kind, item_id=item_id, batch_id=batch_id)
        for key, (kind, item_id, batch_id) in conditions.items()
        if key not in existing
    )
    return len(created)
