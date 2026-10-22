"""Database access: engines, tenant-scoped transactions, migrations, and RLS checks.

Rule (architecture §13.2): application code reaches tenant data only through
`tenant_session`, which sets the tenant context for exactly one transaction.
"""
