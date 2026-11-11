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


def grant_workspace(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    alias = schema_editor.connection.alias
    content_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        .objects.using(alias)
        .get_or_create(app_label="core", model="loginwindow")
    )
    permission, _ = (
        apps.get_model("auth", "Permission")
        .objects.using(alias)
        .get_or_create(
            content_type=content_type,
            codename="access_workspace",
            defaults={"name": "Can access ERP workspace"},
        )
    )
    groups = apps.get_model("auth", "Group").objects.using(alias)
    for role in ROLES:
        group, _ = groups.get_or_create(name=role)
        group.permissions.add(permission)


def revoke_workspace(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    alias = schema_editor.connection.alias
    apps.get_model("auth", "Permission").objects.using(alias).filter(
        content_type__app_label="core",
        content_type__model="loginwindow",
        codename="access_workspace",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("core", "0001_initial"),
    ]
    operations = [migrations.RunPython(grant_workspace, revoke_workspace)]
