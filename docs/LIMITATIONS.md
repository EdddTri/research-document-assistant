# Known limitations and prioritized improvements

This document lists observed failure modes, their impact and the improvements to prioritize.
The [evaluation](EVALUATION.md) records 57 fully correct, 11 partial and 12 incorrect answers across
80 development questions. See [architecture](../TECHNICAL_README.md) and
[recovery instructions](../README.md#stop-restart-and-recover).

## Priority 1: extraction and answer correctness

| Observed issue | Impact | Planned improvement |
| --- | --- | --- |
| Incomplete table crops | ReAct Table 1 retained only a header; RAG Table 1 lost its final row. | Improve crop boundaries and validate row/column coverage against original pages. |
| Unreadable native glyphs and incomplete figure descriptions | ReAct Figure 1 contained `(cid:...)` codes and omitted the reasoning trajectory. | Detect unusable native text and compare visual extraction with original pixels. |
| Wrong table selection | RAG returned page-8 ablation scores for a Table 1 question despite valid citations. | Preserve table/caption identity and check relevance to the requested table. |
| Numeric validation of citation identifiers | UUID fragments were treated as unsupported facts, dropping valid claims. | Exclude structural citation IDs from factual-number validation and add regression cases. |
| Missing structured evidence IDs | Drafts with inline citations but invalid `evidence_ids` lost all claims during validation. | Improve structured-output adherence and explicit recovery while retaining citation checks. |
| Follow-up context not consistently supplied to composition | The BERT follow-up failed to answer that BERT LARGE has 16 heads. | Pass resolved context to composition and test multi-turn references with retrieval. |

Vision cannot guarantee exact chart values or table layout. Merged cells and multi-panel figures remain
difficult. Crop splitting is implemented, but table fragments stay separate with partial-context
limitations. Q&A uses indexed descriptions rather than a fresh image request, so omitted details remain
unavailable. Question-specific visual reinspection is a possible extension requiring cost/latency tests.

## Priority 2: retrieval and feedback quality

- A relevant page can be retrieved without the answer-bearing chunk. Improve chunk selection and
  table/caption associations, using more precise relevance labels to measure the change.
- The judge missed an echoed attack marker, and some security grades disagreed with source review.
  Strengthen literal checks and calibrate the rubric while preserving original and reviewed scores.
- One code reference required a function wrapper absent from its question. Clarify the question and
  expected output in a new dataset version rather than silently changing past results.
- Some refusals included irrelevant figures or tables. Align displayed evidence with the supported
  part of the answer and suppress unrelated visual material.
- Cost estimates can omit usage hidden by provider retries/timeouts. Keep incomplete estimates
  explicit and separate query, vision and judge costs when comparing configurations.

## Priority 3: durable ingestion delivery

The backend commits document/job records before publishing to Redis. A crash between those operations
can leave a queued job without a message. PostgreSQL is the durable job authority; publication and
worker failures currently use manual Retry, alongside bounded SDK request retries.

Progress renews a 26-minute lease. Queued jobs can be resent; processing jobs become retryable after
lease expiry. Retrying cancels the old attempt, and attempt tokens prevent stale publication. These
controls do not provide automatic recovery.

Planned improvements are a transactional job/outbox write, a dispatcher with bounded delivery backoff,
stalled-job reconciliation, bounded transient-failure retries, and queue-age/dead-letter monitoring.
Test crashes around publication, duplicate delivery and Redis/worker outages against real PostgreSQL.
The initial migration retains `app.outbox` for compatibility; current code does not use it. Reintroduce
the ORM/dispatcher through a coordinated change, not alongside the current direct-publication workflow.

## Priority 4: deployment and scale

- Provider quota, timeouts and malformed outputs can fail whole documents. Two of five added PDFs
  initially failed coordinate validation and required Retry. Splitting handles truncation, not all
  provider/schema errors. Any partial-ingestion mode must disclose missing evidence.
- No PDF size/page-count ceiling is configured, but disk, memory and provider limits still apply.
  Windows uses one Celery `solo` worker without prefork time-limit enforcement. Evaluate Linux workers,
  admission control and shared rate-limit scheduling before scaling.
- Exact vector search has no approximate index or reranker. Measure larger collections before adding
  those optimizations.
- Deletion blocks access but retains files/records. Implement physical retention/purge and tested
  backup/restore procedures when required.
- The demo has a six-character password minimum, no email verification/recovery and no application
  rate limiting. Remote deployment needs an appropriate password policy, abuse controls and TLS with
  secure cookies.
- Local processes share root environment credentials. Restricted database roles limit queries but do
  not isolate secrets from other processes. Use per-service secret delivery and planned rotation.
- LangSmith tracing/evaluation exists; deployment alerting, uptime monitoring and recovery procedures
  remain to be implemented. Enabled tracing exports document content and must match its data policy.

Prioritize evidence extraction and answer correctness before scaling the deployment. The improvements
above are proposed work; the evaluation report describes the current implementation's observed results.
