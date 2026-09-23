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

Current release: **v0.3.367**

v0.3.367 makes full-backup progress continuous while Docker data is being copied. Update Monitor uses measured copied bytes when a fast size sample is available and otherwise shows a conservative bounded intermediate estimate; visible changes are limited to small steps, and the copy phase can never reach completion until `docker cp` actually exits.\n\nv0.3.366 separates backup progress from installation progress: a backup now completes its own 0–100% phase before installation begins. Manual update installation is also dispatched to a backend worker and followed through short action-progress requests, so long Docker operations are no longer tied to one proxy request that can be reported as HTTP 504.

v0.3.365 hardens moving tag updates such as `:latest`: when a scan advertises a concrete target version, Update Monitor now verifies that the freshly pulled moving tag resolves to the same registry digest as that concrete version before any container is recreated. A lagging or stale moving tag is rejected without changing the app.\n\nv0.3.364 moves the running backup cancel action into the existing backup progress button. The progress percentage remains visible, and the same compact control can be clicked to cancel the backup without adding a second button to the app card.\n\nv0.3.363 adds safe cancellation for a running pre-update backup. Cancelling interrupts the active Docker copy, restores containers stopped for a full backup, deletes the `.incomplete` staging directory, and prevents the update, image-source switch, or update-channel switch from continuing.\n\nv0.3.362 repairs stale concrete-version state for moving numeric channels such as `:2`. Numeric major tags now always use digest-to-release resolution, legacy `previous-scan`/`configured-tag` evidence is rejected for moving channels, and the installed-version cache schema is bumped so old mappings are revalidated.\n\nv0.3.361 fixes stale installed-version display after a moving-tag update. The concrete version is now resolved from the freshly pulled Docker digest and passed to the targeted verification scan. Previous installed-version results are reused only when both RepoDigests and local Docker image IDs are unchanged.

v0.3.360 repairs mixed Compose/runtime channel states. If Compose still points to an old GHCR fixed tag while the live container already runs the Docker Hub channel, Update Kanal now reconciles Compose to the selected live registry/channel instead of falsely reporting that the channel is already active. Automatic rollback verifies the restored original Compose ref, not the stale runtime alias.

v0.3.359 preserves the selected moving Docker channel (for example `:2`) across scans, automatic updates and policy edits. Normal updates resolve the live Docker repository before cached source metadata, and a manual Docker check acknowledges an old automatic-update error so the same stale red message is not immediately rendered again.

The v0.3.359 release workflow compiles the Python backend and runs regression checks for live-registry precedence, channel persistence, stale automatic-error acknowledgement and frontend synchronization before the release image is published.

Update Kanal now actively completes a stored Compose change when ZimaOS leaves the old runtime container in place. It waits for an in-flight replacement first, then sends at most one fallback recreate and verifies the exact target image. Before the switch, the original Docker image ID and RepoDigests are captured; automatic rollback restores those exact local image bits without repulling a mutable tag and verifies runtime stability before reporting success.

Update Kanal is registry-preserving. It changes only the tag on the registry that is actually active in the running container. GitHub project metadata and discovered aliases can no longer turn a Docker Hub channel change into a GHCR change. Registry migrations remain a separate Image Quelle action.

Registry alias verification still accepts an exact Docker image ID match when Docker Hub and GHCR expose different RepoDigests for equivalent image bits, but that compatibility logic is verification-only and no longer selects a different registry for Update Kanal.

Update Kanal no longer starts a second forced ZimaOS container recreate during the Compose channel change. It now waits for the original Compose apply to finish and verifies the target digest. The separate Image Quelle recreate path now prefers the current Docker container ID after replacement.

Update Kanal now treats HTTP 502, 503 and 504 responses as transport interruptions when the backend Docker action is still running. The dialog keeps the action locked, follows backend progress and waits for the targeted verification instead of showing a false failure while ZimaOS is still applying the change.

Update channels are now separated from concrete versions. Moving Docker tags such as latest, stable, next, lts or a major channel such as 2 can be selected directly from the application details with backup, Compose validation, verified pull and targeted post-change verification.

The Port Conflict view now keeps configured conflicts visible and colors runtime status clearly: running is green and stopped is red.

Gateway errors such as HTTP 502, 503 or 504 during a long running update are now treated as an interrupted response first. Update Monitor waits for the existing targeted verification and only reports a failure if the installed result cannot be confirmed.

Self updates now trigger a browser reload as soon as the replacement Update Monitor backend is detected. The browser also compares its loaded frontend version with the backend version during runtime polling and reloads with cache busting when they differ.
