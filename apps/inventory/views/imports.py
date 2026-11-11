from typing import cast
from uuid import uuid4

from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.inventory.services.common import require
from apps.inventory.services.imports import (
    COLUMNS,
    REQUIRED,
    import_permissions,
    import_records,
)
from apps.inventory.uploads import MAX_IMPORT_BYTES
from apps.inventory.views import workspace


class ImportForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    file = forms.FileField(
        label=_("CSV or Excel file"),
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx"}),
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def upload(request: HttpRequest, kind: str) -> HttpResponse:
    if kind not in COLUMNS:
        raise Http404
    actor = cast(User, request.user)
    require(actor, *import_permissions(kind))
    form = ImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        file = form.cleaned_data["file"]
        if file.size > MAX_IMPORT_BYTES:
            form.add_error("file", _("Upload a nonempty file up to 2 MB."))
        else:
            try:
                result = import_records(
                    actor,
                    kind,
                    file.name,
                    file.read(MAX_IMPORT_BYTES + 1),
                    form.cleaned_data["key"],
                    dry_run=request.POST.get("action") != "commit",
                )
            except ValidationError as error:
                form.add_error("file", error)
    if result is not None and result.committed:
        form = ImportForm()
    return workspace(
        request,
        "inventory/import.html",
        form=form,
        result=result,
        columns=COLUMNS[kind],
        required=sorted(REQUIRED[kind]),
    )
