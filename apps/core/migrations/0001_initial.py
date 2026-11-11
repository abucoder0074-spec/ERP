from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="LoginWindow",
            fields=[
                (
                    "client_key",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
            ],
            options={
                "default_permissions": (),
                "permissions": [("access_workspace", "Can access ERP workspace")],
            },
        )
    ]
