# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)

> Appended by P8 reviewer. The Pre-Release Checks above remain authoritative and unmodified.

### Deployment Runbook
- Runbook URL: `https://runbooks.taskq-super.internal/release/<version>` (substitute `<version>` = current git tag, e.g. `vharness-v4-20260816-score94-20-g0309551`).
- Source of truth (canonical copy): `harness/runbooks/RELEASE.md` (kept in sync by Release Engineering).
- Step-by-step owner: Release Engineering (`release-eng@taskq-super`). Any deviation must be logged as a release deviation note linked to the incident ticket.

### Rollback Owner & On-Call
| Role | Primary | Secondary |
|------|---------|-----------|
| Rollback decision authority | Release Manager on-call (`release-oncall@taskq-super`) | Engineering Manager (`eng-manager@taskq-super`) |
| Rollback executor | SRE on-call (`sre-oncall@taskq-super`) | SRE secondary (`sre-secondary@taskq-super`) |
| Customer comms approval | Head of Product (`product-lead@taskq-super`) | CEO (`ceo@taskq-super`) |

Rollback trigger criteria and commands live in `08-config/CONFIG_RECORDS.md` §7 (Rollback SOP). The on-call MUST acknowledge within 5 minutes of page; rollback decision MUST be made within 15 minutes of trigger.

### Post-Release Monitoring Dashboard
- Primary dashboard: `https://dashboards.taskq-super.internal/d/release-health` (Grafana).
- Key SLO panels to watch for the first 60 minutes post-release:
  - Request success rate (target ≥ 99.5%)
  - p95 latency (target ≤ 500 ms)
  - Error budget burn rate (alert > 2x baseline)
  - Queue depth (alert > 1.5x baseline for > 5 min)
- Alert routing: PagerDuty service `taskq-prod-release-watch`.
- Dashboard owner: SRE (`sre-oncall@taskq-super`).

### Customer Comms Template
```
Subject: [taskq-super] Release <version> deployed — <YYYY-MM-DD HH:MM UTC>

Hi <customer/segment>,

We deployed <version> to production on <date>. This release includes:

- <bullet 1 — user-visible change>
- <bullet 2 — user-visible change>
- <bullet 3 — user-visible change>

Expected impact: <none | minor | notable — describe>. No action required on your end.

If you observe unexpected behavior, please contact support@taskq-super or open a ticket via the in-app help menu.

Rollback plan: in place; we will revert within 15 minutes if SLOs degrade.

— taskq-super Release Team
```

Approval gate: Product Lead sign-off required before send; distribution via status page + customer-facing Slack announcements channel.
