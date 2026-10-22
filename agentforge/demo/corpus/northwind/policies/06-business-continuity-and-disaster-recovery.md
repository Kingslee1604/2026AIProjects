# Northwind Analytics Business Continuity and Disaster Recovery Plan

Fictional company for the AgentForge demo. CC-BY-4.0.

## Objectives

For the Northwind Insights platform, the recovery point objective (RPO) is 4 hours and the recovery time objective (RTO) is 8 hours.

## Backups

Production databases use continuous point-in-time recovery, and daily snapshots are retained for 35 days. Backups are encrypted and replicated to a separate region from the primary data.

## Resilience

The platform runs across multiple availability zones in the primary region, so the loss of a single zone does not cause an outage.

## Testing

Restores from backup are tested every quarter. A full failover to the disaster recovery region is tested once a year, and the results are reported to the Security Steering Committee.
