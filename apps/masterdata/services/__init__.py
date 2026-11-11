from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import ModelForm, model_to_dict
from django.utils.translation import gettext as _

from apps.inventory.models import StockBalance, StockMovement
from apps.inventory.services.common import audit, lock_items, require
from apps.masterdata.forms import CATALOGS
from apps.masterdata.models import Item, Location, MasterRecord, UoMConversion, Warehouse


def _snapshot(instance: MasterRecord) -> dict[str, Any]:
    state = model_to_dict(instance)
    state.pop("tin", None)
    return state


def _validate(instance: MasterRecord, before: dict[str, Any]) -> None:
    if isinstance(instance, Item):
        if (
            before
            and (
                instance.storage != before["storage"]
                or instance.base_unit_id != before["base_unit"]
            )
            and StockMovement.objects.filter(batch__item=instance).exists()
        ):
            raise ValidationError(
                _("Storage condition and base unit are fixed after stock is posted.")
            )
        if instance.active and (not instance.base_unit.active or not instance.category.active):
            raise ValidationError(_("Use an active category and base unit."))
    if isinstance(instance, Location):
        if (
            before
            and (
                instance.storage != before["storage"]
                or instance.warehouse_id != before["warehouse"]
            )
            and StockMovement.objects.filter(location=instance).exists()
        ):
            raise ValidationError(
                _("Warehouse and storage condition are fixed after stock is posted.")
            )
        if instance.active and not instance.warehouse.active:
            raise ValidationError(_("Use an active warehouse."))
    if isinstance(instance, UoMConversion):
        if instance.unit_id == instance.item.base_unit_id:
            raise ValidationError(_("The alternative unit must differ from the base unit."))
        if instance.active and (not instance.item.active or not instance.unit.active):
            raise ValidationError(_("Use an active item and unit."))
    if before and not instance.active and before["active"]:
        occupied = StockBalance.objects.filter(quantity__gt=0)
        if (
            isinstance(instance, Warehouse)
            and occupied.filter(location__warehouse=instance).exists()
        ):
            raise ValidationError(_("Move remaining stock before archiving this warehouse."))
        if isinstance(instance, Location) and occupied.filter(location=instance).exists():
            raise ValidationError(_("Move remaining stock before archiving this location."))
        if isinstance(instance, Item) and occupied.filter(batch__item=instance).exists():
            raise ValidationError(
                _("Move or write off remaining stock before archiving this item.")
            )


def validate_record(slug: str, data: Any, instance: MasterRecord | None) -> ModelForm[MasterRecord]:
    """Apply the same master data validation for imports and interactive changes."""
    _model, form_class, _search = CATALOGS[slug]
    before = _snapshot(instance) if instance is not None else {}
    form: ModelForm[MasterRecord] = form_class(data, instance=instance)
    if form.is_valid():
        try:
            _validate(form.save(commit=False), before)
        except ValidationError as error:
            form.add_error(None, error)
    return form


@transaction.atomic
def save_record(
    actor: User, slug: str, data: Any, pk: int | None = None
) -> tuple[ModelForm[MasterRecord], MasterRecord | None]:
    """Validate and persist master data under lock, auditing each effective edit."""
    model, _form_class, _search = CATALOGS[slug]
    action = "change" if pk is not None else "add"
    require(actor, f"masterdata.{action}_{model._meta.model_name}")
    if model is UoMConversion:
        item_id = data.get("item")
        if item_id is not None and str(item_id).isdecimal():
            lock_items([int(item_id)])
    record = model._default_manager.select_for_update().get(pk=pk) if pk is not None else None
    before = _snapshot(record) if record is not None else {}
    previous_tin = getattr(record, "tin", None)
    form = validate_record(slug, data, record)
    if not form.is_valid():
        return form, None
    instance = form.save(commit=False)
    after = _snapshot(instance)
    tin_changed = previous_tin != getattr(instance, "tin", None)
    if record is None or before != after or tin_changed:
        instance.save()
        if tin_changed:
            after["tin_changed"] = True
        audit(actor, f"master_{action}", instance, before, after)
    return form, instance
