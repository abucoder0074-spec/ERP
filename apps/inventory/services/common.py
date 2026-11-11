import json
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from hashlib import sha256
from typing import Any
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext as _

from apps.inventory.models import AuditLog, StockOperation
from apps.masterdata.models import Item, Location, UoMConversion

QTY = Decimal("0.001")
MONEY = Decimal("0.01")
VALUE = Decimal("0.00001")
MAX_QUANTITY = Decimal("999999999999.999")
MAX_COST = Decimal("9999999999999.99")


def require(actor: User, *permissions: str) -> None:
    if not actor.has_perms(permissions):
        raise PermissionDenied


def exact(value: Decimal, scale: Decimal, limit: Decimal) -> Decimal:
    """Validate finite fixed-point input without silently losing precision."""
    try:
        rounded = value.quantize(scale, rounding=ROUND_HALF_UP)
        if value.is_finite() and rounded == value and abs(rounded) <= limit:
            return rounded
    except InvalidOperation:
        pass
    raise ValidationError(_("Number exceeds the supported precision or range."))


def positive_quantity(item: Item, quantity: Decimal, unit_id: int | None) -> Decimal:
    """Convert to a positive quantity in the item's base unit."""
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError(_("Quantity must be positive."))
    if unit_id is not None and unit_id != item.base_unit_id:
        try:
            conversion = UoMConversion.objects.get(
                item=item, unit_id=unit_id, active=True, unit__active=True
            )
        except UoMConversion.DoesNotExist as error:
            raise ValidationError(_("No active conversion for this unit.")) from error
        with localcontext() as context:
            context.prec = 60
            quantity *= conversion.factor
    return exact(quantity, QTY, MAX_QUANTITY)


def lock_locations(ids: Iterable[int]) -> dict[int, Location]:
    return {
        row.pk: row
        for row in Location.objects.select_for_update().filter(pk__in=set(ids)).order_by("pk")
    }


def lock_items(ids: Iterable[int]) -> dict[int, Item]:
    return {
        row.pk: row
        for row in Item.objects.select_for_update().filter(pk__in=set(ids)).order_by("pk")
    }


def audit(
    actor: User, action: str, subject: models.Model, before: dict[str, Any], after: dict[str, Any]
) -> None:
    AuditLog.objects.create(
        actor=actor,
        action=action,
        subject=f"{subject._meta.label_lower}:{subject.pk}",
        before=json.loads(json.dumps(before, cls=DjangoJSONEncoder)),
        after=json.loads(json.dumps(after, cls=DjangoJSONEncoder)),
    )


def posting_fingerprint(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest()


def begin(
    actor: User,
    key: UUID,
    kind: str,
    payload: dict[str, Any],
    *,
    reverses: StockOperation | None = None,
) -> tuple[StockOperation, bool]:
    """Claim a request key before posting, with retries bound to the original actor and payload."""
    fingerprint = posting_fingerprint(payload)
    operation, created = StockOperation.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "created_by": actor,
            "kind": kind,
            "fingerprint": fingerprint,
            "reverses": reverses,
        },
    )
    if not created and (
        operation.created_by_id != actor.pk
        or operation.kind != kind
        or operation.fingerprint != fingerprint
    ):
        raise ValidationError(_("This posting key is already used for a different request."))
    return operation, created
