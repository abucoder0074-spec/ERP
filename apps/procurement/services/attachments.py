from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.module_loading import import_string
from django.utils.translation import gettext as _
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.inventory.services.common import audit, require
from apps.procurement.models import (
    Attachment,
    GoodsReceipt,
    PurchaseOrder,
    PurchaseRequest,
    SupplierInvoice,
)
from apps.procurement.services.common import identity, replay

OWNERS = {
    "request": (PurchaseRequest, "purchaserequest"),
    "order": (PurchaseOrder, "purchaseorder"),
    "receipt": (GoodsReceipt, "goodsreceipt"),
    "invoice": (SupplierInvoice, "supplierinvoice"),
}
TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


def no_op_scan(content: bytes, media_type: str) -> bool:
    """Default deployment hook. Replace with a real scanner in production."""
    return True


def validate_attachment(filename: str, content: bytes) -> str:
    """Validate type and bounded content independently of browser-supplied MIME."""
    path = PurePosixPath(filename)
    windows = PureWindowsPath(filename)
    if (
        len(filename) > 255
        or path.name != filename
        or windows.name != filename
        or windows.drive
        or filename in {".", ".."}
        or any(ord(char) < 32 for char in filename)
    ):
        raise ValidationError(_("Invalid attachment filename."))
    kind = TYPES.get(path.suffix.lower())
    if kind is None or not 0 < len(content) <= settings.PROCUREMENT_ATTACHMENT_LIMIT:
        raise ValidationError(_("Upload a JPEG, PNG or PDF within the attachment limit."))
    try:
        if kind == "application/pdf":
            if not content.startswith(b"%PDF-") or not content.rstrip().endswith(b"%%EOF"):
                raise ValueError
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted or not reader.pages:
                raise ValueError
        else:
            with Image.open(BytesIO(content), formats=["JPEG", "PNG"]) as image:
                expected = "JPEG" if kind == "image/jpeg" else "PNG"
                if image.format != expected or image.width * image.height > 16_000_000:
                    raise ValueError
                image.verify()
    except (
        PdfReadError,
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        RecursionError,
    ) as error:
        raise ValidationError(
            _("Attachment contents do not match the selected file type.")
        ) from error
    return kind


def owner_permission(actor: User, owner: str, *, write: bool = False) -> None:
    if owner not in OWNERS:
        raise PermissionDenied
    model = OWNERS[owner][1]
    require(actor, f"procurement.view_{model}")
    if write:
        require(actor, f"procurement.add_{model}")


@transaction.atomic
def attach(
    actor: User, key: UUID, owner: str, owner_id: int, filename: str, content: bytes
) -> Attachment:
    owner_permission(actor, owner, write=True)
    model = OWNERS[owner][0]
    model._default_manager.select_for_update().get(pk=owner_id)
    media = validate_attachment(filename, content)
    payload = {
        "owner": owner,
        "id": owner_id,
        "media": media,
        "digest": sha256(content).hexdigest(),
    }
    previous = replay(Attachment, actor, key, payload)
    if previous:
        return previous
    try:
        clean = import_string(settings.PROCUREMENT_SCAN_HOOK)(content, media)
    except Exception as error:
        raise ValidationError(_("The attachment scanner is unavailable.")) from error
    if clean is not True:
        raise ValidationError(_("The attachment failed the security scan."))
    attachment = Attachment.objects.create(
        **identity(actor, key, payload),
        **{f"{owner}_id": owner_id},
        storage_name=uuid4(),
        mime_type=media,
        size=len(content),
        content=content,
    )
    audit(
        actor,
        "attachment_add",
        attachment,
        {},
        {"owner": owner, "id": owner_id, "media": media, "size": len(content)},
    )
    return attachment


def read_attachment(actor: User, attachment_id: int) -> Attachment:
    attachment = Attachment.objects.defer("content").get(pk=attachment_id)
    owner = next(name for name in OWNERS if getattr(attachment, f"{name}_id") is not None)
    owner_permission(actor, owner)
    audit(actor, "attachment_read", attachment, {}, {})
    return attachment
