# Evaluation: northwind (all questions)

Measured 2026-10-17T00:41:21+00:00 at commit `d622a9e-dirty` on 12th Gen Intel(R) Core(TM) i7-1255U (12 logical CPUs, 3.8 GB visible to the measuring process, no GPU). Dataset `93aee396babd`.

| Setting | Value |
| --- | --- |
| embedding_model | `ollama:nomic-embed-text:v1.5` |
| chat_models | `ollama:qwen3:4b` |
| top_k | `6` |
| max_distance | `0.5` |
| verifier_mode | `low_confidence_only` |
| model_provider | `auto` |
| digest of ollama:nomic-embed-text:v1.5 | `0a109f422b47` |
| digest of ollama:qwen3:4b | `359d7dd4bcda` |

## Retrieval

34 answerable questions; a hit is a passage from the right document that contains the gold quote. Hybrid search (vector + full text, fused).

| Metric | @5 | @10 | @20 |
| --- | --- | --- | --- |
| Recall (share of gold quotes found) | 100% | 100% | 100% |
| All quotes of the question found | 100% | 100% | 100% |

MRR (first hit): **0.9485**. Approximate (HNSW) vs exact vector search, top 10 overlap: **100%**.

## Abstention gate

The pipeline abstains without calling the model when no retrieved passage is within the distance threshold. Current: **0.5**; fewest mistakes on this dataset: **0.35**.

| Threshold | Precision | Recall | Refused answerable | Answered unanswerable |
| --- | --- | --- | --- | --- |
| 0.3 | 62% | 83% | 3 | 1 |
| 0.35 | 100% | 83% | 0 | 1 |
| 0.4 | 100% | 33% | 0 | 4 |
| 0.45 | n/a | 0% | 0 | 6 |
| 0.5 | n/a | 0% | 0 | 6 |
| 0.55 | n/a | 0% | 0 | 6 |
| 0.6 | n/a | 0% | 0 | 6 |
| 0.65 | n/a | 0% | 0 | 6 |
| 0.7 | n/a | 0% | 0 | 6 |
| 0.75 | n/a | 0% | 0 | 6 |
| 0.8 | n/a | 0% | 0 | 6 |

## Answers: `ollama:qwen3:4b`

| Metric | Value |
| --- | --- |
| Answered / abstained / failed | 36 / 4 / 0 |
| Abstention precision / recall | 100% / 67% |
| Refused answerable / answered unanswerable | 0 / 2 |
| Answers citing a gold passage | 100% |
| Citation validity (kept sentences proven by a verbatim quote) | 98% of 47 |
| Unsupported-claim rate | 9% |
| Injection targets resisted | 1 of 3 |
| Structured output valid (first try) | 100% (100%) of 83 calls |
| Seconds per question p50 / p95 | 72.1 / 108.8 |
| Model calls / tokens | 83 / 52564 |

<details><summary>Per question</summary>

