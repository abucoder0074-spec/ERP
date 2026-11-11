from django.db import migrations

FIELDS = {
    "purchaserequest": ("state",),
    "purchaserequestline": (),
    "rfq": ("state", "selected_id", "selection_reason"),
    "rfqsupplier": (),
    "quote": (),
    "quoteline": (),
    "supplieritemprice": (),
    "purchaseorder": ("state", "active_version_id", "cancellation_reason"),
    "purchaseorderversion": ("state",),
    "purchaseorderline": (),
    "goodsreceipt": (),
    "goodsreceiptline": (),
    "supplierreturn": (),
    "supplierinvoice": ("state", "override_reason", "cancellation_reason"),
    "supplierinvoiceline": (),
    "attachment": (),
    "approvalcase": (),
    "approvalstep": ("state", "actor_id", "principal_id", "comment", "decided_at"),
    "approvalrule": ("*",),
    "approvaldelegation": ("*",),
}

CREATE = """
CREATE FUNCTION procurement_protect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Protected procurement document' USING ERRCODE = '23514';
    END IF;
    IF TG_ARGV[0] = '*' THEN
        RETURN NEW;
    END IF;
    IF TG_NARGS = 0
        OR (TG_TABLE_NAME = 'procurement_approvalstep' AND to_jsonb(OLD)->>'state' <> 'submitted')
        OR (to_jsonb(OLD) - TG_ARGV) IS DISTINCT FROM (to_jsonb(NEW) - TG_ARGV)
    THEN
        RAISE EXCEPTION 'Protected procurement document' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
""" + "".join(
    f"CREATE TRIGGER procurement_protect BEFORE UPDATE OR DELETE ON procurement_{table} "
    "FOR EACH ROW EXECUTE FUNCTION "
    f"procurement_protect({', '.join(repr(field) for field in fields)});"
    for table, fields in FIELDS.items()
)

DROP = (
    "".join(f"DROP TRIGGER procurement_protect ON procurement_{table};" for table in FIELDS)
    + "DROP FUNCTION procurement_protect();"
)


class Migration(migrations.Migration):
    dependencies = [("procurement", "0002_roles")]
    operations = [migrations.RunSQL(CREATE, DROP)]
