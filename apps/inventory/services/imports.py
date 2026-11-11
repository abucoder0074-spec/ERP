from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from typing import Any
from uuid import UUID, uuid5
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from apps.inventory.models import Batch, MovementType, Quality, StockOperation, WarehouseCost
from apps.inventory.services.common import (
    MAX_COST,
    MAX_QUANTITY,
    MONEY,
    QTY,
    exact,
    lock_items,
    lock_locations,
    positive_quantity,
    posting_fingerprint,
    require,
)
from apps.inventory.services.posting import Receipt, compatible, receive_goods
from apps.inventory.uploads import MAX_IMPORT_BYTES
from apps.masterdata.forms import CATALOGS
from apps.masterdata.models import Item, ItemCategory, Location, Supplier, UnitOfMeasure, Warehouse
from apps.masterdata.services import save_record, validate_record

COLUMNS = {
    "suppliers": ("code", "name", "tin", "lead_time_days"),
    "items": (
        "code",
        "name_uz",
        "name_ru",
        "name_en",
        "category",
        "base_unit",
        "storage",
        "min_stock",
        "max_stock",
        "reorder_qty",
        "shelf_life_days",
        "min_temperature",
        "max_temperature",
        "barcode",
    ),
    "opening": (
        "item",
        "location",
        "supplier",
        "batch_number",
        "quantity",
        "unit_cost",
        "reference",
        "expiry",
    ),
}
REQUIRED = {
    "suppliers": {"code", "name"},
    "items": {"code", "name_uz", "category", "base_unit", "storage"},
    "opening": set(COLUMNS["opening"]) - {"expiry"},
}
MAX_ROWS = 500


@dataclass(frozen=True)
class RowError:
    row: int
    messages: list[str]


@dataclass
class ImportResult:
    rows: int
    errors: list[RowError]
    committed: bool = False


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip()


def read_rows(filename: str, content: bytes) -> list[list[str]]:
    """Read a bounded UTF-8 CSV or single-sheet XLSX without evaluating formulas."""
    if not content or len(content) > MAX_IMPORT_BYTES:
        raise ValidationError(_("Upload a nonempty file up to 2 MB."))
    try:
        if filename.lower().endswith(".csv"):
            reader = csv.reader(StringIO(content.decode("utf-8-sig")), strict=True)
            rows = []
            for row in reader:
                rows.append([value.strip() for value in row])
                if len(rows) > MAX_ROWS + 1:
                    raise ValidationError(_("The import supports up to 500 rows."))
            return rows
        if filename.lower().endswith(".xlsx"):
            with ZipFile(BytesIO(content)) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 20 * MAX_IMPORT_BYTES:
                    raise ValidationError(_("The Excel file expands beyond the supported size."))
            workbook = load_workbook(
                BytesIO(content), read_only=True, data_only=False, keep_links=False
            )
            try:
                if len(workbook.worksheets) != 1:
                    raise ValidationError(_("Use an Excel workbook with one sheet."))
                sheet = workbook.worksheets[0]
                if (sheet.max_column or 0) > 30:
                    raise ValidationError(_("The file has too many columns."))
                rows = []
                for index, cells in enumerate(sheet.iter_rows(max_col=30), start=1):
                    if index > MAX_ROWS + 1:
                        raise ValidationError(_("The import supports up to 500 rows."))
                    values = [_cell(cell.value) for cell in cells]
                    while values and not values[-1]:
                        values.pop()
                    rows.append(values)
                return rows
            finally:
                workbook.close()
    except (
        UnicodeError,
        csv.Error,
        BadZipFile,
        InvalidFileException,
        ParseError,
        DefusedXmlException,
        KeyError,
        OSError,
        ValueError,
    ) as error:
        raise ValidationError(_("The import file could not be read.")) from error
    raise ValidationError(_("Choose a CSV or XLSX file."))


