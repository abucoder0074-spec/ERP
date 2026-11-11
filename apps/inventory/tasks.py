from celery import shared_task

from apps.inventory.services.alerts import refresh_alerts


@shared_task
def refresh_stock_alerts() -> int:
    return refresh_alerts()
