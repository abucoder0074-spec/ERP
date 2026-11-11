from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.services.common import (
    MAX_COST,
    MAX_QUANTITY,
    MONEY,
    QTY,
    exact,
    posting_fingerprint,
)
from apps.procurement.models import Currency, Document, State

TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "request": {
        State.DRAFT: {State.SUBMITTED},
        State.SUBMITTED: {State.APPROVED, State.REJECTED},
        State.APPROVED: {State.CONVERTED},
    },
    "rfq": {State.OPEN: {State.SELECTED, State.CANCELLED}},
    "order": {
        State.SUBMITTED: {State.ACTIVE, State.REJECTED, State.CANCELLED},
        State.ACTIVE: {State.CANCELLED},
        State.REJECTED: {State.CANCELLED, State.SUBMITTED},
    },
    "version": {
        State.SUBMITTED: {State.ACTIVE, State.REJECTED, State.CANCELLED},
        State.ACTIVE: {State.SUPERSEDED},
    },
    "step": {State.SUBMITTED: {State.APPROVED, State.REJECTED}},
    "invoice": {State.MISMATCH: {State.READY, State.CANCELLED}, State.READY: {State.CANCELLED}},
}


def check_transition(kind: str, before: str, after: str) -> None:
    if after not in TRANSITIONS[kind].get(before, set()):
        raise ValidationError(_("This document cannot move to the requested state."))


def replay[D: Document](
    model: type[D], actor: User, key: UUID, payload: dict[str, Any]
) -> D | None:
    previous = model._default_manager.filter(key=key).first()
    if previous and (
        previous.created_by_id != actor.pk or previous.fingerprint != posting_fingerprint(payload)
    ):
        raise ValidationError(_("This key belongs to a different procurement request."))
    return previous


def identity(actor: User, key: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "created_by": actor, "fingerprint": posting_fingerprint(payload)}


def amount(value: Decimal, *, quantity: bool = False) -> Decimal:
    result = exact(value, QTY if quantity else MONEY, MAX_QUANTITY if quantity else MAX_COST)
    if result <= 0:
        raise ValidationError(_("Enter a positive quantity or price."))
    return result


def money(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        rounded = value.quantize(MONEY, rounding=ROUND_HALF_UP)
    if not rounded.is_finite() or abs(rounded) > Decimal("99999999999999999999999.99"):
        raise ValidationError(_("This total is outside the supported money range."))
    return rounded


def fx(currency: str, rate: Decimal, on: date) -> Decimal:
    value = exact(rate, Decimal("0.000001"), Decimal("999999999.999999"))
    if (
        currency not in Currency.values
        or value <= 0
        or on > timezone.localdate()
        or (currency == Currency.UZS and value != 1)
    ):
        raise ValidationError(_("Enter a valid currency, exchange rate and rate date."))
    return value


def note(value: str, length: int, *, required: bool = True) -> str:
    value = value.strip()
    if len(value) > length or (required and not value):
        raise ValidationError(_("Enter a value within the allowed length."))
    return value
