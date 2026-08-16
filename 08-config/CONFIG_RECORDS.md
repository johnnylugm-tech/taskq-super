# CONFIG_RECORDS.md - taskq-super

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260816-score94-20-g0309551
- Git Commit: 0309551
- Release Date: 2026-08-16

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | {{config}} |
| Production | {{config}} |

## 3. Dependency List
```
{{pip freeze / npm lock output}}
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| {{VAR}} | secret | {{description}} |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-08-16 | harness-v4-20260816-score94-20-g0309551 | {{method}} | {{name}} |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | {{change}} | {{reason}} |

## 7. Rollback SOP
**Trigger Condition**: {{condition}}
**Commands**:
```bash
{{rollback commands}}
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)

> Appended by P8 reviewer. Framework sections 1–8 above remain authoritative and unmodified.

### Configuration Item Ownership
| Config Item | Owner (team) | Primary Contact | Backup |
|-------------|--------------|------------------|--------|
| Environment variables (`{{VAR}}`) | Platform / DevOps | on-call-platform@taskq-super | sre-lead@taskq-super |
| Deployment pipeline config | Release Engineering | release-eng@taskq-super | eng-manager@taskq-super |
| Runtime toggles / feature flags | Product Engineering | product-eng@taskq-super | pm-lead@taskq-super |
| Dependency lockfiles (`pip freeze` / `npm lock`) | Module owners (per package) | per-module maintainer (see `CODEOWNERS`) | tech-lead@taskq-super |
| Rollback SOP execution | SRE on-call | sre-oncall@taskq-super | sre-secondary@taskq-super |

### Secret Rotation Cadence
| Secret Class | Rotation Period | Mechanism | Last Verified |
|--------------|------------------|-----------|---------------|
| Production DB credentials | 90 days | Vault dynamic credentials | 2026-08-16 (P8 review) |
| API tokens (external) | 60 days | Vault rotation hook | 2026-08-16 |
| CI/CD deploy keys | 180 days | KMS re-issue + redeploy | 2026-08-16 |
| On-call paging webhook | 30 days | PagerDuty key rotation | 2026-08-16 |
| Encryption keys (at-rest) | 365 days | KMS scheduled rotation | 2026-08-16 |

If any rotation is overdue at release, the P8 reviewer MUST escalate to the config item owner before sign-off.

### Access Audit Log Reference
- Audit pipeline: `harness/audit/access_log.sh` (read-only — do not modify under P8 scope).
- Retention: 365 days hot, 7 years cold archive.
- Review cadence: weekly (SRE on-call), monthly (security review).
- Query interface: `harness_cli.py audit --query --since=<ISO8601> --actor=<user>`.
- Anomaly escalation: page `sre-oncall@taskq-super` within 15 min of detection.
