# Northwind Analytics Access Control Standard

Fictional company for the AgentForge demo. CC-BY-4.0.

## Authentication

All workforce access to company systems goes through the corporate identity provider with single sign-on. Multi-factor authentication is required for every workforce account without exception.

Administrators of production systems must use phishing-resistant multi-factor authentication based on FIDO2 security keys. SMS codes are not accepted as a second factor for any workforce account.

Where passwords are still used, they must be at least 14 characters long. Shared and generic accounts are prohibited for workforce use.

## Least Privilege

Access is granted on the principle of least privilege and is based on role. Production access is limited to the on-call engineering and site reliability teams.

Privileged production access is granted just in time through an approval workflow and expires automatically after at most 8 hours. Every privileged session is logged.

## Access Reviews

Managers and system owners review all user access to production systems and to customer data every quarter. Access that is no longer needed is removed within five business days of the review.

## Joiners, Movers, and Leavers

Accounts for departing employees and contractors are disabled within 24 hours of their last working day. When an employee changes role, access that the new role does not need is removed within five business days.
