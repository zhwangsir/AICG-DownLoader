# Security Policy

DashBox is a local-final fork based on DramaClaw CE (Elastic License 2.0). It has
no upstream update channel and no upstream security process — **all security
issues are handled locally**.

## Reporting a problem

Please do not disclose security issues publicly. Handle them locally with the
repository owner/operators:

- Fix in place, then verify with `uv run pytest tests/ -q`.
- If the issue originates in the upstream DramaClaw CE codebase, note the
  upstream provenance in the fix commit message for attribution tracking.

When triaging, please record:

- The version or commit affected
- A clear description of the issue and the impact you observed
- Steps to reproduce (proof-of-concept snippets are welcome)
- Any mitigations already applied

## Out of scope

The following are unlikely to be treated as security reports:

- Issues that require physical access to the host
- Self-inflicted resource exhaustion on a user's own self-hosted deployment
- Findings in third-party model providers — please report those to the
  provider directly
