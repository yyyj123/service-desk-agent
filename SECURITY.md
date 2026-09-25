# Security policy

Use GitHub private vulnerability reporting for this repository. Do not put credentials,
private documents or exploit payloads in public issues.

## Deployment boundary

- Business tools are simulated; no real password reset, access grant or external ticket is performed.
- Demo credential display is OFF by default. Do not enable it with real company data.
- Configure your own secrets and HTTPS. Never commit environment files, runtime directories,
  logs, database backups or `deploy/secrets/`.
- Keep `/metrics` private and use its dedicated bearer token.
- Backend identity, tool validation, tenant filters and approvals enforce authorization;
  prompt filtering is only an additional layer.

## Dependencies

Local SQLite compatibility mode uses ChromaDB 1.5.9, which has known server-side
authorization and code-execution advisories, including
[GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c).
This application uses the embedded client without an embedding function and does not
start a Chroma HTTP server. Do not expose a Chroma server with this lock file.

The PostgreSQL cloud export excludes ChromaDB and requires DATABASE_URL. Its pinned
dependency scan on 2026-09-24 found no known vulnerabilities in the audited packages;
this does not cover platform-added dependencies or guarantee security.

Third-party dependencies retain their respective licenses. This project has not received
an independent penetration test or compliance certification.
