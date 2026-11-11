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
READ = (
    "purchaserequest",
    "approvalstep",
    "rfq",
    "quote",
    "purchaseorder",
    "purchaseorderversion",
    "goodsreceipt",
    "supplierinvoice",
    "supplierreturn",
)


def grants(role: str) -> set[tuple[str, str]]:
    result = {(model, f"view_{model}") for model in READ}
    if role in {"Procurement Specialist", "Procurement Manager", "Admin"}:
        result |= {
            (model, f"add_{model}")
            for model in ("purchaserequest", "rfq", "quote", "purchaseorder")
        }
        result.add(("purchaseorder", "change_purchaseorder"))
        result.add(("purchaseorder", "act_as_delegate"))
    if role in {"Procurement Manager", "Admin"}:
        result |= {
            ("purchaseorder", code)
            for code in ("approve_procurement", "cancel_order", "receive_unplanned")
        }
    if role in {"Warehouse Keeper", "Procurement Manager", "Admin"}:
        result |= {(model, f"add_{model}") for model in ("goodsreceipt", "supplierreturn")}
    if role in {"Accountant", "Admin"}:
        result |= {("supplierinvoice", "add_supplierinvoice"), ("purchaseorder", "override_match")}
    if role == "Admin":
        result |= {(model, f"change_{model}") for model in ("approvalrule", "approvaldelegation")}
    return result


def apply(apps: Apps, editor: BaseDatabaseSchemaEditor) -> None:
    alias = editor.connection.alias
    groups = apps.get_model("auth", "Group").objects.using(alias)
    permissions = apps.get_model("auth", "Permission").objects.using(alias)
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(alias)
    for role in ROLES:
        group, _ = groups.get_or_create(name=role)
        for model, code in sorted(grants(role)):
            content, _ = content_types.get_or_create(app_label="procurement", model=model)
            permission, _ = permissions.get_or_create(
                content_type=content,
                codename=code,
                defaults={"name": code.replace("_", " ").capitalize()},
            )
            group.permissions.add(permission)
    manager = groups.get(name="Procurement Manager")
    apps.get_model("procurement", "ApprovalRule").objects.using(alias).get_or_create(
        category=None,
        minimum=0,
        maximum=None,
        level=1,
        group=manager,
    )


def unapply(apps: Apps, editor: BaseDatabaseSchemaEditor) -> None:
    alias = editor.connection.alias
    groups = apps.get_model("auth", "Group").objects.using(alias)
    for group in groups.filter(name__in=ROLES):
        codes = {code for _, code in grants(group.name)}
        group.permissions.remove(
            *group.permissions.filter(content_type__app_label="procurement", codename__in=codes)
        )
    apps.get_model("procurement", "ApprovalRule").objects.using(alias).filter(
        category__isnull=True,
        minimum=0,
        maximum__isnull=True,
        level=1,
        group__name="Procurement Manager",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("procurement", "0001_initial"), ("inventory", "0003_role_grants")]
    operations = [migrations.RunPython(apply, unapply)]
