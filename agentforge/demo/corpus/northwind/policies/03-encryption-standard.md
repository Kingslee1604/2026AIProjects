# Northwind Analytics Encryption Standard

Fictional company for the AgentForge demo. CC-BY-4.0.

## Data at Rest

Customer data at rest is encrypted with AES-256, including databases, object storage, search indexes, and backups. Employee laptops use full-disk encryption that is enforced and monitored by device management.

## Data in Transit

All data in transit over public networks is encrypted with TLS 1.2 or higher, and TLS 1.3 is preferred. Connections between internal services are also encrypted with TLS. Legacy protocols such as SSL 3.0, TLS 1.0, and TLS 1.1 are disabled.

## Key Management

Encryption keys are generated and stored in the cloud provider's key management service, which uses FIPS 140-3 validated hardware security modules. Keys that protect customer data are rotated every twelve months.

Customers on the Enterprise plan can use customer-managed encryption keys. Access to key management is limited to a small group of platform engineers, and every key operation is logged.
