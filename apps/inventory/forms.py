from decimal import Decimal
from typing import Any
from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.inventory.models import Batch, Quality
from apps.masterdata.models import Item, Location, Supplier, UnitOfMeasure


def item_from_code(code: str) -> Item:
    item = (
        Item.objects.filter(active=True).filter(Q(code=code) | Q(barcode=code)).order_by("pk")[:2]
    )
    matches = list(item)
    if len(matches) != 1:
        raise ValidationError(_("Enter an unambiguous active item code or barcode."))
    return matches[0]


def batch_from_code(code: str) -> Batch:
    item_code, separator, number = code.partition(":")
    queryset = Batch.objects.all()
    if separator:
        queryset = queryset.filter(item__code=item_code, number=number)
    else:
        queryset = queryset.filter(number=code)
    matches = list(queryset.order_by("pk")[:2])
    if len(matches) != 1:
        raise ValidationError(
            _("Enter a batch number or item code followed by a colon and batch number.")
        )
    return matches[0]


class PostingForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)


class QuantityForm(PostingForm):
    item = forms.CharField(
        label=_("Item code or barcode"),
        max_length=120,
        widget=forms.TextInput(attrs={"data-scan-field": "true"}),
    )
    location = forms.ModelChoiceField(
        label=_("Location"), queryset=Location.objects.filter(active=True)
    )
    quantity = forms.DecimalField(
        label=_("Quantity"), max_digits=18, decimal_places=6, min_value=Decimal("0.000001")
    )
    unit = forms.ModelChoiceField(
        label=_("Alternative unit"),
        queryset=UnitOfMeasure.objects.filter(active=True),
        required=False,
    )

    def clean_item(self) -> Item:
        return item_from_code(self.cleaned_data["item"])


class ReceiptForm(QuantityForm):
    supplier = forms.ModelChoiceField(
        label=_("Supplier"), queryset=Supplier.objects.filter(active=True)
    )
    batch_number = forms.CharField(label=_("Batch number"), max_length=80)
    expiry = forms.DateField(
        label=_("Expiry date"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    unit_cost = forms.DecimalField(
        label=_("Unit cost in UZS per base unit"),
        max_digits=15,
        decimal_places=2,
        min_value=Decimal(0),
    )
    reference = forms.CharField(label=_("Receipt reference"), max_length=120)


class WithdrawalForm(QuantityForm):
    batch = forms.CharField(label=_("Batch number"), max_length=125, required=False)
    reason = forms.CharField(label=_("Reason"), max_length=240, required=False)

    def clean_batch(self) -> Batch | None:
        raw = self.cleaned_data["batch"]
        return batch_from_code(raw) if raw else None


class TransferForm(WithdrawalForm):
    destination = forms.ModelChoiceField(
        label=_("Destination location"), queryset=Location.objects.filter(active=True)
    )


class ReasonForm(PostingForm):
    reason = forms.CharField(label=_("Reason"), max_length=240)


class QualityForm(forms.Form):
    quality = forms.ChoiceField(
        label=_("Quality status"),
        choices=[
            (Quality.APPROVED, Quality.APPROVED.label),
            (Quality.REJECTED, Quality.REJECTED.label),
        ],
    )


class StocktakeStartForm(PostingForm):
    location = forms.ModelChoiceField(
        label=_("Location"), queryset=Location.objects.filter(active=True)
    )


class CountForm(forms.Form):
    batch = forms.CharField(
        label=_("Batch number or QR code"),
        max_length=125,
        widget=forms.TextInput(attrs={"data-scan-field": "true"}),
    )
    counted = forms.DecimalField(
        label=_("Counted quantity"), max_digits=15, decimal_places=3, min_value=Decimal(0)
    )

    def clean_batch(self) -> Batch:
        return batch_from_code(self.cleaned_data["batch"])


def pk_of(data: dict[str, Any], name: str) -> int | None:
    value = data.get(name)
    return value.pk if value is not None else None