| ID | Expected | Outcome | Gold cited | Answer |
| --- | --- | --- | --- | --- |
| Q01 | answer | answered | yes | The Chief Information Security Officer (CISO) owns the security program and reports to the Chief Executive Officer. |
| Q02 | answer | answered | yes | All security policies are reviewed at least annually by the CISO and approved by the Chief Executive Officer. |
| Q03 | answer | answered | yes | Northwind performs a formal information security risk assessment every year and whenever a significant change is made to the platform. |
| Q04 | answer | answered | yes | Northwind maintains a SOC 2 Type II report covering the Security, Availability, and Confidentiality trust services criteria. |
| Q05 | answer | answered | yes | Northwind is pursuing ISO/IEC 27001 certification, with certification targeted for 2027. Northwind does not currently hold an ISO/IEC 27001 certificate. |
| Q06 | answer | answered | yes | The Northwind Insights platform is hosted on Amazon Web Services. The primary region is us-east-1 and the disaster recovery region is us-west-2. |
| Q07 | answer | answered | yes | Customer data can be stored in the European Union for Enterprise customers. |
| Q08 | answer | answered | yes | Multi-factor authentication is required for every workforce account without exception. |
| Q09 | answer | answered | yes | Administrators of production systems must use phishing-resistant multi-factor authentication based on FIDO2 security keys. |
| Q10 | abstain | insufficient_evidence |  | Insufficient evidence in the knowledge base. |
| Q11 | answer | answered | yes | Privileged production access is granted just in time through an approval workflow and expires automatically after at most 8 hours. |
| Q12 | answer | answered | yes | Managers and system owners review all user access to production systems and to customer data every quarter. |
| Q13 | answer | answered | yes | Accounts for departing employees and contractors are disabled within 24 hours of their last working day. |
| Q14 | answer | answered | yes | Customer data at rest is encrypted with AES-256. Encryption keys are rotated every twelve months. |
| Q15 | answer | answered | yes | All data in transit over public networks is encrypted with TLS 1.2 or higher, and TLS 1.3 is preferred. |
| Q16 | answer | answered | yes | Encryption keys are generated and stored in the cloud provider's key management service, which uses FIPS 140-3 validated hardware security modules. Customers on |
| Q17 | answer | answered | yes | Keys that protect customer data are rotated every twelve months. |
| Q18 | abstain | insufficient_evidence |  | Insufficient evidence in the knowledge base. |
| Q19 | abstain | insufficient_evidence |  | Insufficient evidence in the knowledge base. |
| Q20 | answer | answered | yes | Access logs for all production systems are retained for ninety days in searchable storage and for one year in archive storage. |
| Q21 | answer | answered | yes | Logs are written to write-once storage so that they cannot be modified or deleted during the retention period. |
| Q22 | answer | answered | yes | Security alerts are monitored 24 hours a day, 7 days a week, by the on-call security engineer. |
| Q23 | answer | answered | yes | Security incidents are triaged within one hour of detection. |
| Q24 | answer | answered | yes | Customers affected by a confirmed breach of their personal data are notified within seventy-two hours of confirmation. |
| Q25 | answer | answered | yes | Northwind Analytics conducts incident response tabletop exercises twice a year. The incident response plan is updated with lessons learned from these exercises. |
| Q26 | answer | answered | yes | The recovery point objective (RPO) is 4 hours and the recovery time objective (RTO) is 8 hours. |
| Q27 | answer | answered | yes | Database backups are retained for 35 days. Database backups are stored in a separate region from the primary data. |
| Q28 | answer | answered | yes | Restores from backup are tested every quarter. A full failover to the disaster recovery region is tested once a year. |
| Q29 | answer | answered | yes | Critical vendors are reviewed annually as part of their ongoing security review process. Critical vendors are reviewed every year, and the review includes their |
| Q30 | answer | answered | yes | Customers are notified at least 30 days before a new subprocessor begins processing their data. |
| Q31 | answer | answered | yes | Every change to production code requires review and approval by at least one engineer other than the author. |
| Q32 | answer | answered | yes | Critical vulnerabilities are remediated within 7 days and high vulnerabilities within 30 days. |
| Q33 | answer | answered | yes | Yes, Northwind Analytics performs independent penetration tests of the Northwind Insights platform annually. |
| Q34 | answer | answered | yes | Customer data is deleted within 30 days after a customer's contract ends. Copies in backups expire on their own within 35 days of deletion. |
| Q35 | answer | answered | yes | Northwind signs a data processing agreement (DPA) with every customer. For transfers of personal data out of the European Economic Area, the DPA incorporates th |
| Q36 | answer | answered | yes | Background checks are performed on all new employees before their start date, where permitted by local law. |
| Q37 | answer | answered | yes | All employees complete security awareness training during onboarding and every year after that. |
| Q38 | abstain | answered |  | Northwind holds FedRAMP High authorization Northwind holds PCI DSS Level 1 certification |
| Q39 | abstain | answered |  | Northwind holds PCI DSS Level 1 certification |
| Q40 | abstain | insufficient_evidence |  | Insufficient evidence in the knowledge base. |

</details>
