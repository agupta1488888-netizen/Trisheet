# Documentation

Design notes and decision records for Trisheet.

Working rules live in [CLAUDE.md](../CLAUDE.md) at the repository root, not
here. This directory is for the things that need more room than a rule: metric
templates per sector, the peer selection ladder, XBRL tag research, and any
decision whose reasoning would otherwise be lost.

## Contents

- [Architecture](architecture.md) — the thirteen modules, what each is
  responsible for, and what happens when each one fails.
- [Sourcing and validation](sourcing-and-validation.md) — the tier hierarchy
  and where code enforces it, how claims are cited, how conflicting and
  missing information are handled, and why the system does not fabricate
  figures.
- [Deployment](deployment.md) — the deployed topology, the EDGAR rate
  constraint, monitoring, refresh and where scaling binds first.
