# Pilot Readiness: What Works and What's Missing

This document summarizes the state of the system and what's needed to move from hackathon to a real pilot at Strauss.

---

## What Works Today

### Calculation Engine
- Accurate monthly net pay: tax brackets, national insurance, health tax, pension credit — all from `rules.yaml`, zero numbers in code
- All 6 challenge employees match the organizers' spreadsheet to the shekel (automated tests)
- Exact decimal arithmetic, rounding to whole shekels only on display

### Eligibility Rules
- Group budgets (A-D) with car selection from the employee's group or lower
- Family track: 4+ children up to age 23, 50/50 gap split
- Electric car refund: exactly +₪250 net
- Car waiver by occupation, with taxable car maintenance
- Unused budget displayed for information only

### AI Agent
- Hebrew conversation, asks only for missing fields, in four steps
- Number Guard: every number in a reply must come from a tool result
- Car search across 77,988 Tax Authority records
- Six deterministic tools: profile update, car lookup, catalog, eligibility, comparison, rep question

### Document Reader (Optional)
- Employee uploads a payslip or contract, the system proposes values for confirmation
- Optional — manual input works in the chat or the details panel

### Employee Screen
- Chat + "My Details" panel with direct editing
- Comparison table: Today vs. alternatives with breakdown and explanation
- Car picker on each column, recalculation without calling the model
- Right-to-left, keyboard navigation, screen reader announcements

### Rep Screen
- Open and answered questions, structured answer form per type
- Choice answers (occupation/group), participation with effective date, free text
- Automatic conversation resume after an answer

### Infrastructure
- Single-command startup (`docker compose up`)
- `restart: unless-stopped` on all services — automatic recovery after a crash
- Hosted on a GCP VM with HTTPS (via DuckDNS)
- 1,300+ backend tests and 400+ frontend tests, no network or API key required
- Data replacement without code changes: YAML files + CSV

---

## Required for Pilot

### Security and Access Control

| Item | Current State | What's Needed |
|---|---|---|
| Authentication (SSO / Active Directory) | Not implemented | Employee identifies through the organization's system |
| Roles (employee / transport rep / admin) | Not implemented | Permission separation between screens |
| Audit log | Not implemented | Who viewed and changed what, for compliance |
| Least-privilege SQL user | Running as `sa` | A database user with minimal permissions |
| Privacy review with OpenAI | Not done | Data processing agreement (DPA) |

### Real Data from Strauss

| Item | Current State | What's Needed |
|---|---|---|
| Real car catalog | Challenge data only | YAML file in the same format, including vehicle type code |
| Real occupations table | Challenge data only | YAML file in the same format |
| Pilot-year tax tables | 2025 tables for illustration | Update `rules.yaml` |
| Calculation method confirmation | Per challenge rules | Strauss confirms the net pay is calculated correctly |
| Additional golden tests | 6 employees only | Strauss-approved net pay for comparison |

### Infrastructure

| Item | Current State | What's Needed |
|---|---|---|
| Hosting | GCP VM with Docker Compose and HTTPS | Managed service (Cloud Run / GKE) with health checks and scaling |
| Database backups | No backup strategy | Automated backups with a defined retention and restore procedure |
| Monitoring and alerting | Local logs only | Centralized logging and alerts on failures (Cloud Logging, Datadog) |
| Data retention policy | All conversations kept indefinitely | Conversations deleted after a defined period |

### Recommended Improvements

| Item | Current State | Value |
|---|---|---|
| Cumulative calculation (not just monthly) | Not built | Higher accuracy mid-tax-year |

---

## Migration Path

```
Step 1: Strauss provides real data
        → Replace YAML + CSV files, no code changes
        → Run golden tests against Strauss-approved net pay

Step 2: Security
        → SSO, roles, audit log
        → Data processing agreement with OpenAI

Step 3: Production infrastructure
        → Managed hosting on GCP with backups and monitoring
        → Staging environment for testing before deployment

Step 4: Limited pilot
        → Restricted group of employees
        → Feedback → adjustments → broader rollout
```

---

## Open Questions for Strauss

| # | Question | Current Default |
|---|---|---|
| 1 | Pilot-year tax tables | 2025 tables from the challenge |
| 2 | Is the employer's study fund deposit taxable | No (0%) |
| 3 | Real car catalog and occupations table | Challenge data |
| 4 | Which groups see family track cars | All (group A + children check) |
| 5 | Additional golden tests | 6 challenge employees |

Every answer is a data or configuration change — not a code change.
