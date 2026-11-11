from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.inventory.models import Batch, Quality
from apps.inventory.services.alerts import refresh_alerts
from apps.inventory.services.posting import Receipt, change_quality, receive_goods
from apps.masterdata.forms import CATALOGS
from apps.masterdata.models import Item, NamedRecord
from apps.masterdata.services import save_record
from apps.procurement.services.demo import seed_procurement


def _record(actor: User, slug: str, code: str, **fields: Any) -> int:
    model = cast(type[NamedRecord] | type[Item], CATALOGS[slug][0])
    existing = model._default_manager.filter(code=code).first()
    if existing is not None:
        return int(existing.pk)
    form, record = save_record(actor, slug, {"code": code, "active": True, **fields})
    if record is None:
        raise CommandError(f"Could not seed {code}: {form.errors.as_text()}")
    return int(record.pk)


class Command(BaseCommand):
    help = "Seed repeatable Uzbek Phase 1 catalog and opening stock in development."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        if not settings.DEBUG:
            raise CommandError("Demo seeding is available only in development.")
        actor, created = User.objects.get_or_create(username="phase1_demo_seed")
        group = Group.objects.get(name="Admin")
        if created:
            actor.set_unusable_password()
            actor.save(update_fields=["password"])
            actor.groups.add(group)
        elif actor.has_usable_password() or not actor.groups.filter(pk=group.pk).exists():
            raise CommandError("The reserved demo seed account already exists.")
        raw = _record(actor, "categories", "DEMO-XA", name="Xomashyo", kind="raw")
        pack = _record(actor, "categories", "DEMO-QD", name="Qadoqlash", kind="packaging")
        kg = _record(actor, "units", "DEMO-KG", name="Kilogramm")
        piece = _record(actor, "units", "DEMO-DONA", name="Dona")
        warehouse = _record(actor, "warehouses", "DEMO-TOSH", name="Toshkent ombori")
        chilled = _record(
            actor, "locations", "DEMO-SOV", name="Sovitkich", warehouse=warehouse, storage="chilled"
        )
        dry = _record(
            actor, "locations", "DEMO-QUR", name="Quruq xona", warehouse=warehouse, storage="dry"
        )
        milk_supplier = _record(
            actor, "suppliers", "DEMO-SUTCHI", name="Chirchiq Sut", lead_time_days=1
        )
        sugar_supplier = _record(
            actor, "suppliers", "DEMO-SHAKAR", name="Yangiyo‘l Shakar", lead_time_days=2
        )
        pack_supplier = _record(
            actor, "suppliers", "DEMO-QADOQ", name="Toshkent Qadoq", lead_time_days=3
        )
        stock = (
            (
                "DEMO-SUT",
                "Sut",
                raw,
                kg,
                "chilled",
                chilled,
                milk_supplier,
                "A",
                2,
                "48",
                "7800",
                True,
            ),
            (
                "DEMO-SUT",
                "Sut",
                raw,
                kg,
                "chilled",
                chilled,
                milk_supplier,
                "B",
                8,
                "24",
                "8200",
                False,
            ),
            (
                "DEMO-QAND",
                "Shakar",
                raw,
                kg,
                "dry",
                dry,
                sugar_supplier,
                "A",
                140,
                "160",
                "12500",
                True,
            ),
            (
                "DEMO-STAKAN",
                "Stakan",
                pack,
                piece,
                "dry",
                dry,
                pack_supplier,
                "A",
                220,
                "600",
                "350",
                True,
            ),
            (
                "DEMO-KONUS",
                "Vafli konus",
                pack,
                piece,
                "dry",
                dry,
                pack_supplier,
                "A",
                65,
                "400",
                "900",
                True,
            ),
        )
        for (
            code,
            name,
            category,
            unit,
            storage,
            location,
            supplier,
            suffix,
            days,
            qty,
            cost,
            approved,
        ) in stock:
            item = _record(
                actor,
                "items",
                code,
                name_uz=name,
                category=category,
                base_unit=unit,
                storage=storage,
                min_stock="100",
                max_stock="500",
                reorder_qty="150",
            )
            number = f"{code}-{suffix}"
            batch = Batch.objects.filter(item_id=item, number=number).first()
            expiry = batch.expiry if batch else timezone.localdate() + timedelta(days=days)
            receive_goods(
                actor,
                uuid5(NAMESPACE_URL, f"muzqaymoq/phase1/{number}"),
                Receipt(
                    item,
                    location,
                    supplier,
                    number,
                    Decimal(qty),
                    Decimal(cost),
                    "DEMO-KIRIM",
                    expiry,
                ),
            )
            if approved:
                batch = Batch.objects.get(item_id=item, number=number)
                if batch.quality == Quality.QUARANTINE:
                    change_quality(actor, batch.pk, Quality.APPROVED)
        invoice = seed_procurement(actor)
        alerts = refresh_alerts()
        self.stdout.write(
            f"Phase 1 demo ready: 4 items, 3 suppliers, 5 batches, {alerts} new alerts."
        )

        self.stdout.write(
            "Phase 2 demo ready: request, approval, RFQ, PO, partial receipt, "
            f"invoice {invoice.number} ({invoice.state})."
        )
