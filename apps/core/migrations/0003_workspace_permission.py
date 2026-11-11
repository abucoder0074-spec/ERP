from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def move_permission(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor, old: str, new: str
) -> None:
    alias = schema_editor.connection.alias
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(alias)
    target, _ = content_types.get_or_create(app_label="core", model=new)
    apps.get_model("auth", "Permission").objects.using(alias).filter(
        content_type__app_label="core",
        content_type__model=old,
        codename="access_workspace",
    ).update(content_type=target)


def forward(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    move_permission(apps, schema_editor, "loginwindow", "workspace")


def backward(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    move_permission(apps, schema_editor, "workspace", "loginwindow")


class Migration(migrations.Migration):
    dependencies = [("core", "0002_workspace_roles")]
    operations = [
        migrations.CreateModel(
            name="Workspace",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                )
            ],
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": [("access_workspace", "Can access ERP workspace")],
            },
        ),
        migrations.RunPython(forward, backward),
        migrations.DeleteModel(name="LoginWindow"),
    ]
