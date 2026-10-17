# Northwind Analytics Secure Development Standard

Fictional company for the AgentForge demo. CC-BY-4.0.

## Code Review

Every change to production code requires review and approval by at least one engineer other than the author. Branch protection prevents changes from being merged without a passing build and an approved review.

## Automated Security Testing

The build pipeline runs static application security testing, dependency vulnerability scanning, and secret scanning on every change. Container images are scanned for known vulnerabilities before they are deployed.

## Vulnerability Management

Critical vulnerabilities are remediated within 7 days and high vulnerabilities within 30 days. The external attack surface is scanned for vulnerabilities every month.

## Penetration Testing

An independent third party performs a penetration test of the Northwind Insights platform every year. A summary of the most recent penetration test is available to customers under a non-disclosure agreement.

## Training

Engineers complete secure coding training every year.
