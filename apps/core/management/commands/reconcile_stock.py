from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Reconcile stock balances against ledger movements once inventory is available."

    def handle(self, *args: object, **options: object) -> None:
        raise CommandError("Stock reconciliation requires the Phase 1 inventory ledger.")
