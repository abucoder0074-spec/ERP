from typing import Any

from django import forms
from django.contrib.auth.models import User
from django.db import transaction
from django.forms import model_to_dict

from apps.inventory.services.common import audit, require
from apps.procurement.forms import DelegationForm, RuleForm
from apps.procurement.models import ApprovalDelegation, ApprovalRule

POLICIES = {"rules": (ApprovalRule, RuleForm), "delegations": (ApprovalDelegation, DelegationForm)}


@transaction.atomic
def save_policy(actor: User, kind: str, pk: int, data: Any) -> tuple[forms.Form, bool]:
    """Lock and audit an approval rule or delegation configuration change."""
    model, cls = POLICIES[kind]
    require(actor, f"procurement.change_{model._meta.model_name}")
    record = model._default_manager.select_for_update().get(pk=pk) if pk else None
    before = model_to_dict(record) if record else {}
    form_type: Any = cls
    form = form_type(data, instance=record)
    if not form.is_valid():
        return form, False
    saved = form.save()
    audit(actor, "approval_policy", saved, before, model_to_dict(saved))
    return form, True
