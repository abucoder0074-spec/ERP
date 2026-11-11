from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.inventory.forms import item_from_code
from apps.masterdata.models import Item, Location, Supplier
from apps.procurement.models import ApprovalDelegation, ApprovalRule, Currency, GoodsReceiptLine


class KeyForm(forms.Form):
    key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)


class NoteForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"), max_length=300, widget=forms.Textarea(attrs={"rows": 3})
    )


class DecisionForm(forms.Form):
    approve = forms.TypedChoiceField(
        label=_("Decision"),
        choices=[("yes", _("Approve")), ("no", _("Reject"))],
        coerce=lambda value: value == "yes",
    )
    comment = forms.CharField(label=_("Comment"), max_length=300, required=False)


class RequestForm(KeyForm):
    department = forms.CharField(label=_("Department"), max_length=100)
    needed_by = forms.DateField(
        label=_("Needed by"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    priority = forms.ChoiceField(
        label=_("Priority"), choices=[("normal", _("Normal")), ("urgent", _("Urgent"))]
    )
    justification = forms.CharField(
        label=_("Justification"), max_length=500, widget=forms.Textarea(attrs={"rows": 3})
    )


class ItemQuantity(forms.Form):
    item = forms.ModelChoiceField(label=_("Item"), queryset=Item.objects.filter(active=True))
    quantity = forms.DecimalField(
        label=_("Quantity"), min_value=Decimal(".001"), max_digits=15, decimal_places=3
    )


class RequestRow(ItemQuantity):
    estimated_cost = forms.DecimalField(
        label=_("Estimated unit cost in UZS"),
        min_value=Decimal(".01"),
        max_digits=15,
        decimal_places=2,
    )
    supplier = forms.ModelChoiceField(
        label=_("Preferred supplier"), queryset=Supplier.objects.filter(active=True), required=False
    )


class RFQForm(KeyForm):
    suppliers = forms.ModelMultipleChoiceField(
        label=_("Invited suppliers"),
        queryset=Supplier.objects.filter(active=True),
        widget=forms.CheckboxSelectMultiple,
    )


class CurrencyForm(KeyForm):
    currency = forms.ChoiceField(label=_("Currency"), choices=Currency.choices)
    rate = forms.DecimalField(
        label=_("UZS per currency unit"),
        initial=Decimal(1),
        min_value=Decimal(".000001"),
        max_digits=15,
        decimal_places=6,
    )
    rate_date = forms.DateField(
        label=_("Rate date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )


class QuoteForm(CurrencyForm):
    supplier = forms.ModelChoiceField(
        label=_("Supplier"), queryset=Supplier.objects.filter(active=True)
    )
    lead_days = forms.IntegerField(label=_("Lead time in days"), min_value=0, max_value=3650)
    valid_from = forms.DateField(
        label=_("Valid from"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    valid_until = forms.DateField(
        label=_("Valid until"), widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    payment_terms = forms.CharField(label=_("Payment terms"), max_length=240)


class QuoteSelectForm(forms.Form):
    quote = forms.IntegerField(label=_("Quote number"), min_value=1)
    reason = forms.CharField(label=_("Selection reason"), max_length=300, required=False)


class OrderForm(CurrencyForm):
    supplier = forms.ModelChoiceField(
        label=_("Supplier"), queryset=Supplier.objects.filter(active=True)
    )
    expected_on = forms.DateField(
        label=_("Expected delivery"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    reason = forms.CharField(label=_("Amendment reason"), max_length=300, required=False)


class OrderRow(ItemQuantity):
    price = forms.DecimalField(
        label=_("Unit price"), min_value=Decimal(".01"), max_digits=15, decimal_places=2
    )


class ReceiptForm(KeyForm):
    delivered_on = forms.DateField(
        label=_("Actual delivery"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    delay_reason = forms.CharField(label=_("Delay reason"), max_length=300, required=False)
    photo = forms.FileField(
        label=_("Receipt photo"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png"}),
    )


class ReceiptRow(forms.Form):
    item = forms.CharField(
        label=_("Item code or barcode"),
        max_length=120,
        widget=forms.TextInput(attrs={"data-scan-field": "true"}),
    )
    location = forms.ModelChoiceField(
        label=_("Location"), queryset=Location.objects.filter(active=True)
    )
    quantity = forms.DecimalField(
        label=_("Quantity"), min_value=Decimal(".001"), max_digits=15, decimal_places=3
    )
    batch = forms.CharField(label=_("Batch number"), max_length=80)
    expiry = forms.DateField(
        label=_("Expiry date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    unplanned_price = forms.DecimalField(
        label=_("Unplanned unit price"),
        required=False,
        min_value=Decimal(".01"),
        max_digits=15,
        decimal_places=2,
    )
    unplanned_reason = forms.CharField(
        label=_("Unplanned receipt reason"), required=False, max_length=300
    )

    def clean_item(self) -> Item:
        return item_from_code(self.cleaned_data["item"])


class ReturnForm(KeyForm, NoteForm):
    quantity = forms.DecimalField(
        label=_("Quantity"), min_value=Decimal(".001"), max_digits=15, decimal_places=3
    )
    photo = forms.IntegerField(label=_("Receipt photo number"), min_value=1)


class InvoiceForm(CurrencyForm):
    number = forms.CharField(label=_("Invoice number"), max_length=80)
    invoice_date = forms.DateField(
        label=_("Invoice date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )


class InvoiceRow(forms.Form):
    receipt_line = forms.ModelChoiceField(
        label=_("Receipt line"), queryset=GoodsReceiptLine.objects.none()
    )
    quantity = forms.DecimalField(
        label=_("Quantity"), min_value=Decimal(".001"), max_digits=15, decimal_places=3
    )
    price = forms.DecimalField(
        label=_("Unit price"), min_value=Decimal(".01"), max_digits=15, decimal_places=2
    )

    def __init__(self, *args: Any, order_id: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["receipt_line"]
        assert isinstance(field, forms.ModelChoiceField)
        field.queryset = GoodsReceiptLine.objects.filter(receipt__order_id=order_id).select_related(
            "item", "receipt"
        )


class AttachmentForm(KeyForm):
    file = forms.FileField(
        label=_("Attachment"),
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.pdf"}),
    )


if TYPE_CHECKING:
    RuleBase = forms.ModelForm[ApprovalRule]
    DelegationBase = forms.ModelForm[ApprovalDelegation]
else:
    RuleBase = DelegationBase = forms.ModelForm


class RuleForm(RuleBase):
    class Meta:
        model = ApprovalRule
        fields = ("category", "minimum", "maximum", "level", "group", "active")
        labels = {
            "category": _("Category"),
            "minimum": _("Minimum amount"),
            "maximum": _("Exclusive maximum amount"),
            "level": _("Approval level"),
            "group": _("Approver group"),
            "active": _("Active"),
        }


class DelegationForm(DelegationBase):
    class Meta:
        model = ApprovalDelegation
        fields = ("principal", "delegate", "starts_on", "ends_on", "active")
        labels = {
            "principal": _("Principal approver"),
            "delegate": _("Delegate"),
            "starts_on": _("Starts on"),
            "ends_on": _("Ends on"),
            "active": _("Active"),
        }
        widgets = {
            field: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            for field in ("starts_on", "ends_on")
        }
