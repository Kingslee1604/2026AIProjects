# Northwind Analytics Logging and Monitoring Standard

Fictional company for the AgentForge demo. CC-BY-4.0.

## What Is Logged

Production systems log authentication events, privileged actions, administrative changes, and access to customer data. Logs include a timestamp, the acting identity, the source address, and the outcome.

## Retention

Access logs for all production systems are retained for ninety days in searchable storage and for one year in archive storage. Logs are written to write-once storage so that they cannot be modified or deleted during the retention period.

## Monitoring and Alerting

Logs are centralized in a security information and event management (SIEM) system. Security alerts are monitored 24 hours a day, 7 days a week, by the on-call security engineer.

The security team reviews privileged-access logs every week. All production systems synchronize their clocks with a trusted time source so that events can be correlated.
