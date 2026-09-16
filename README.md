# Update Monitor

Update Monitor is a self-hosted web application for ZimaOS that checks Docker applications for image and version updates and provides per-application update policies, targeted checks, update installation, backup and restore workflows, and runtime information.

## Screenshots

### Dashboard

Overview of monitored Docker applications, update state, runtime status and available actions.

![Update Monitor dashboard](https://isanto1306.github.io/zima-appstore/apps/io.github.isanto1306.update-monitor/assets/screenshot-1-dashboard.webp)

### Application details

Detailed information for an individual application, including image source, installed version, digest information, backup controls and self-protection.

<p align="center">
  <img src="https://isanto1306.github.io/zima-appstore/apps/io.github.isanto1306.update-monitor/assets/screenshot-2-details.webp" alt="Update Monitor application details" width="430">
</p>

### Automatic updates

Configure update policy, schedule, weekdays, immediate installation and backup behavior for each application.

![Update Monitor automatic updates](https://isanto1306.github.io/zima-appstore/apps/io.github.isanto1306.update-monitor/assets/screenshot-3-auto-updates.webp)

### Backup and restore

Manage backup retention, stored backups, restore operations and cleanup from the built-in backup interface.

<p align="center">
  <img src="https://isanto1306.github.io/zima-appstore/apps/io.github.isanto1306.update-monitor/assets/screenshot-4-backups.webp" alt="Update Monitor backup settings" width="800">
</p>

## Requirements

- ZimaOS / CasaOS App Management
- Docker with access to `/var/run/docker.sock`
- A host where port `9001` is available

> **Security note:** Access to the Docker socket is highly privileged. Run Update Monitor only on a trusted host and protect the web interface with a strong username, password, and session secret.

## Installation from source

```bash
cp .env.example .env
```

Edit `.env` and replace all `CHANGE_ME` values. Then start the application:

```bash
docker compose up -d --build
```

Open:

```text
http://YOUR_SERVER_IP:9001
```

## Persistent data

Update Monitor stores runtime state and backups in local directories that are excluded from Git:

```text
./cache
./backups
```

Do not commit `.env`, `cache`, or `backups`.

## Backups

Two backup modes are available:

- **Quick backup**: stores Docker/Compose state. It is not a complete rollback when an application has persistent data that may be migrated by a newer application version.
- **Full app backup**: also copies detected application data/volumes while the affected containers are stopped, then restarts them after the backup.

For version rollback of stateful applications, use a full app backup.

## GitHub API token

A GitHub token is optional and can increase GitHub API limits. It can be supplied through the application settings or with `UPDATE_MONITOR_GITHUB_TOKEN`. Never commit a real token.

## Privacy

The repository contains no user-specific `.env`, cache data, backup data, NAS hostnames, local IP addresses, or private filesystem paths. Runtime information stays on the host unless the user explicitly sends it elsewhere.

## Version

Current release: **v0.3.325**
