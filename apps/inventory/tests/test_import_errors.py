from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.inventory.services.imports import import_records, read_rows
from apps.inventory.tests.conftest import Setup
from apps.masterdata.models import Supplier
from apps.masterdata.services import save_record

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("empty.csv", b"", "nonempty"),
        ("bad.pdf", b"example", "CSV or XLSX"),
        ("bad.csv", b"\xff", "could not be read"),
        ("huge.csv", b"x\n" * 502, "500 rows"),
    ],
)
def test_import_parser_rejects_invalid_files(filename: str, content: bytes, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        read_rows(filename, content)


def test_import_reports_bad_headers_formulas_duplicates_and_codes(stock: Setup) -> None:
    key = uuid4()
    with pytest.raises(ValidationError, match="header"):
        import_records(stock.actor, "suppliers", "data.csv", b"other\nfoo\n", key, dry_run=True)
    with pytest.raises(ValidationError, match="no data"):
        import_records(stock.actor, "suppliers", "data.csv", b"code,name\n\n", key, dry_run=True)
    content = b"code,name\nA,Yangi\nA,Ikkinchi\nB,=1+1\n"
    result = import_records(stock.actor, "suppliers", "data.csv", content, key, dry_run=True)
    assert [error.row for error in result.errors] == [3, 4]
    assert result.rows == 3
    unknown = b"code,name_uz,category,base_unit,storage\nNEW,Yangi,UNKNOWN,KG,chilled\n"
    result = import_records(stock.actor, "items", "data.csv", unknown, key, dry_run=True)
    assert result.errors[0].row == 2


def test_import_rolls_back_when_commit_fails_after_first_row(stock: Setup) -> None:
    def fail_second(actor: object, slug: str, data: dict[str, str], pk: int | None) -> object:
        if data["code"] == "SECOND":
            raise IntegrityError("concurrent uniqueness conflict")
        assert actor == stock.actor
        return save_record(stock.actor, slug, data, pk)

    with patch("apps.inventory.services.imports.save_record", side_effect=fail_second):
        result = import_records(
            stock.actor,
            "suppliers",
            "data.csv",
            b"code,name\nFIRST,Yangi\nSECOND,Ikkinchi\n",
            uuid4(),
            dry_run=False,
        )
    assert not result.committed and result.errors[0].row == 3
    assert not Supplier.objects.filter(code__in=["FIRST", "SECOND"]).exists()
