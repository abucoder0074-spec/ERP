from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.services.common import audit
from apps.procurement.models import (
    ApprovalCase,
    ApprovalDelegation,
    ApprovalRule,
    ApprovalStep,
    PurchaseOrder,
    PurchaseOrderVersion,
    PurchaseRequest,
    State,
)
from apps.procurement.services.common import check_transition, note


def make_case(
    actor: User,
    amount: Decimal,
    categories: set[int],
    *,
    request: PurchaseRequest | None = None,
    version: PurchaseOrderVersion | None = None,
) -> ApprovalCase:
    """Snapshot matching group assignments and order them by level and group."""
    matching = list(
        ApprovalRule.objects.filter(active=True, minimum__lte=amount)
        .filter(Q(maximum__isnull=True) | Q(maximum__gt=amount))
        .filter(Q(category__isnull=True) | Q(category_id__in=categories))
    )
    general = [rule for rule in matching if rule.category_id is None]
    assignments: set[tuple[int, int]] = set()
    for category in categories:
        rules = [rule for rule in matching if rule.category_id == category] or general
        if not rules:
            raise ValidationError(_("No approval rule covers this amount and category."))
        assignments.update((rule.level, rule.group_id) for rule in rules)
    if not assignments:
        raise ValidationError(_("An approval requires at least one assignment."))
    case = ApprovalCase.objects.create(
        request=request, version=version, requester=actor, amount=amount
    )
    ApprovalStep.objects.bulk_create(
        ApprovalStep(case=case, sequence=index, level=level, group_id=group)
        for index, (level, group) in enumerate(sorted(assignments), start=1)
    )
    return case


def eligible_principal(actor: User, step: ApprovalStep, case: ApprovalCase) -> int | None:
    """Resolve a direct or currently delegated decision identity without chaining."""
    if not actor.is_active or actor.pk == case.requester_id:
        return None
    if actor.is_superuser or (
        actor.has_perm("procurement.approve_procurement")
        and actor.groups.filter(pk=step.group_id).exists()
    ):
        return actor.pk
    if not actor.has_perm("procurement.act_as_delegate"):
        return None
    today = timezone.localdate()
    for delegation in (
        ApprovalDelegation.objects.filter(
            delegate=actor,
            active=True,
            principal__is_active=True,
            principal__groups=step.group,
            starts_on__lte=today,
            ends_on__gte=today,
        )
        .exclude(principal_id=case.requester_id)
        .select_related("principal")
        .order_by("pk")
    ):
        if delegation.principal.has_perm("procurement.approve_procurement"):
            return delegation.principal_id
    return None


@transaction.atomic
def decide(actor: User, step_id: int, *, approve: bool, comment: str = "") -> ApprovalStep:
    """Apply an idempotent decision usable from both web and future Telegram handlers."""
    from apps.procurement.services.orders import finish_version

    target = ApprovalStep.objects.select_related("case__version").get(pk=step_id)
    case = target.case
    request = (
        PurchaseRequest.objects.select_for_update().get(pk=case.request_id)
        if case.request_id
        else None
    )
    order = (
        PurchaseOrder.objects.select_for_update().get(pk=case.version.order_id)
        if case.version
        else None
    )
    step = ApprovalStep.objects.select_for_update().get(pk=step_id)
    status = State.APPROVED if approve else State.REJECTED
    comment = note(comment, 300, required=not approve)
    if (step.actor_id, step.state, step.comment) == (actor.pk, status, comment):
        return step
    current_state = (
        request.state
        if request
        else PurchaseOrderVersion.objects.get(pk=case.version_id or 0).state
    )
    if current_state != State.SUBMITTED:
        raise ValidationError(_("This approval is closed."))
    check_transition("step", step.state, status)
    if case.steps.filter(sequence__lt=step.sequence).exclude(state=State.APPROVED).exists():
        raise ValidationError(_("Earlier approval steps must finish first."))
    principal = eligible_principal(actor, step, case)
    if principal is None:
        raise PermissionDenied
    if case.steps.filter(
        Q(actor_id__in=(actor.pk, principal)) | Q(principal_id__in=(actor.pk, principal))
    ).exists():
        raise ValidationError(_("A decision maker cannot approve more than one step."))
    step.state, step.actor, step.principal_id, step.comment, step.decided_at = (
        status,
        actor,
        principal,
        comment,
        timezone.now(),
    )
    step.save(update_fields=["state", "actor", "principal", "comment", "decided_at"])
    audit(
        actor,
        "approval_decision",
        step,
        {"state": State.SUBMITTED},
        {"state": status, "principal": principal, "comment": comment},
    )
    if not approve or not case.steps.filter(state=State.SUBMITTED).exists():
        if request:
            check_transition("request", request.state, status)
            request.state = status
            request.save(update_fields=["state"])
            audit(actor, "request_decision", request, {"state": State.SUBMITTED}, {"state": status})
        elif order and case.version_id:
            finish_version(actor, order, case.version_id, approve)
    return step
