"""Schema lint: every table in the `app` schema must be tenant-isolated (NFR-01, baseline S4)."""

from sqlalchemy import Connection, text

# Tables whose tenant key is not `org_id`.
TENANT_KEY_OVERRIDES = {"organizations": "id"}
ROLE_FLAGS = ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb")

_TABLES = text(
    """
    SELECT c.relname AS name,
           c.relrowsecurity AS rls,
           c.relforcerowsecurity AS forced,
           EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy,
           pg_get_userbyid(c.relowner) AS owner,
           ARRAY(SELECT a.attname::text FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname
    """
)


def find_rls_violations(connection: Connection, app_role: str = "agentforge_app") -> list[str]:
    violations: list[str] = []
    for row in connection.execute(_TABLES).mappings():
        name = str(row["name"])
        key = TENANT_KEY_OVERRIDES.get(name, "org_id")
        if key not in row["columns"]:
            violations.append(f"app.{name}: no tenant column '{key}'")
        if not row["rls"]:
            violations.append(f"app.{name}: row level security not enabled")
        if not row["forced"]:
            violations.append(f"app.{name}: row level security not forced")
        if not row["has_policy"]:
            violations.append(f"app.{name}: no RLS policy")
        if row["owner"] == app_role:
            violations.append(f"app.{name}: owned by {app_role} (owners can alter or disable RLS)")

    role = (
        connection.execute(
            text(f"SELECT {', '.join(ROLE_FLAGS)} FROM pg_roles WHERE rolname = :role"),  # noqa: S608
            {"role": app_role},
        )
        .mappings()
        .first()
    )
    if role is None:
        violations.append(f"role {app_role} does not exist")
    else:
        violations.extend(f"role {app_role}: {flag} is set" for flag in ROLE_FLAGS if role[flag])
    return violations
