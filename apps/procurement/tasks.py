from celery import shared_task

from apps.procurement.services.deliveries import refresh_delivery_alerts


@shared_task
def refresh_overdue_deliveries() -> int:
    return refresh_delivery_alerts()
