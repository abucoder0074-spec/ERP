import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_phase1_migration_roundtrip() -> None:
    phase0 = [("inventory", None), ("masterdata", None), ("core", "0003_workspace_permission")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(phase0)
        assert "inventory_stockmovement" not in connection.introspection.table_names()
        MigrationExecutor(connection).migrate(latest)
        assert "inventory_stockmovement" in connection.introspection.table_names()
        assert (
            Group.objects.get(name="Warehouse Keeper")
            .permissions.filter(codename="issue_stock")
            .exists()
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'phase1_protect' "
                "AND NOT tgisinternal"
            )
            result = cursor.fetchone()
        assert result is not None and result[0] == 13
    finally:
        MigrationExecutor(connection).migrate(latest)
