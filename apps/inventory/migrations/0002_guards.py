from django.db import migrations

IMMUTABLE = ("inventory_stockmovement", "inventory_stockoperation", "inventory_auditlog")
RETAINED = (
    "inventory_batch",
    "inventory_stocktake",
    "inventory_stocktakeline",
    "masterdata_item",
    "masterdata_itemcategory",
    "masterdata_location",
    "masterdata_supplier",
    "masterdata_unitofmeasure",
    "masterdata_uomconversion",
    "masterdata_warehouse",
)

CREATE = (
    """
CREATE FUNCTION phase1_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Protected ERP record' USING ERRCODE = '23514';
END;
$$;
"""
    + "".join(
        f"CREATE TRIGGER phase1_protect BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION phase1_reject_mutation();"
        for table in IMMUTABLE
    )
    + "".join(
        f"CREATE TRIGGER phase1_protect BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION phase1_reject_mutation();"
        for table in RETAINED
    )
)

DROP = (
    "".join(f"DROP TRIGGER phase1_protect ON {table};" for table in (*IMMUTABLE, *RETAINED))
    + "DROP FUNCTION phase1_reject_mutation();"
)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [migrations.RunSQL(CREATE, DROP)]
