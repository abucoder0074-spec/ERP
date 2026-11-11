from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

ROLES = (
    "Admin",
    "Procurement Manager",
    "Procurement Specialist",
    "Warehouse Keeper",
    "Accountant",
    "Production Planner",
    "Viewer",
)
CATALOG = (
    "item",
    "itemcategory",
    "unitofmeasure",
    "uomconversion",
    "supplier",
    "warehouse",
    "location",
)
OPERATIONS = {
    "receive_stock",
    "issue_stock",
    "transfer_stock",
    "write_off_stock",
    "return_stock",
}
APPROVALS = {"large_write_off", "override_fefo", "reverse_stock", "import_opening"}
READ = ("stockbalance", "stockmovement", "stockoperation", "batch", "stocktake", "notification")


def grants(role: str) -> set[tuple[str, str, str]]:
    result = {("masterdata", model, f"view_{model}") for model in CATALOG}
    result |= {("inventory", model, f"view_{model}") for model in READ}
    if role in {"Admin", "Procurement Manager", "Procurement Specialist"}:
        models = ("supplier", "item") if role == "Procurement Specialist" else CATALOG
        result |= {
            ("masterdata", model, f"{action}_{model}")
            for model in models
            for action in ("add", "change")
        }
    if role in {"Warehouse Keeper", "Procurement Manager", "Admin"}:
        result |= {("inventory", "stockoperation", code) for code in OPERATIONS}
        result.add(("inventory", "stocktake", "count_stocktake"))
    if role in {"Procurement Manager", "Admin"}:
        result |= {("inventory", "stockoperation", code) for code in APPROVALS}
        result |= {
            ("inventory", "stocktake", "approve_stocktake"),
            ("inventory", "batch", "change_batch"),
            ("inventory", "auditlog", "view_auditlog"),
        }
    return result


def apply(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    alias = schema_editor.connection.alias
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(alias)
    permissions = apps.get_model("auth", "Permission").objects.using(alias)
    groups = apps.get_model("auth", "Group").objects.using(alias)
    for role in ROLES:
        group, created = groups.get_or_create(name=role)
        if created:
            workspace = permissions.get(content_type__app_label="core", codename="access_workspace")
            group.permissions.add(workspace)
        for label, model, code in sorted(grants(role)):
            content, _ = content_types.get_or_create(app_label=label, model=model)
            permission, _ = permissions.get_or_create(
                content_type=content,
                codename=code,
                defaults={"name": code.replace("_", " ").capitalize()},
            )
            group.permissions.add(permission)


def unapply(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    alias = schema_editor.connection.alias
    groups = apps.get_model("auth", "Group").objects.using(alias)
    for group in groups.filter(name__in=ROLES):
        for label in ("inventory", "masterdata"):
            codes = {code for app_label, _, code in grants(group.name) if app_label == label}
            group.permissions.remove(
                *group.permissions.filter(content_type__app_label=label, codename__in=codes)
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_workspace_permission"),
        ("inventory", "0002_guards"),
    ]
    operations = [migrations.RunPython(apply, unapply)]
