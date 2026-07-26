# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 3.x     | ✅        |
| < 3.0   | ❌        |

## Reporting a Vulnerability

If you discover a security issue (especially around API key handling, local storage, or update checks), please **do not open a public issue**.

Prefer one of:

1. GitHub Security Advisory on this repository (if enabled)
2. Contact the maintainer via GitHub profile: [249469326i-lang](https://github.com/249469326i-lang)

Please include:

- Impact description
- Reproduction steps
- Affected version / commit
- Whether API keys or local data could leak

We will acknowledge reports as soon as practical and coordinate a fix before public disclosure.

## Security Notes for Users

- API keys are encrypted with **Windows DPAPI** and stored under `%APPDATA%\.api-monitor\`.
- Do not commit real API keys, `providers.db`, or local config into git.
- Prefer downloading release binaries only from this repository’s **GitHub Releases**.
- Automatic update checks only query GitHub Releases for this project’s repository.
