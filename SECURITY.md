# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in soev.ai, please report it privately.

**Email:** `security@soev.ai`

Please include:
- A description of the vulnerability and its impact.
- Steps to reproduce (or a proof-of-concept).
- The affected version, branch, or commit.
- Any suggested mitigations, if known.

We aim to acknowledge reports within **3 business days** and to provide a status update within **10 business days**.

**Please do not** open a public GitHub issue, pull request, or discussion for security reports until we have had a chance to investigate and ship a patch.

## Scope

This policy covers the soev.ai fork — primarily our additions on top of Open WebUI:

- Cloud sync integrations (OneDrive, Google Drive)
- External agents API proxy
- TOTP 2FA flow
- Data export and retention
- SSO invite flow (Microsoft Graph)
- Helm chart and deployment surface

For vulnerabilities in upstream Open WebUI features, please also consider reporting through [Open WebUI's security page](https://github.com/open-webui/open-webui/security/policy) so the wider community benefits from the fix.

## Supported versions

Security fixes are issued for the **`main`** branch (current stable). Older versions do not receive security updates.

## Security practices

The project runs the following on every push and on a weekly schedule:

- **Trivy** — container image vulnerability scanning.
- **Bandit** — Python static analysis.
- **pip-audit** — Python dependency vulnerability scan.
- **Dependabot** — automated dependency update alerts.

See [`.github/workflows/security-scanning.yaml`](./.github/workflows/security-scanning.yaml) for the full configuration.
