"""SQL for tenant-isolated tables, used by migrations (one template, so every table is alike)."""

APP_ROLE = "agentforge_app"


def tenant_table_sql(table: str, tenant_column: str = "org_id") -> list[str]:
    """Enable and force RLS on `app.<table>` with an isolation policy, and grant DML to the app.

    FORCE makes the policy apply to the table owner too; only superusers bypass it.
    """
    qualified = f"app.{table}"
    predicate = f"{tenant_column} = app.current_org_id()"
    return [
        f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {qualified} "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO {APP_ROLE}",
    ]
