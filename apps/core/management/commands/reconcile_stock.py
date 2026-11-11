from django.core.management.base import BaseCommand, CommandError

from apps.inventory.services.reconciliation import discrepancies


class Command(BaseCommand):
    help = "Reconcile stock balances and warehouse carrying values against the ledger."

    def handle(self, *args: object, **options: object) -> None:
        differences = discrepancies()
        for difference in differences:
            self.stderr.write(difference)
        if differences:
            raise CommandError(f"{len(differences)} stock discrepancies")
        self.stdout.write("Stock balances and carrying values reconcile.")
