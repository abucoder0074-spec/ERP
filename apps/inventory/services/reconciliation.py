from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Sum

from apps.inventory.models import StockBalance, StockMovement, WarehouseCost


@transaction.atomic
def discrepancies() -> list[str]:
    """Compare both materializations against a consistent PostgreSQL snapshot."""
    with connection.cursor() as cursor:
        cursor.execute(
            "LOCK TABLE inventory_stockbalance, inventory_warehousecost, inventory_stockmovement "
            "IN SHARE MODE"
        )
    ledger = {
        (batch, location): quantity
        for batch, location, quantity in StockMovement.objects.values_list(
            "batch_id", "location_id"
        ).annotate(quantity=Sum("quantity"))
    }
    balances = {
        (batch, location): quantity
        for batch, location, quantity in StockBalance.objects.values_list(
            "batch_id", "location_id", "quantity"
        )
    }
    valuation = {
        (item, warehouse): (quantity, value)
        for item, warehouse, quantity, value in StockMovement.objects.values_list(
            "batch__item_id", "warehouse_id"
        ).annotate(total=Sum("quantity"), value=Sum("value_delta"))
    }
    costs = {
        (item, warehouse): (quantity, value)
        for item, warehouse, quantity, value in WarehouseCost.objects.values_list(
            "item_id", "warehouse_id", "quantity", "carrying_value"
        )
    }
    zero = Decimal(0)
    messages = [
        f"Batch/location {key}: ledger={ledger.get(key, zero)} balance={balances.get(key, zero)}"
        for key in sorted(ledger.keys() | balances.keys())
        if ledger.get(key, zero) != balances.get(key, zero)
    ]
    messages.extend(
        f"Item/warehouse {key}: ledger={valuation.get(key, (zero, zero))} "
        f"cost={costs.get(key, (zero, zero))}"
        for key in sorted(valuation.keys() | costs.keys())
        if valuation.get(key, (zero, zero)) != costs.get(key, (zero, zero))
    )
    return messages
