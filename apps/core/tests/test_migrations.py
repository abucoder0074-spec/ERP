import pytest
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core.tests.test_views import ROLES


@pytest.mark.django_db(transaction=True)
def test_role_migration_preserves_grants_on_upgrade_and_rollback() -> None:
    latest = [("core", "0003_workspace_permission")]
    previous = [("core", "0002_workspace_roles")]
    try:
        MigrationExecutor(connection).migrate([("core", None)])
        assert not Permission.objects.filter(codename="access_workspace").exists()
        MigrationExecutor(connection).migrate(previous)
        assert "core_loginwindow" in connection.introspection.table_names()
        permission = Permission.objects.get(codename="access_workspace")
        assert permission.content_type.model == "loginwindow"
        assert set(Group.objects.filter(permissions=permission).values_list("name", flat=True)) == (
            set(ROLES)
        )
        MigrationExecutor(connection).migrate(latest)
        permission.refresh_from_db()
        assert permission.content_type.model == "workspace"
        assert Group.objects.filter(permissions=permission).count() == len(ROLES)
        assert "core_loginwindow" not in connection.introspection.table_names()
        MigrationExecutor(connection).migrate(previous)
        permission.refresh_from_db()
        assert permission.content_type.model == "loginwindow"
        assert Group.objects.filter(permissions=permission).count() == len(ROLES)
    finally:
        MigrationExecutor(connection).migrate(latest)