def _records(
    kind: str, filename: str, content: bytes
) -> tuple[list[tuple[int, dict[str, str]]], list[RowError]]:
    raw = read_rows(filename, content)
    if not raw:
        raise ValidationError(_("The file has no header."))
    header = raw[0]
    if (
        len(header) != len(set(header))
        or set(header) - set(COLUMNS[kind])
        or not REQUIRED[kind] <= set(header)
    ):
        raise ValidationError(_("The header contains missing, duplicate or unknown columns."))
    rows = []
    errors = []
    for index, values in enumerate(raw[1:], start=2):
        if not any(values):
            continue
        if len(values) > len(header) or any(value.startswith("=") for value in values):
            errors.append(RowError(index, [_("Unexpected columns or formulas are not supported.")]))
            continue
        rows.append((index, dict(zip(header, values, strict=False))))
    if not rows and not errors:
        raise ValidationError(_("The file contains no data rows."))
    return rows, errors


def import_permissions(kind: str) -> tuple[str, ...]:
    if kind == "opening":
        return ("inventory.import_opening", "inventory.receive_stock")
    model = CATALOGS[kind][0]._meta.model_name
    return (f"masterdata.add_{model}", f"masterdata.change_{model}")


def _master_data(kind: str, data: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {**data, "active": True}
    if kind == "suppliers":
        values["lead_time_days"] = values.get("lead_time_days") or "0"
    if kind == "items":
        values["category"] = ItemCategory.objects.get(code=data.get("category"), active=True).pk
        values["base_unit"] = UnitOfMeasure.objects.get(code=data.get("base_unit"), active=True).pk
        values["min_stock"] = values.get("min_stock") or "0"
        values["reorder_qty"] = values.get("reorder_qty") or "0"
    return values


def _opening(data: dict[str, str]) -> tuple[Receipt, int, Batch | None]:
    item = Item.objects.get(code=data.get("item"), active=True)
    location = Location.objects.get(code=data.get("location"), active=True)
    supplier = Supplier.objects.get(code=data.get("supplier"), active=True)
    compatible(item, location)
    quantity = positive_quantity(item, Decimal(data.get("quantity", "")), None)
    cost = exact(Decimal(data.get("unit_cost", "")), MONEY, MAX_COST)
    expiry_value = data.get("expiry")
    expiry = date.fromisoformat(expiry_value) if expiry_value else None
    number, reference = data.get("batch_number", ""), data.get("reference", "")
    if not (0 < len(number) <= 80 and 0 < len(reference) <= 120 and cost >= 0):
        raise ValidationError(_("Enter a batch, receipt reference and nonnegative cost."))
    existing = Batch.objects.filter(item=item, number=number).first()
    if existing and (existing.supplier_id, existing.expiry, existing.receipt_reference) != (
        supplier.pk,
        expiry,
        reference,
    ):
        raise ValidationError(_("This batch number already has different receipt details."))
    receipt = Receipt(item.pk, location.pk, supplier.pk, number, quantity, cost, reference, expiry)
    return receipt, location.warehouse_id, existing


def _check_opening(
    actor: User,
    key: UUID,
    receipt: Receipt,
    warehouse_id: int,
    lot: Batch | None,
    projected: dict[tuple[int, int], Decimal],
) -> None:
    operation = StockOperation.objects.filter(idempotency_key=key).first()
    if operation:
        if (
            operation.kind != MovementType.RECEIPT
            or operation.created_by_id != actor.pk
            or operation.fingerprint != posting_fingerprint(vars(receipt))
        ):
            raise ValidationError(_("This posting key is already used for a different request."))
        return
    if lot and lot.quality != Quality.QUARANTINE:
        raise ValidationError(_("Only quarantined batches can accept further receipts."))
    position = (receipt.item_id, warehouse_id)
    if position not in projected:
        quantity = (
            WarehouseCost.objects.filter(item_id=receipt.item_id, warehouse_id=warehouse_id)
            .values_list("quantity", flat=True)
            .first()
        )
        projected[position] = quantity or Decimal(0)
    projected[position] = exact(projected[position] + receipt.quantity, QTY, MAX_QUANTITY)


def import_records(
    actor: User, kind: str, filename: str, content: bytes, key: UUID, *, dry_run: bool
) -> ImportResult:
    """Validate a complete import first. Any commit-time failure rolls the entire file back."""
    require(actor, *import_permissions(kind))
    records, errors = _records(kind, filename, content)
    prepared: list[tuple[int, Any, int | None]] = []
    seen: set[str] = set()
    barcodes: set[str] = set()
    lots: dict[tuple[int, str], tuple[int, date | None, str]] = {}
    projected: dict[tuple[int, int], Decimal] = {}
    for index, data in records:
        try:
            if kind == "opening":
                receipt, warehouse_id, lot = _opening(data)
                lot_key = (receipt.item_id, receipt.batch_number)
                detail = (receipt.supplier_id, receipt.expiry, receipt.reference)
                if lots.setdefault(lot_key, detail) != detail:
                    raise ValidationError(
                        _("This batch number already has different receipt details.")
                    )
                _check_opening(
                    actor, uuid5(key, f"opening:{index}"), receipt, warehouse_id, lot, projected
                )
                prepared.append((index, receipt, None))
            else:
                code, barcode = data.get("code", ""), data.get("barcode", "")
                if code in seen or (barcode and barcode in barcodes):
                    raise ValidationError(_("Duplicate code or barcode in this file."))
                seen.add(code)
                if barcode:
                    barcodes.add(barcode)
                instance = (
                    Item.objects.filter(code=code).first()
                    if kind == "items"
                    else Supplier.objects.filter(code=code).first()
                )
                pk = instance.pk if instance is not None else None
                values = _master_data(kind, data)
                form = validate_record(kind, values, instance)
                if not form.is_valid() or form.errors:
                    raise ValidationError(
                        [str(message) for messages in form.errors.values() for message in messages]
                    )
                prepared.append((index, values, pk))
        except ValidationError as error:
            errors.append(RowError(index, error.messages))
        except (
            Item.DoesNotExist,
            Location.DoesNotExist,
            Supplier.DoesNotExist,
            ItemCategory.DoesNotExist,
            UnitOfMeasure.DoesNotExist,
            InvalidOperation,
            ValueError,
        ):
            errors.append(RowError(index, [_("Check referenced codes, amounts and dates.")]))
    result = ImportResult(
        len(records) + len({error.row for error in errors} - {index for index, _ in records}),
        sorted(errors, key=lambda error: error.row),
    )
    if dry_run or errors:
        return result
    index = 0
    try:
        with transaction.atomic():
            if kind == "opening":
                locations = lock_locations(row.location_id for _, row, _ in prepared)
                lock_items(row.item_id for _, row, _ in prepared)
                warehouses = {location.warehouse_id for location in locations.values()}
                suppliers = {row.supplier_id for _, row, _ in prepared}
                list(Warehouse.objects.select_for_update().filter(pk__in=warehouses).order_by("pk"))
                list(Supplier.objects.select_for_update().filter(pk__in=suppliers).order_by("pk"))
            else:
                prepared.sort(key=lambda record: record[1]["code"])
            for index, row, pk in prepared:
                if kind == "opening":
                    receive_goods(actor, uuid5(key, f"opening:{index}"), row)
                else:
                    form, saved = save_record(actor, kind, row, pk)
                    if saved is None:
                        raise ValidationError(
                            [
                                str(message)
                                for messages in form.errors.values()
                                for message in messages
                            ]
                        )
    except ValidationError as error:
        result.errors.append(RowError(index, error.messages))
    except IntegrityError:
        result.errors.append(RowError(index, [_("The record conflicts with an existing record.")]))
    else:
        result.committed = True
    return result
