from typing import TYPE_CHECKING, Any

from django import forms
from django.forms import modelform_factory

from apps.masterdata.models import (
    Item,
    ItemCategory,
    Location,
    MasterRecord,
    Supplier,
    UnitOfMeasure,
    UoMConversion,
    Warehouse,
)

if TYPE_CHECKING:
    MasterForm = forms.ModelForm[MasterRecord]
else:
    MasterForm = forms.ModelForm

CATALOGS: dict[str, tuple[type[MasterRecord], type[MasterForm], tuple[str, ...]]] = {}


def _register(
    slug: str, model: type[MasterRecord], fields: tuple[str, ...], search: tuple[str, ...]
) -> None:
    form: Any = modelform_factory(model, fields=(*fields, "active"))
    CATALOGS[slug] = (model, form, search)


_register("categories", ItemCategory, ("code", "name", "kind"), ("code", "name"))
_register("units", UnitOfMeasure, ("code", "name"), ("code", "name"))
_register("warehouses", Warehouse, ("code", "name"), ("code", "name"))
_register(
    "locations",
    Location,
    ("code", "name", "warehouse", "storage"),
    ("code", "name", "warehouse__code"),
)
_register("suppliers", Supplier, ("code", "name", "tin", "lead_time_days"), ("code", "name"))
_register(
    "items",
    Item,
    (
        "code",
        "name_uz",
        "name_ru",
        "name_en",
        "category",
        "base_unit",
        "min_stock",
        "max_stock",
        "reorder_qty",
        "shelf_life_days",
        "storage",
        "min_temperature",
        "max_temperature",
        "barcode",
    ),
    ("code", "name_uz", "name_ru", "name_en", "barcode"),
)
_register("conversions", UoMConversion, ("item", "unit", "factor"), ("item__code", "unit__code"))
