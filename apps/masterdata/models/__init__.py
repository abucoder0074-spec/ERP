from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _


class Storage(models.TextChoices):
    DRY = "dry", _("Dry")
    CHILLED = "chilled", _("Chilled")
    FROZEN = "frozen", _("Frozen")


class CategoryType(models.TextChoices):
    RAW = "raw", _("Raw material")
    PACKAGING = "packaging", _("Packaging")
    SPARE = "spare", _("Spare part")
    CONSUMABLE = "consumable", _("Consumable")


class MasterRecord(models.Model):
    active = models.BooleanField(_("Active"), default=True)

    class Meta:
        abstract = True


class NamedRecord(MasterRecord):
    code = models.CharField(_("Code"), max_length=40, unique=True)
    name = models.CharField(_("Name"), max_length=160)

    class Meta:
        abstract = True
        ordering = ("code",)

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


class ItemCategory(NamedRecord):
    kind = models.CharField(_("Category type"), choices=CategoryType, max_length=12)


class UnitOfMeasure(NamedRecord):
    pass


class Warehouse(NamedRecord):
    pass


class Supplier(NamedRecord):
    tin = models.CharField(_("TIN"), max_length=20, blank=True)
    lead_time_days = models.PositiveIntegerField(_("Lead time in days"), default=0)


class Location(NamedRecord):
    warehouse = models.ForeignKey(Warehouse, models.PROTECT, verbose_name=_("Warehouse"))
    storage = models.CharField(_("Storage condition"), choices=Storage, max_length=8)


class Item(MasterRecord):
    code = models.CharField(_("Code"), max_length=40, unique=True)
    name_uz = models.CharField(_("Uzbek name"), max_length=160)
    name_ru = models.CharField(_("Russian name"), max_length=160, blank=True)
    name_en = models.CharField(_("English name"), max_length=160, blank=True)
    category = models.ForeignKey(ItemCategory, models.PROTECT, verbose_name=_("Category"))
    base_unit = models.ForeignKey(UnitOfMeasure, models.PROTECT, verbose_name=_("Base unit"))
    min_stock = models.DecimalField(_("Minimum stock"), max_digits=15, decimal_places=3, default=0)
    max_stock = models.DecimalField(
        _("Maximum stock"), max_digits=15, decimal_places=3, null=True, blank=True
    )
    reorder_qty = models.DecimalField(
        _("Reorder quantity"), max_digits=15, decimal_places=3, default=0
    )
    shelf_life_days = models.PositiveIntegerField(_("Shelf life in days"), null=True, blank=True)
    storage = models.CharField(_("Storage condition"), choices=Storage, max_length=8)
    min_temperature = models.DecimalField(
        _("Minimum temperature"), max_digits=5, decimal_places=2, null=True, blank=True
    )
    max_temperature = models.DecimalField(
        _("Maximum temperature"), max_digits=5, decimal_places=2, null=True, blank=True
    )
    barcode = models.CharField(_("Barcode or QR code"), max_length=120, blank=True)

    class Meta:
        ordering = ("code",)
        constraints = [
            models.CheckConstraint(
                condition=Q(min_stock__gte=0, reorder_qty__gte=0)
                & (Q(max_stock__isnull=True) | Q(max_stock__gte=F("min_stock"))),
                name="item_stock_thresholds",
            ),
            models.CheckConstraint(
                condition=Q(min_temperature__isnull=True)
                | Q(max_temperature__isnull=True)
                | Q(max_temperature__gte=F("min_temperature")),
                name="item_temperature_range",
            ),
            models.UniqueConstraint(
                fields=("barcode",), condition=~Q(barcode=""), name="item_barcode"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} · {self.name_uz}"


class UoMConversion(MasterRecord):
    item = models.ForeignKey(Item, models.PROTECT, verbose_name=_("Item"))
    unit = models.ForeignKey(UnitOfMeasure, models.PROTECT, verbose_name=_("Alternative unit"))
    factor = models.DecimalField(
        _("Base units per alternative unit"),
        max_digits=18,
        decimal_places=9,
        validators=[MinValueValidator(Decimal("0.000000001"))],
    )

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(fields=("item", "unit"), name="item_alternative_unit"),
            models.CheckConstraint(condition=Q(factor__gt=0), name="uom_positive_factor"),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} · {self.unit_id} × {self.factor}"
