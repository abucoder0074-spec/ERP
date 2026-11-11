from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create demo business data once the required modules are available."

    def handle(self, *args: object, **options: object) -> None:
        raise CommandError(
            "Demo business data requires the inventory, payables and production modules. "
            "Phase 0 leaves the database unchanged."
        )
