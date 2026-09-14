# Security

## Sensitive files

Never commit any of the following:

- `.env`
- GitHub access tokens
- passwords or session secrets
- `cache/` contents
- `backups/` contents
- exported Docker/ZimaOS runtime state

The provided `.gitignore` excludes the normal local state directories.

## Docker socket

Update Monitor needs Docker API access for container inspection, updates, backups, and restore operations. Mounting `/var/run/docker.sock` gives the container highly privileged control over Docker. Only deploy it on a trusted host and restrict access to the web interface.

## Reporting a vulnerability

Please avoid posting credentials, tokens, private logs, hostnames, or local IP addresses in public issues. Redact sensitive information before sharing diagnostics.
