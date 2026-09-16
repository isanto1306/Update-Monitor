import json
import base64
import concurrent.futures
import copy
import hashlib
import ipaddress
import socket
import ssl
import struct
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "0.3.331"
STATIC_DIR = Path(os.getenv("UPDATE_MONITOR_STATIC_DIR", "/app/static"))
CACHE_DIR = Path(os.getenv("UPDATE_MONITOR_CACHE_DIR", "/app/cache"))
SCAN_FILE = CACHE_DIR / "scan.json"
SETTINGS_FILE = CACHE_DIR / "settings.json"
POLICY_FILE = CACHE_DIR / "policies.json"
BACKUP_DIR = Path(os.getenv("UPDATE_MONITOR_BACKUP_DIR", str(CACHE_DIR / "backups")))
BACKUP_RETENTION_DEFAULT = "5"
BACKUP_RETENTION_OPTIONS = {"2": 2, "5": 5, "10": 10, "forever": None}
BACKUP_MAX_PER_APP_DEFAULT = 3
BACKUP_MAX_PER_APP_OPTIONS = {1, 2, 3}
BACKUP_LOCK = threading.Lock()
BACKUP_ENCRYPTION_LOCK = threading.RLock()
BACKUP_ENCRYPTION_CONFIG_FILE = BACKUP_DIR / ".encryption.json"
BACKUP_AUTO_UNLOCK_FILE = CACHE_DIR / "backup-encryption-autounlock.json"
BACKUP_MASTER_KEY = None
BACKUP_ENCRYPTION_AAD = b"update-monitor-backup-master-v1"
BACKUP_AUTO_UNLOCK_AAD = b"update-monitor-backup-autounlock-v1"
BACKUP_PAYLOAD_CHUNK_SIZE = 1024 * 1024
GITHUB_VERSION_CACHE_FILE = CACHE_DIR / "github-versions.json"
GITHUB_VERSION_CACHE_TTL_SECONDS = 6 * 60 * 60
GITHUB_VERSION_CACHE_STALE_SECONDS = 7 * 24 * 60 * 60
GITHUB_VERSION_CACHE_LOCK = threading.Lock()
GITHUB_TOKEN_FILE = CACHE_DIR / "github-token"
UPDATE_MONITOR_GITHUB_TOKEN = os.getenv("UPDATE_MONITOR_GITHUB_TOKEN", "").strip()

# Persistent source/project link cache.  This is intentionally independent
# from the GitHub release cache: it maps Docker image repositories to their
# upstream project page/source forge.
PROJECT_LINK_CACHE_FILE = CACHE_DIR / "project-links.json"
PROJECT_LINK_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
PROJECT_LINK_NEGATIVE_TTL_SECONDS = 24 * 60 * 60
PROJECT_LINK_CACHE_LOCK = threading.Lock()
PROJECT_LINK_CACHE = {}

# Persistent handoff state for updating Update Monitor itself. The actual
# container recreation is performed by a short-lived helper so the updater is
# not killed before it can hand control to the replacement container.
SELF_UPDATE_STATE_FILE = CACHE_DIR / "self-update.json"

# Persistent canonical Compose image-source assignments.
IMAGE_SOURCE_STATE_FILE = CACHE_DIR / "image-sources.json"
RESTORE_PIN_STATE_FILE = CACHE_DIR / "restore-pins.json"
RESTORE_PIN_STATE_LOCK = threading.RLock()
IMAGE_SOURCE_STATE_LOCK = threading.RLock()
IMAGE_SOURCE_STATE = {}
IMAGE_SOURCE_STATE_LOADED = False

# Verified installed-version cache for containers configured with generic tags
# such as "latest" or "stable". The cache key includes the exact local
# RepoDigest set, so replacing/updating the image invalidates stale mappings.
INSTALLED_VERSION_CACHE_FILE = CACHE_DIR / "installed-versions.json"
INSTALLED_VERSION_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
INSTALLED_VERSION_CACHE_LOCK = threading.Lock()
INSTALLED_VERSION_CACHE = {}

# Manual confirmations for installed Docker versions that Update Monitor can
# only classify as a hint/unresolved value. A confirmation is tied to the
# exact app/image/version/tag/digest signature, so a changed image naturally
# becomes unconfirmed again.
VERSION_CONFIRMATIONS_FILE = CACHE_DIR / "version-confirmations.json"
VERSION_CONFIRMATIONS_LOCK = threading.Lock()
VERSION_CONFIRMATIONS = {}

# Reuse short-lived anonymous registry bearer tokens while checking several
# manifests from the same repository.
REGISTRY_MANIFEST_TOKEN_CACHE_LOCK = threading.Lock()
REGISTRY_MANIFEST_TOKEN_CACHE = {}
REGISTRY_MANIFEST_TOKEN_CACHE_TTL_SECONDS = 10 * 60

ICON_CACHE_DIR = CACHE_DIR / "icons"
ICON_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
ICON_CACHE_FAILURE_TTL_SECONDS = 5 * 60
ICON_CACHE_MAX_BYTES = 2 * 1024 * 1024
ICON_CACHE_WORKERS = 8
ICON_CACHE_LOCKS_GUARD = threading.Lock()
ICON_CACHE_LOCKS = {}
# Browser URLs contain only an opaque cache key. The original remote URL stays
# server-side so the browser never has to round-trip it through a query string.
ICON_SOURCE_MAP_GUARD = threading.Lock()
ICON_SOURCE_MAP = {}
CASAOS_URL_FILE = Path("/host/run/casaos/app-management.url")
MESSAGE_BUS_URL_FILE = Path("/host/run/casaos/message-bus.url")


def require_private_env(name, min_length):
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"{name} must be set. Copy .env.example to .env and provide a private value.")
    if not value or value.upper().startswith("CHANGE_ME"):
        raise RuntimeError(f"{name} still contains a placeholder and must be changed before startup.")
    if len(value) < min_length:
        raise RuntimeError(f"{name} must contain at least {min_length} characters.")
    if "\n" in value or "\r" in value:
        raise RuntimeError(f"{name} must not contain newline characters.")
    return value


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


UPDATE_MONITOR_USERNAME = require_private_env("UPDATE_MONITOR_USERNAME", 1)
UPDATE_MONITOR_PASSWORD = require_private_env("UPDATE_MONITOR_PASSWORD", 12)
SESSION_SECRET = require_private_env("UPDATE_MONITOR_SESSION_SECRET", 32)
SESSION_HTTPS_ONLY = env_bool("UPDATE_MONITOR_SESSION_HTTPS_ONLY", False)

app = FastAPI(title="Update Monitor", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="update_monitor_session", max_age=60 * 60 * 24 * 7, same_site="lax", https_only=SESSION_HTTPS_ONLY)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/icons-v3/"):
        if 200 <= response.status_code < 300:
            response.headers["Cache-Control"] = "private, max-age=604800, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/api/icons-v2/") or request.url.path.startswith("/api/icons/"):
        # Legacy icon URLs stay retryable. v3 no longer transports the remote
        # source URL through the browser at all.
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class LoginRequest(BaseModel):
    username: str
    password: str


class SettingsRequest(BaseModel):
    scan_interval_seconds: int


class GithubTokenRequest(BaseModel):
    token: Optional[str] = None


class AppUpdateRequest(BaseModel):
    stack_key: str
    backup_mode: Optional[str] = "none"


class ImageSourceSwitchRequest(BaseModel):
    stack_key: str
    image_key: str
    source_id: str
    accept_warnings: bool = False
    backup_mode: Optional[str] = "full"


class AppRestoreRequest(BaseModel):
    stack_key: str
    backup_id: Optional[str] = None


class BackupSettingsRequest(BaseModel):
    retention: Optional[str] = None
    max_per_app: Optional[int] = None
    stack_key: Optional[str] = None


class VersionConfirmationRequest(BaseModel):
    stack_key: str
    image_ref: str


class BackupEncryptionRequest(BaseModel):
    enabled: bool
    password: Optional[str] = None


class BackupUnlockRequest(BaseModel):
    password: str


class AppRuntimeRequest(BaseModel):
    stack_key: str
    action: str


class AppScanRequest(BaseModel):
    stack_key: str


class AppUninstallRequest(BaseModel):
    stack_key: str
    delete_config_folder: bool = False


class AppPolicyRequest(BaseModel):
    stack_key: str
    mode: str
    target_tag: Optional[str] = None
    auto_enabled: bool = False
    auto_immediate: bool = False
    auto_time: Optional[str] = "03:00"
    auto_days: Optional[list[int]] = None
    auto_timezone: Optional[str] = None
    auto_backup_mode: Optional[str] = "none"


LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 60.0
LOGIN_BLOCK_SECONDS = 60.0
LOGIN_RATE_LIMIT_LOCK = threading.Lock()
login_failures = {}
scan_lock = threading.Lock()
scan_thread = None
scan_state = {"state": "idle", "started_at": None, "finished_at": None, "results": [], "apps": [], "summary": {}, "app_summary": {}}
settings_lock = threading.Lock()
app_update_lock = threading.Lock()
policies_lock = threading.Lock()
# One deduplicated authoritative post-action scan per app. This prevents a
# failed/retried automation from queueing several identical scans which then
# execute one after another.
post_scan_lock = threading.Lock()
post_scan_pending = {}
# v0.3.119: a batch of automatic updates gets one shared full verification
# scan after every installation has finished. Keeping this marker separate from
# per-app post scans prevents scan -> update -> scan -> update interleaving.
post_full_scan_pending = None
settings = {"scan_interval_seconds": 21600, "backup_retention": BACKUP_RETENTION_DEFAULT, "backup_max_per_app": BACKUP_MAX_PER_APP_DEFAULT, "backup_max_per_app_by_stack": {}, "backup_max_per_app_migrated_v1": False}
policies = {}
SERVICE_STARTED_AT = datetime.now(timezone.utc).isoformat()

# Live action progress. Numeric update progress is taken from ZimaOS'
# own app-management message bus (app:install-progress / app:progress).
# Uninstall has no native numeric percentage, so only actually observed
# container removals are converted into a measured percentage.
action_progress_lock = threading.Lock()
action_progress = {}
message_bus_state_lock = threading.Lock()
message_bus_state = {
    "connected": False,
    "last_connected_at": None,
    "last_event_at": None,
    "last_error": None,
}



def _clamp_progress(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (number == number):
        return None
    return max(0, min(100, int(round(number))))


def begin_action_progress(stack_key, kind, app_item, automatic=False):
    stack_key = str(stack_key or "").strip()
    kind = str(kind or "").strip().lower()
    if not stack_key or kind not in {"update", "uninstall", "restore"}:
        return

    project = str((app_item or {}).get("compose_project") or "").strip()
    app_name = str((app_item or {}).get("name") or "").strip()
    container_count = int((app_item or {}).get("container_count") or 0)

    aliases = {value.lower() for value in (stack_key, project, app_name) if value}
    for item in (app_item or {}).get("items") or []:
        for container in item.get("containers") or []:
            for value in (
                container.get("name"),
                container.get("compose_service"),
            ):
                value = str(value or "").strip()
                if value:
                    aliases.add(value.lower())

    record = {
        "stack_key": stack_key,
        "kind": kind,
        "automatic": bool(automatic),
        "compose_project": project,
        "app_name": app_name,
        "event_aliases": sorted(aliases),
        "progress": 0 if kind == "uninstall" else None,
        "determinate": kind == "uninstall",
        "phase": "starting",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "finished": False,
        "success": None,
        "error": None,
        "initial_container_count": max(0, container_count),
        "removed_container_ids": [],
    }
    with action_progress_lock:
        action_progress[stack_key] = record


def update_action_progress(stack_key, progress=None, *, determinate=None, phase=None):
    stack_key = str(stack_key or "").strip()
    if not stack_key:
        return
    with action_progress_lock:
        record = action_progress.get(stack_key)
        if not record or record.get("finished"):
            return

        if progress is not None:
            parsed = _clamp_progress(progress)
            if parsed is not None:
                # Never move a determinate progress bar backwards.
                current = record.get("progress")
                if current is None or parsed >= int(current):
                    record["progress"] = parsed
                record["determinate"] = True

        if determinate is not None:
            record["determinate"] = bool(determinate)
        if phase:
            record["phase"] = str(phase)
        record["updated_at"] = utc_now()


def finish_action_progress(stack_key, success, error=None):
    stack_key = str(stack_key or "").strip()
    if not stack_key:
        return
    with action_progress_lock:
        record = action_progress.get(stack_key)
        if not record:
            return
        record["finished"] = True
        record["success"] = bool(success)
        record["error"] = str(error) if error else None
        record["updated_at"] = utc_now()
        if success:
            record["progress"] = 100
            record["determinate"] = True
            record["phase"] = "complete"
        else:
            record["phase"] = "error"


def action_progress_snapshot(stack_key):
    stack_key = str(stack_key or "").strip()
    with action_progress_lock:
        record = action_progress.get(stack_key)
        if not record:
            return None
        # Deep-copy through JSON so callers cannot mutate nested lists.
        return json.loads(json.dumps(record))


def action_progress_public_snapshot():
    """Compact progress records for the lightweight live-status poll."""
    with action_progress_lock:
        result = {}
        for key, record in action_progress.items():
            if not isinstance(record, dict):
                continue
            result[str(key)] = {
                "stack_key": str(record.get("stack_key") or key),
                "kind": record.get("kind"),
                "automatic": bool(record.get("automatic")),
                "progress": record.get("progress"),
                "determinate": bool(record.get("determinate")),
                "phase": record.get("phase"),
                "started_at": record.get("started_at"),
                "updated_at": record.get("updated_at"),
                "finished": bool(record.get("finished")),
                "success": record.get("success"),
                "error": record.get("error"),
            }
        return json.loads(json.dumps(result))


def live_scan_snapshot():
    """Small scan state used by the 3-second live UI poll."""
    with scan_lock:
        return {
            "state": scan_state.get("state"),
            "started_at": scan_state.get("started_at"),
            "finished_at": scan_state.get("finished_at"),
            "progress_step": scan_state.get("progress_step"),
            "progress_percent": scan_state.get("progress_percent"),
            "progress_current": scan_state.get("progress_current"),
            "progress_total": scan_state.get("progress_total"),
        }


def _active_progress_key(kind=None, app_name=None):
    """
    Resolve an incoming ZimaOS event to the one active action.

    Update Monitor serializes update/uninstall operations with app_update_lock,
    so there can normally be only one. Name/project matching is preferred, but
    the single-active-action fallback also covers ZimaOS events whose app:name
    uses a display/container name rather than the Compose project ID.
    """
    kind = str(kind or "").strip().lower() or None
    app_name = str(app_name or "").strip().lower()

    with action_progress_lock:
        active = []
        matches = []
        for key, record in action_progress.items():
            if record.get("finished"):
                continue
            if kind and record.get("kind") != kind:
                continue
            active.append(key)

            if app_name:
                candidates = set(record.get("event_aliases") or [])
                if app_name in candidates:
                    matches.append(key)

        if app_name:
            return matches[0] if len(matches) == 1 else None
        if len(active) == 1:
            return active[0]
    return None


def handle_message_bus_event(event):
    if not isinstance(event, dict):
        return

    event_name = str(event.get("name") or "").strip()
    properties = event.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    app_name = str(
        properties.get("app:name")
        or properties.get("docker:container:name")
        or ""
    ).strip()

    with message_bus_state_lock:
        message_bus_state["last_event_at"] = utc_now()

    if event_name == "app:install-progress":
        key = _active_progress_key("update", app_name)
        if not key:
            return
        progress = _clamp_progress(properties.get("app:progress"))
        if progress is not None:
            update_action_progress(
                key,
                progress,
                determinate=True,
                phase="pulling",
            )
        return

    if event_name in {"app:update-begin", "docker:image:pull-begin"}:
        key = _active_progress_key("update", app_name)
        if key:
            update_action_progress(key, phase="pulling")
        return

    if event_name in {"docker:image:pull-end", "app:update-end"}:
        # Do not mark 100 here. ZimaOS emits pull/update-end before Update
        # Monitor's existing Docker verification necessarily finished.
        key = _active_progress_key("update", app_name)
        if key:
            update_action_progress(key, phase="activating")
        return

    if event_name == "app:uninstall-begin":
        key = _active_progress_key("uninstall", app_name)
        if key:
            update_action_progress(key, phase="removing")
        return

    if event_name == "docker:container:remove-end":
        key = _active_progress_key("uninstall", app_name)
        if not key:
            return

        container_id = str(properties.get("docker:container:id") or "").strip()
        container_name = str(properties.get("docker:container:name") or "").strip()
        token = container_id or container_name
        if not token:
            # No identity = cannot count this event safely.
            return

        with action_progress_lock:
            record = action_progress.get(key)
            if not record or record.get("finished"):
                return
            removed = list(record.get("removed_container_ids") or [])
            if token not in removed:
                removed.append(token)
                record["removed_container_ids"] = removed

            total = max(
                1,
                int(record.get("initial_container_count") or len(removed) or 1),
            )
            # Reserve the last 10% for ZimaOS cleanup / Compose deregistration.
            measured = min(90, int(round((len(removed) / total) * 90)))
            current = record.get("progress")
            if current is None or measured >= int(current):
                record["progress"] = measured
            record["determinate"] = True
            record["phase"] = "removing"
            record["updated_at"] = utc_now()
        return

    if event_name == "app:uninstall-end":
        key = _active_progress_key("uninstall", app_name)
        if key:
            update_action_progress(key, 95, determinate=True, phase="cleanup")


_MESSAGE_BUS_EVENT_NAMES = (
    "app:install-progress",
    "app:update-begin",
    "app:update-end",
    "docker:image:pull-begin",
    "docker:image:pull-end",
    "app:uninstall-begin",
    "app:uninstall-end",
    "docker:container:remove-end",
)


def _message_bus_ws_url():
    raw = MESSAGE_BUS_URL_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("message-bus.url is empty")

    parsed = urllib.parse.urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "ws", "wss"}:
        raise RuntimeError(f"Unsupported message bus URL scheme: {scheme or '-'}")

    ws_scheme = {
        "http": "ws",
        "https": "wss",
        "ws": "ws",
        "wss": "wss",
    }[scheme]

    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/v2/message_bus"):
        api_path = base_path
    else:
        api_path = base_path + "/v2/message_bus"

    event_path = api_path + "/event/app-management"
    query = urllib.parse.urlencode(
        [("names", name) for name in _MESSAGE_BUS_EVENT_NAMES]
    )
    return urllib.parse.urlunsplit((
        ws_scheme,
        parsed.netloc,
        event_path,
        query,
        "",
    ))


def _ws_send_client_frame(sock, opcode, payload=b""):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    payload = bytes(payload)
    first = 0x80 | (opcode & 0x0F)
    mask_key = os.urandom(4)
    length = len(payload)

    if length < 126:
        header = bytes([first, 0x80 | length])
    elif length <= 0xFFFF:
        header = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
    else:
        header = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)

    masked = bytes(
        byte ^ mask_key[index % 4]
        for index, byte in enumerate(payload)
    )
    sock.sendall(header + mask_key + masked)


class _SimpleWebSocket:
    """
    Minimal RFC 6455 client used only for the local ZimaOS MessageBus.
    This avoids adding another Python package to the Update Monitor image.
    """

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, url, timeout=35):
        self.url = url
        self.timeout = timeout
        self.sock = None
        self._connect()

    def _connect(self):
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {"ws", "wss"}:
            raise RuntimeError("WebSocket URL must use ws:// or wss://")
        host = parsed.hostname
        if not host:
            raise RuntimeError("WebSocket host is missing")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)

        sock = socket.create_connection((host, port), timeout=self.timeout)
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        sock.settimeout(self.timeout)

        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        default_port = 443 if parsed.scheme == "wss" else 80
        host_header = host if port == default_port else f"{host}:{port}"

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)

        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("MessageBus WebSocket closed during handshake")
            response.extend(chunk)
            if len(response) > 65536:
                raise RuntimeError("MessageBus WebSocket handshake is too large")

        header_bytes, remainder = bytes(response).split(b"\r\n\r\n", 1)
        header_text = header_bytes.decode("iso-8859-1", "replace")
        status_line = header_text.split("\r\n", 1)[0]
        if " 101 " not in f" {status_line} ":
            raise RuntimeError(
                f"MessageBus WebSocket handshake failed: {status_line}"
            )

        headers = {}
        for line in header_text.split("\r\n")[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

        expected = base64.b64encode(
            hashlib.sha1((key + self.GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise RuntimeError("MessageBus WebSocket handshake accept mismatch")

        self.sock = sock
        self._recv_buffer = bytearray(remainder)

    def _recv_exact(self, size):
        while len(self._recv_buffer) < size:
            chunk = self.sock.recv(max(4096, size - len(self._recv_buffer)))
            if not chunk:
                raise RuntimeError("MessageBus WebSocket closed")
            self._recv_buffer.extend(chunk)
        data = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]
        return data

    def recv_text(self):
        fragments = bytearray()
        fragment_opcode = None

        while True:
            head = self._recv_exact(2)
            first, second = head[0], head[1]
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]

            mask_key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length) if length else b""
            if mask_key:
                payload = bytes(
                    byte ^ mask_key[index % 4]
                    for index, byte in enumerate(payload)
                )

            if opcode == 0x8:  # close
                try:
                    _ws_send_client_frame(self.sock, 0x8, payload[:125])
                except Exception:
                    pass
                raise RuntimeError("MessageBus WebSocket closed")

            if opcode == 0x9:  # ping
                _ws_send_client_frame(self.sock, 0xA, payload)
                continue

            if opcode == 0xA:  # pong
                continue

            if opcode in {0x1, 0x2}:
                fragment_opcode = opcode
                fragments = bytearray(payload)
            elif opcode == 0x0 and fragment_opcode is not None:
                fragments.extend(payload)
            else:
                continue

            if not fin:
                continue

            if fragment_opcode != 0x1:
                fragments.clear()
                fragment_opcode = None
                continue

            text = bytes(fragments).decode("utf-8", "replace")
            return text

    def close(self):
        if not self.sock:
            return
        try:
            _ws_send_client_frame(self.sock, 0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None


def _set_message_bus_state(**changes):
    with message_bus_state_lock:
        message_bus_state.update(changes)


def message_bus_listener_loop():
    while True:
        ws = None
        try:
            url = _message_bus_ws_url()
            ws = _SimpleWebSocket(url, timeout=35)
            _set_message_bus_state(
                connected=True,
                last_connected_at=utc_now(),
                last_error=None,
            )

            while True:
                raw = ws.recv_text()
                event = json.loads(raw)
                handle_message_bus_event(event)

        except Exception as exc:
            _set_message_bus_state(
                connected=False,
                last_error=str(exc)[:500],
            )
            time.sleep(3)
        finally:
            if ws is not None:
                ws.close()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def save_json(path, data):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _normalize_github_token(value):
    token = str(value or "").strip()
    if not token:
        return ""
    if len(token) > 2048:
        raise ValueError("GitHub token is too long")
    if any(ch.isspace() for ch in token):
        raise ValueError("GitHub token must not contain whitespace")
    return token


def saved_github_token():
    try:
        return _normalize_github_token(GITHUB_TOKEN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def active_github_token():
    saved = saved_github_token()
    if saved:
        return saved, "saved"
    env_token = _normalize_github_token(UPDATE_MONITOR_GITHUB_TOKEN)
    if env_token:
        return env_token, "environment"
    return "", "none"


def github_token_public_status():
    token, source = active_github_token()
    masked = None
    if token:
        suffix = token[-4:] if len(token) >= 4 else token
        masked = "••••••••" + suffix
    return {
        "configured": bool(token),
        "source": source,
        "masked": masked,
        "removable": source == "saved",
    }


def save_github_token_secret(token):
    token = _normalize_github_token(token)
    if not token:
        raise ValueError("Enter a GitHub token")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = GITHUB_TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, GITHUB_TOKEN_FILE)
        os.chmod(GITHUB_TOKEN_FILE, 0o600)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def remove_saved_github_token():
    try:
        GITHUB_TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass


def clear_github_version_cache():
    with GITHUB_VERSION_CACHE_LOCK:
        try:
            GITHUB_VERSION_CACHE_FILE.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def is_authenticated(request: Request):
    return request.session.get("authenticated") is True


def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")


def reject_mutation_during_scan():
    """Block settings and app mutations while the full update check is running."""
    if scan_state.get("state") == "scanning":
        raise HTTPException(
            status_code=409,
            detail="Wait until the current update check is finished",
        )


def client_key(request: Request):
    return request.client.host if request.client and request.client.host else "unknown"


def rate_limited(key):
    now = time.monotonic()
    with LOGIN_RATE_LIMIT_LOCK:
        failures = [x for x in login_failures.get(key, []) if now - x <= LOGIN_FAILURE_WINDOW_SECONDS]
        login_failures[key] = failures
        return len(failures) >= LOGIN_FAILURE_LIMIT and now - failures[-1] <= LOGIN_BLOCK_SECONDS


def record_failure(key):
    now = time.monotonic()
    with LOGIN_RATE_LIMIT_LOCK:
        failures = [x for x in login_failures.get(key, []) if now - x <= LOGIN_FAILURE_WINDOW_SECONDS]
        failures.append(now)
        login_failures[key] = failures


def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Timeout"
    except FileNotFoundError as exc:
        return 127, "", str(exc)



def normalize_repo(repo):
    parts = repo.split("/")
    first = parts[0]
    if len(parts) == 1:
        return "docker.io/library/" + repo
    if "." in first or ":" in first or first == "localhost":
        return repo
    return "docker.io/" + repo


def parse_image_ref(ref):
    pinned = None
    base = ref
    if "@" in base:
        base, pinned = base.rsplit("@", 1)
    last = base.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = base.rsplit(":", 1)
    else:
        repo, tag = base, "latest"
    return {"original": ref, "base": base, "repo": repo, "normalized_repo": normalize_repo(repo), "tag": tag, "pinned_digest": pinned}


def parse_repo_digest(value):
    if "@" not in value:
        return None, None
    repo, digest = value.rsplit("@", 1)
    return normalize_repo(repo), digest


PROJECT_LINK_LABEL_KEYS = (
    # Source repository labels first.
    "org.opencontainers.image.source",
    "org.label-schema.vcs-url",
    "org.opencontainers.image.vendor-url",
    # Project/homepage labels after source labels.
    "org.opencontainers.image.url",
    "org.label-schema.url",
    "org.opencontainers.image.documentation",
)


def normalize_project_url(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    host = (parsed.hostname or "").lower()

    # Project buttons must open the project itself, never a README asset,
    # workflow badge, issue, release page, etc.  GitHub image labels and Docker
    # Hub descriptions frequently contain badge URLs such as:
    #   github.com/LibreTranslate/LibreTranslate/workflows/.../badge.svg
    # Canonicalize every GitHub project URL to OWNER/REPO.
    if host in {"github.com", "www.github.com"}:
        parts = [
            urllib.parse.unquote(part).strip()
            for part in (parsed.path or "").split("/")
            if urllib.parse.unquote(part).strip()
        ]
        if len(parts) >= 2:
            owner = parts[0]
            repo = parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            if owner and repo:
                return "https://github.com/" + urllib.parse.quote(owner, safe="@._-") + "/" + urllib.parse.quote(repo, safe="@._-")

    # Fragments do not identify a different project and make cache comparison
    # unnecessarily noisy. Preserve queries on generic project sites because
    # some use them for canonical project pages.
    parsed = parsed._replace(fragment="")
    normalized = urllib.parse.urlunparse(parsed).rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized or None


def project_provider_from_url(value):
    url = normalize_project_url(value)
    if not url:
        return None

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in {"github.com", "www.github.com"}:
        return "github"
    if host in {"codeberg.org", "www.codeberg.org"}:
        return "codeberg"
    if host in {"gitlab.com", "www.gitlab.com"} or "gitlab" in host:
        return "gitlab"
    if "forgejo" in host:
        return "forgejo"
    if "gitea" in host:
        return "gitea"
    return "project"


def project_link_from_labels(labels):
    if not isinstance(labels, dict):
        return None

    for label_key in PROJECT_LINK_LABEL_KEYS:
        candidate = normalize_project_url(labels.get(label_key))
        if candidate:
            return {
                "url": candidate,
                "provider": project_provider_from_url(candidate),
                "source": f"label:{label_key}",
            }
    return None


def project_link_cache_key(image_ref):
    try:
        parsed = parse_image_ref(str(image_ref or "").strip())
        return str(parsed.get("normalized_repo") or "").strip().lower()
    except Exception:
        return ""


def _project_link_entry_age_seconds(entry):
    checked = str((entry or {}).get("checked_at") or "").strip()
    if not checked:
        return None
    try:
        then = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
    except Exception:
        return None


def project_link_cache_get(image_ref):
    key = project_link_cache_key(image_ref)
    if not key:
        return None

    changed = False
    with PROJECT_LINK_CACHE_LOCK:
        raw = PROJECT_LINK_CACHE.get(key)
        entry = dict(raw) if isinstance(raw, dict) else None
        if not entry:
            return None

        original_url = str(entry.get("url") or "").strip() or None
        normalized_url = normalize_project_url(original_url)
        if normalized_url != original_url:
            entry["url"] = normalized_url
            entry["provider"] = project_provider_from_url(normalized_url)
            entry["source"] = str(entry.get("source") or "cache") + ":canonicalized"
            PROJECT_LINK_CACHE[key] = dict(entry)
            changed = True

        if changed:
            save_json(PROJECT_LINK_CACHE_FILE, PROJECT_LINK_CACHE)

    return entry


def project_link_cache_put(image_ref, *, url=None, provider=None, source=None):
    key = project_link_cache_key(image_ref)
    if not key:
        return None

    normalized_url = normalize_project_url(url)
    entry = {
        "image_repo": key,
        "url": normalized_url,
        "provider": provider or project_provider_from_url(normalized_url),
        "source": str(source or "unknown"),
        "checked_at": utc_now(),
    }
    with PROJECT_LINK_CACHE_LOCK:
        PROJECT_LINK_CACHE[key] = entry
        save_json(PROJECT_LINK_CACHE_FILE, PROJECT_LINK_CACHE)
    return dict(entry)


def project_link_cache_is_fresh(entry):
    age = _project_link_entry_age_seconds(entry)
    if age is None:
        return False
    ttl = (
        PROJECT_LINK_CACHE_TTL_SECONDS
        if (entry or {}).get("url")
        else PROJECT_LINK_NEGATIVE_TTL_SECONDS
    )
    return age <= ttl


def inferred_project_link_from_image_ref(image_ref):
    """
    Conservative registry-name inference.

    Only mappings with a strong registry-to-forge relationship are inferred.
    Unknown registries are not guessed.
    """
    parsed = parse_image_ref(str(image_ref or "").strip())
    repo = str(parsed.get("normalized_repo") or "").strip()
    if not repo:
        return None

    if repo.startswith("ghcr.io/"):
        parts = [part for part in repo[len("ghcr.io/"):].split("/") if part]
        if len(parts) >= 2:
            url = f"https://github.com/{parts[0]}/{parts[1]}"
            return {"url": url, "provider": "github", "source": "registry:ghcr"}

    if repo.startswith("codeberg.org/"):
        parts = [part for part in repo[len("codeberg.org/"):].split("/") if part]
        if len(parts) >= 2:
            url = f"https://codeberg.org/{parts[0]}/{parts[1]}"
            return {"url": url, "provider": "codeberg", "source": "registry:codeberg"}

    if repo.startswith("registry.gitlab.com/"):
        parts = [part for part in repo[len("registry.gitlab.com/"):].split("/") if part]
        # Exact owner/project paths are safe to infer.  Deeper registry paths
        # can represent nested image names, so do not guess those.
        if len(parts) == 2:
            url = f"https://gitlab.com/{parts[0]}/{parts[1]}"
            return {"url": url, "provider": "gitlab", "source": "registry:gitlab"}

    return None


PROJECT_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)


def _best_project_url_from_text(text):
    """
    Extract a likely forge/project URL from human-readable registry metadata.

    Known source forges are preferred.  Generic URLs are deliberately ignored
    here to avoid caching badges, documentation assets or unrelated links from
    a Docker Hub README.
    """
    candidates = []
    seen = set()
    for raw in PROJECT_URL_RE.findall(str(text or "")):
        candidate = normalize_project_url(raw.rstrip(".,;:!?"))
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        provider = project_provider_from_url(candidate)
        if provider in {"github", "codeberg", "gitlab", "forgejo", "gitea"}:
            candidates.append((candidate, provider))

    if not candidates:
        return None

    provider_order = {"github": 0, "codeberg": 1, "gitlab": 2, "forgejo": 3, "gitea": 4}
    candidates.sort(key=lambda item: provider_order.get(item[1], 99))
    url, provider = candidates[0]
    return {"url": url, "provider": provider, "source": "registry:description"}


DOCKER_HUB_REGISTRY_PREFIXES = (
    "docker.io/",
    "index.docker.io/",
    "registry-1.docker.io/",
)
PROJECT_FORGE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def docker_hub_owner_repo(image_ref):
    """Return an exact Docker Hub owner/repository pair when it is safe to infer."""
    value = str(image_ref or "").strip()
    if not value:
        return None

    # A digest never changes the repository identity.
    value = value.split("@", 1)[0].strip()
    lowered = value.lower()
    for prefix in DOCKER_HUB_REGISTRY_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix):]
            lowered = value.lower()
            break

    # Do not reinterpret an explicitly named non-Docker-Hub registry as an
    # owner. This keeps the fallback conservative and avoids wrong links.
    first = value.split("/", 1)[0]
    if "." in first or ":" in first or first.lower() == "localhost":
        return None

    parts = [part for part in value.split("/") if part]
    if len(parts) != 2:
        return None

    owner, repo = parts
    if ":" in repo:
        repo = repo.rsplit(":", 1)[0]

    if not owner or not repo:
        return None
    if not PROJECT_FORGE_COMPONENT_RE.fullmatch(owner):
        return None
    if not PROJECT_FORGE_COMPONENT_RE.fullmatch(repo):
        return None
    return owner, repo


def _forge_repository_exists(api_url, *, github=False):
    """Probe one fixed public forge API. Never follows a user supplied host."""
    headers = {
        "User-Agent": "Update-Monitor/project-link-discovery",
        "Accept": "application/json",
    }
    if github:
        headers["Accept"] = "application/vnd.github+json"
        try:
            token, _source = active_github_token()
        except Exception:
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(api_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def verified_forge_project_link(image_ref):
    """
    Last-resort source discovery for Docker Hub images that expose no usable
    OCI labels and no project URL in registry metadata.

    Only an exact owner/repository pair is considered. GitHub, GitLab and
    Codeberg are queried concurrently, and a link is returned only after the
    corresponding repository API confirms that it exists. Preference order is
    GitHub, then GitLab, then Codeberg when more than one exact match exists.
    """
    owner_repo = docker_hub_owner_repo(image_ref)
    if not owner_repo:
        return None

    owner, repo = owner_repo
    encoded_path = urllib.parse.quote(f"{owner}/{repo}", safe="")
    probes = [
        (
            "github",
            f"https://api.github.com/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}",
            f"https://github.com/{owner}/{repo}",
            True,
        ),
        (
            "gitlab",
            f"https://gitlab.com/api/v4/projects/{encoded_path}",
            f"https://gitlab.com/{owner}/{repo}",
            False,
        ),
        (
            "codeberg",
            f"https://codeberg.org/api/v1/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}",
            f"https://codeberg.org/{owner}/{repo}",
            False,
        ),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {
            provider: pool.submit(_forge_repository_exists, api_url, github=is_github)
            for provider, api_url, _project_url, is_github in probes
        }
        for provider, _api_url, project_url, _is_github in probes:
            try:
                exists = bool(futures[provider].result(timeout=5))
            except Exception:
                exists = False
            if exists:
                return {
                    "url": project_url,
                    "provider": provider,
                    "source": f"forge-probe:{provider}",
                }

    return None


def docker_hub_project_link(image_ref):
    """
    Last-resort metadata fallback for Docker Hub.

    The public repository description often contains the upstream Git forge
    even when the image itself uses old or missing OCI labels.
    """
    parsed = parse_image_ref(str(image_ref or "").strip())
    repo = str(parsed.get("normalized_repo") or "").strip()
    if not repo.startswith("docker.io/"):
        return None

    path = repo[len("docker.io/"):]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        return None

    namespace, name = parts
    endpoint = (
        "https://hub.docker.com/v2/repositories/"
        + urllib.parse.quote(namespace, safe="")
        + "/"
        + urllib.parse.quote(name, safe="")
        + "/"
    )
    req = urllib.request.Request(
        endpoint,
        headers={
            "User-Agent": f"Update-Monitor/{VERSION}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = response.read(1024 * 1024)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    text = "\n".join(
        str(payload.get(key) or "")
        for key in ("description", "full_description")
    )
    return _best_project_url_from_text(text)


def image_project_link(image_ref):
    """
    Read project/source labels from the locally installed Docker image.
    Returns URL + provider + exact metadata source.
    """
    value = str(image_ref or "").strip()
    if not value:
        return None

    rc, out, _ = run(
        ["docker", "image", "inspect", value, "--format", "{{json .Config.Labels}}"],
        20,
    )
    if rc != 0 or not out:
        return None

    try:
        labels = json.loads(out)
    except Exception:
        return None

    return project_link_from_labels(labels)


def resolve_project_link(image_ref, metadata_url=None, metadata_source=None):
    """
    Resolve and persist an upstream project/source link for an image.

    Order:
      1. Metadata already available from the running container
      2. Fresh persistent cache
      3. Labels on the locally installed image
      4. Strong registry-name inference (GHCR / Codeberg / GitLab)
      5. Docker Hub description metadata
      6. Stale positive cache (never throw away a previously useful link)
      7. Short-lived negative cache
    """
    direct = normalize_project_url(metadata_url)
    if direct:
        return project_link_cache_put(
            image_ref,
            url=direct,
            provider=project_provider_from_url(direct),
            source=metadata_source or "container-label",
        )

    cached = project_link_cache_get(image_ref)
    if cached and project_link_cache_is_fresh(cached):
        # Positive entries remain authoritative. Negative entries created by
        # older Update Monitor versions are intentionally rechecked once so
        # the verified forge fallback can discover links that were previously
        # unavailable. Current-generation negative results keep the normal TTL.
        if cached.get("url") or str(cached.get("source") or "") == "not-found:v2":
            return cached

    local = image_project_link(image_ref)
    if local and local.get("url"):
        return project_link_cache_put(
            image_ref,
            url=local.get("url"),
            provider=local.get("provider"),
            source=local.get("source"),
        )

    inferred = inferred_project_link_from_image_ref(image_ref)
    if inferred and inferred.get("url"):
        return project_link_cache_put(
            image_ref,
            url=inferred.get("url"),
            provider=inferred.get("provider"),
            source=inferred.get("source"),
        )

    registry = docker_hub_project_link(image_ref)
    if registry and registry.get("url"):
        return project_link_cache_put(
            image_ref,
            url=registry.get("url"),
            provider=registry.get("provider"),
            source=registry.get("source"),
        )

    verified_forge = verified_forge_project_link(image_ref)
    if verified_forge and verified_forge.get("url"):
        return project_link_cache_put(
            image_ref,
            url=verified_forge.get("url"),
            provider=verified_forge.get("provider"),
            source=verified_forge.get("source"),
        )

    # Preserve a previously known positive link if a later revalidation cannot
    # rediscover it.  This prevents temporary registry/network problems from
    # making project links disappear from the UI.
    if cached and cached.get("url"):
        return project_link_cache_put(
            image_ref,
            url=cached.get("url"),
            provider=cached.get("provider"),
            source=str(cached.get("source") or "cache") + ":retained",
        )

    return project_link_cache_put(
        image_ref,
        url=None,
        provider=None,
        source="not-found:v2",
    )


def _remote_digest_error_is_transient(message):
    value = str(message or "").lower()
    transient_markers = (
        "429",
        "too many requests",
        "rate limit",
        "toomanyrequests",
        "temporarily unavailable",
        "temporary failure",
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "tls handshake",
        "unexpected eof",
        "502",
        "503",
        "504",
    )
    return any(marker in value for marker in transient_markers)


def remote_digests(image_ref, platform):
    last_error = None

    # One retry is enough to absorb short registry/network hiccups without
    # making a large scan unnecessarily slow when a registry is truly down.
    for attempt in range(2):
        rc, out, err = run(
            ["docker", "buildx", "imagetools", "inspect", image_ref],
            90,
        )
        if rc != 0:
            last_error = err or out or f"Exit {rc}"
            if attempt == 0 and _remote_digest_error_is_transient(last_error):
                time.sleep(1.2)
                continue
            return None, None, last_error

        top = None
        p_digest = None
        current = None
        for line in out.splitlines():
            s = line.strip()
            if top is None and s.startswith("Digest:"):
                v = s.split(":", 1)[1].strip()
                if v.startswith("sha256:"):
                    top = v
            if s.startswith("Name:") and "@sha256:" in s:
                m = re.search(r"@(sha256:[0-9a-fA-F]+)", s)
                current = m.group(1) if m else None
            if (
                s.startswith("Platform:")
                and s.split(":", 1)[1].strip() == platform
                and current
            ):
                p_digest = current

        if top:
            return top, p_digest, None

        last_error = "No remote digest found"
        if attempt == 0:
            time.sleep(0.6)

    return None, None, last_error or "No remote digest found"


def remote_manifest_config_digest(image_ref, manifest_digest):
    """Return the OCI config digest for one concrete remote manifest.

    Docker image IDs are config digests.  This gives us a second, registry-backed
    proof path for images whose local Docker metadata has lost RepoDigests (for
    example after retagging/importing).  It prevents a normal registry image from
    being mislabeled as a purely local image just because RepoDigests is empty.
    """
    digest = str(manifest_digest or "").strip().lower()
    if not digest.startswith("sha256:"):
        return None, "No concrete remote manifest digest"

    parsed = parse_image_ref(str(image_ref or "").strip())
    base = str(parsed.get("base") or "").strip()
    if not base:
        return None, "Invalid image reference"

    target = f"{base}@{digest}"
    rc, out, err = run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", target],
        90,
    )
    if rc != 0:
        return None, err or out or f"Exit {rc}"

    try:
        payload = json.loads(out) if out else {}
    except Exception as exc:
        return None, f"Invalid remote manifest JSON: {exc}"

    config = payload.get("config") if isinstance(payload, dict) else None
    config_digest = (config or {}).get("digest") if isinstance(config, dict) else None
    config_digest = str(config_digest or "").strip().lower()
    if config_digest.startswith("sha256:"):
        return config_digest, None
    return None, "No config digest in remote manifest"


def registry_parts(normalized):
    if normalized.startswith("docker.io/"):
        return "registry-1.docker.io", normalized[len("docker.io/"):]
    return normalized.split("/", 1)


def parse_bearer(header):
    if not header or not header.lower().startswith("bearer "):
        return None
    d = dict(re.findall(r'(\w+)="([^"]*)"', header[7:]))
    return d if "realm" in d else None


def http_json(url, headers=None, timeout=20):
    h = {"User-Agent": f"Update-Monitor/{VERSION}", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                payload = json.loads(body) if body else {}
            except Exception:
                return 0, resp.headers, {"error": "Remote server returned invalid JSON"}
            return resp.status, resp.headers, payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {"raw": body}
        return exc.code, exc.headers, payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        return 0, {}, {"error": str(reason)[:300]}


def test_github_token(token=None):
    if token is None:
        token, source = active_github_token()
    else:
        token = _normalize_github_token(token)
        source = "provided"

    if not token:
        raise RuntimeError("No GitHub token is configured")

    status, headers, payload = http_json(
        "https://api.github.com/user",
        headers=_github_api_headers(token),
        timeout=20,
    )
    if status == 401:
        raise RuntimeError("GitHub rejected the token (HTTP 401)")
    if status == 403:
        message = str(payload.get("message") or "Forbidden") if isinstance(payload, dict) else "Forbidden"
        raise RuntimeError(f"GitHub rejected the request (HTTP 403): {message[:180]}")
    if status == 0:
        detail = str(payload.get("error") or "network error") if isinstance(payload, dict) else "network error"
        raise RuntimeError(f"GitHub is not reachable: {detail[:180]}")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"GitHub token test failed (HTTP {status})")

    def _header_int(name):
        try:
            return int(headers.get(name))
        except Exception:
            return None

    limit = _header_int("X-RateLimit-Limit")
    remaining = _header_int("X-RateLimit-Remaining")
    reset_raw = _header_int("X-RateLimit-Reset")
    reset_at = None
    if reset_raw:
        try:
            reset_at = datetime.fromtimestamp(reset_raw, timezone.utc).isoformat()
        except Exception:
            pass

    return {
        "authenticated": True,
        "login": str(payload.get("login") or "").strip() or None,
        "limit": limit,
        "remaining": remaining,
        "reset_at": reset_at,
        "source": source,
    }



def registry_tags(normalized, max_pages=5):
    host, repo = registry_parts(normalized)
    url = f"https://{host}/v2/{repo}/tags/list?n=1000"
    headers = {}
    tags = []
    pages = 0
    while url and pages < max_pages:
        pages += 1
        status, rh, payload = http_json(url, headers, 25)
        if status == 401 and "Authorization" not in headers:
            challenge = parse_bearer(rh.get("WWW-Authenticate"))
            if not challenge:
                return None, "Unsupported registry authentication"
            params = {"scope": challenge.get("scope") or f"repository:{repo}:pull"}
            if challenge.get("service"):
                params["service"] = challenge["service"]
            sep = "&" if "?" in challenge["realm"] else "?"
            token_url = challenge["realm"] + sep + urllib.parse.urlencode(params)
            ts, _, tp = http_json(token_url, timeout=20)
            token = (tp.get("token") or tp.get("access_token")) if ts == 200 else None
            if not token:
                return None, f"Token request HTTP {ts}"
            headers["Authorization"] = "Bearer " + token
            pages -= 1
            continue
        if status != 200:
            return None, f"Tag list HTTP {status}"
        tags.extend(str(x) for x in (payload.get("tags") or []) if x)
        link = rh.get("Link")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        if m:
            url = m.group(1)
            if url.startswith("/"):
                url = f"https://{host}{url}"
        else:
            url = None
    return sorted(set(tags)), None


_REGISTRY_MANIFEST_ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


INSTALLED_VERSION_CACHE_SCHEMA = "v3-uniform-digest-verified"

def _installed_version_cache_key(repo_key, local_digests):
    digests = sorted(
        str(value or "").strip().lower()
        for value in (local_digests or [])
        if str(value or "").strip()
    )
    raw = (
        INSTALLED_VERSION_CACHE_SCHEMA
        + "|"
        + str(repo_key or "").strip().lower()
        + "|"
        + "|".join(digests)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def installed_version_cache_get(repo_key, local_digests, selectable=None):
    current_digests = {
        str(value or "").strip().lower()
        for value in (local_digests or [])
        if str(value or "").strip()
    }
    allowed = (
        {str(x) for x in selectable}
        if selectable is not None
        else None
    )

    key = _installed_version_cache_key(repo_key, current_digests)
    with INSTALLED_VERSION_CACHE_LOCK:
        exact = dict(INSTALLED_VERSION_CACHE.get(key) or {})
        entries = [
            dict(value or {})
            for value in INSTALLED_VERSION_CACHE.values()
            if isinstance(value, dict)
        ]

    def valid_entry(entry):
        tag = str(entry.get("tag") or "").strip()
        if not tag:
            return None
        try:
            age = time.time() - float(entry.get("saved_at") or 0)
        except Exception:
            return None
        if age > INSTALLED_VERSION_CACHE_TTL_SECONDS:
            return None
        if allowed is not None and tag not in allowed:
            return None
        return tag

    tag = valid_entry(exact)
    if tag:
        return tag

    # v0.3.296: never infer one installed version from a partial digest overlap.
    # Multi-container stacks can temporarily run two different image revisions.
    # In that state, reusing a cache entry for only one matching digest would make
    # the whole app look like one concrete version even though it is mixed.
    # The exact digest-set cache key above remains safe; otherwise resolve again.
    return None


def installed_version_cache_put(repo_key, local_digests, tag):
    tag = str(tag or "").strip()
    if not tag:
        return None
    key = _installed_version_cache_key(repo_key, local_digests)
    value = {
        "repo": str(repo_key or "").strip().lower(),
        "local_digests": sorted(
            str(x or "").strip().lower()
            for x in (local_digests or [])
            if str(x or "").strip()
        ),
        "tag": tag,
        "saved_at": time.time(),
    }
    with INSTALLED_VERSION_CACHE_LOCK:
        INSTALLED_VERSION_CACHE[key] = value
        save_json(INSTALLED_VERSION_CACHE_FILE, INSTALLED_VERSION_CACHE)
    return tag


def _registry_cached_bearer(host, repo):
    key = (str(host), str(repo))
    now = time.time()
    with REGISTRY_MANIFEST_TOKEN_CACHE_LOCK:
        entry = REGISTRY_MANIFEST_TOKEN_CACHE.get(key)
        if entry and now - float(entry.get("at") or 0) < REGISTRY_MANIFEST_TOKEN_CACHE_TTL_SECONDS:
            token = str(entry.get("token") or "").strip()
            if token:
                return token
    return None


def _registry_store_bearer(host, repo, token):
    token = str(token or "").strip()
    if not token:
        return
    with REGISTRY_MANIFEST_TOKEN_CACHE_LOCK:
        REGISTRY_MANIFEST_TOKEN_CACHE[(str(host), str(repo))] = {
            "token": token,
            "at": time.time(),
        }


def _registry_manifest_token(host, repo, challenge_header):
    challenge = parse_bearer(challenge_header)
    if not challenge:
        return None, "Unsupported registry authentication"

    params = {"scope": challenge.get("scope") or f"repository:{repo}:pull"}
    if challenge.get("service"):
        params["service"] = challenge["service"]
    sep = "&" if "?" in challenge["realm"] else "?"
    token_url = challenge["realm"] + sep + urllib.parse.urlencode(params)
    status, _, payload = http_json(token_url, timeout=12)
    token = (
        (payload.get("token") or payload.get("access_token"))
        if status == 200 and isinstance(payload, dict)
        else None
    )
    if not token:
        return None, f"Token request HTTP {status}"
    _registry_store_bearer(host, repo, token)
    return token, None


def _platform_manifest_digest(payload, platform):
    if not isinstance(payload, dict):
        return None
    manifests = payload.get("manifests")
    if not isinstance(manifests, list):
        return None

    parts = [x for x in str(platform or "").strip().lower().split("/") if x]
    if len(parts) < 2:
        return None
    wanted_os = parts[0]
    wanted_arch = parts[1]
    wanted_variant = parts[2] if len(parts) > 2 else None

    for descriptor in manifests:
        if not isinstance(descriptor, dict):
            continue
        p = descriptor.get("platform") or {}
        if str(p.get("os") or "").lower() != wanted_os:
            continue
        if str(p.get("architecture") or "").lower() != wanted_arch:
            continue
        variant = str(p.get("variant") or "").lower() or None
        if wanted_variant and variant and variant != wanted_variant:
            continue
        digest = str(descriptor.get("digest") or "").strip()
        if digest.startswith("sha256:"):
            return digest
    return None


def registry_manifest_digests(normalized, tag, platform=None, timeout=8):
    """
    Return digest candidates for one exact Docker/OCI tag using the registry API.
    The set may contain both the index/manifest-list digest and the selected
    platform-manifest digest.
    """
    host, repo = registry_parts(normalized)
    quoted_tag = urllib.parse.quote(str(tag or "").strip(), safe="")
    if not quoted_tag:
        return set(), "Empty tag"

    url = f"https://{host}/v2/{repo}/manifests/{quoted_tag}"
    headers = {
        "User-Agent": f"Update-Monitor/{VERSION}",
        "Accept": _REGISTRY_MANIFEST_ACCEPT,
    }
    cached_token = _registry_cached_bearer(host, repo)
    if cached_token:
        headers["Authorization"] = "Bearer " + cached_token

    for attempt in range(2):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(4 * 1024 * 1024)
                digests = set()

                top_digest = str(resp.headers.get("Docker-Content-Digest") or "").strip()
                if top_digest.startswith("sha256:"):
                    digests.add(top_digest)

                payload = {}
                if raw:
                    try:
                        payload = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:
                        payload = {}

                platform_digest = _platform_manifest_digest(payload, platform)
                if platform_digest:
                    digests.add(platform_digest)

                return digests, None
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                token, token_error = _registry_manifest_token(
                    host,
                    repo,
                    exc.headers.get("WWW-Authenticate"),
                )
                if token:
                    headers["Authorization"] = "Bearer " + token
                    continue
                return set(), token_error or "Registry authentication failed"
            return set(), f"Manifest HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            return set(), str(reason)[:240]

    return set(), "Manifest lookup failed"


def _needs_installed_version_resolution(tag):
    value = str(tag or "").strip()
    if not value:
        return True
    if value.lower() in _DYNAMIC_REGISTRY_TAGS:
        return True
    return not bool(re.search(r"\d", value))


def _all_local_digest_groups_match_remote(digest_groups, remote_digests):
    """Return True only when every active local image has a remote-matching digest.

    One Compose app can have several containers using the same image reference but
    temporarily different image IDs (for example after a partial recreate).  A union
    of their RepoDigests is not sufficient: one matching container must never make
    the entire deployment look current.
    """
    remote = {
        str(value or "").strip().lower()
        for value in (remote_digests or [])
        if str(value or "").strip()
    }
    groups = []
    for values in (digest_groups or []):
        group = {
            str(value or "").strip().lower()
            for value in (values or [])
            if str(value or "").strip()
        }
        groups.append(group)

    if not remote or not groups:
        return False
    return all(bool(group and group.intersection(remote)) for group in groups)


def resolve_installed_version_tag(
    repo_key,
    local_digests,
    selectable,
    platform=None,
    batch_size=8,
    deadline_seconds=18.0,
    cache_result=True,
):
    """
    Resolve latest/stable/etc. to the first concrete selectable tag whose
    registry digest matches the locally installed image.

    The interactive version picker keeps the fast defaults. The normal full scan
    may use a smaller batch size and a longer deadline so dozens of apps do not
    create a registry request storm.
    """
    local = {
        str(value or "").strip().lower()
        for value in (local_digests or [])
        if str(value or "").strip()
    }
    candidates = [
        str(tag or "").strip()
        for tag in (selectable or [])
        if str(tag or "").strip()
    ]
    if not repo_key or not local or not candidates:
        return None

    cached = installed_version_cache_get(repo_key, local, candidates)
    if cached:
        return cached

    try:
        batch_size = max(1, min(8, int(batch_size or 1)))
    except Exception:
        batch_size = 8
    try:
        deadline_seconds = max(5.0, min(60.0, float(deadline_seconds or 18.0)))
    except Exception:
        deadline_seconds = 18.0

    deadline = time.monotonic() + deadline_seconds

    for offset in range(0, len(candidates), batch_size):
        if time.monotonic() >= deadline:
            break
        batch = candidates[offset:offset + batch_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [
                executor.submit(
                    registry_manifest_digests,
                    repo_key,
                    tag,
                    platform,
                    7,
                )
                for tag in batch
            ]
            # Keep candidate order: if several aliases point to the same digest,
            # the first concrete tag in the already-sorted selector wins.
            for tag, future in zip(batch, futures):
                try:
                    digests, _ = future.result(timeout=8)
                except Exception:
                    digests = set()
                normalized = {
                    str(value or "").strip().lower()
                    for value in (digests or [])
                    if str(value or "").strip()
                }
                # v0.3.296: a concrete installed version is proven only when
                # every active local digest belongs to this same registry tag.
                # A single matching digest is insufficient for multi-container
                # stacks that are temporarily split across image revisions.
                if local and local.issubset(normalized):
                    if cache_result:
                        return installed_version_cache_put(repo_key, local, tag)
                    return tag

    return None


VERSION_RE = re.compile(r'^(?P<prefix>v)?(?P<num>\d+(?:\.\d+){1,3})(?:[-._]?(?P<pre>alpha|beta|rc)(?:[-._]?(?P<prenum>\d+))?)?$', re.I)


def parse_version(tag):
    m = VERSION_RE.fullmatch(tag)
    if not m:
        return None
    nums = tuple(int(x) for x in m.group("num").split(".")) + (0,) * (4 - len(m.group("num").split(".")))
    pre = (m.group("pre") or "").lower()
    rank = {"alpha": 0, "beta": 1, "rc": 2, "": 3}[pre]
    return {"tag": tag, "prefix": "v" if m.group("prefix") else "", "key": nums + (rank, int(m.group("prenum") or 0)), "pre": pre}


def compatible_newer_tags(current_tag, tags, limit=30):
    current = parse_version(current_tag)
    if not current:
        return []

    candidates = []
    for tag in tags:
        p = parse_version(tag)
        if not p or p["prefix"] != current["prefix"]:
            continue

        # Keep stable users on stable releases; prerelease users stay within
        # the same prerelease family or may move to the final stable release.
        if not current["pre"] and p["pre"]:
            continue
        if current["pre"] and p["pre"] not in {current["pre"], ""}:
            continue
        if p["key"] > current["key"]:
            candidates.append(p)

    candidates.sort(key=lambda x: x["key"], reverse=True)
    return [x["tag"] for x in candidates[:limit]]




def compatible_newer_tags_relaxed_prefix(current_tag, tags, limit=100):
    """
    Version comparison used for GitHub <-> registry matching.

    GitHub releases commonly use tags such as v1.2.3 while a Docker image may
    use 1.2.3 (or the other way around). For this comparison the optional
    leading "v" is intentionally ignored, while the existing stable/prerelease
    rules remain unchanged.
    """
    current = parse_version(current_tag)
    if not current:
        return []

    candidates = []
    seen = set()
    for tag in tags or []:
        tag = str(tag or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)

        parsed = parse_version(tag)
        if not parsed:
            continue
        if not current["pre"] and parsed["pre"]:
            continue
        if current["pre"] and parsed["pre"] not in {current["pre"], ""}:
            continue
        if parsed["key"] > current["key"]:
            candidates.append(parsed)

    candidates.sort(key=lambda item: item["key"], reverse=True)
    return [item["tag"] for item in candidates[:limit]]


def github_repo_from_url(project_url):
    """Return (owner, repo) for an ordinary github.com repository URL."""
    value = str(project_url or "").strip()
    if not value:
        return None

    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return None

    host = str(parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        return None

    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0].strip()
    repo = parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]

    # Keep generated API URLs constrained to normal GitHub path components.
    valid = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not owner or not repo or not valid.fullmatch(owner) or not valid.fullmatch(repo):
        return None

    return owner, repo



def github_url_from_image_ref(image_ref):
    """
    Derive the most likely GitHub repository from a GHCR image name.

    ghcr.io/OWNER/REPO[:tag] -> https://github.com/OWNER/REPO

    GHCR package names may contain additional path components. The first two
    components after ghcr.io are the GitHub owner/repository candidate.
    """
    value = str(image_ref or "").strip()
    if not value:
        return None

    parsed = parse_image_ref(value)
    normalized = str(parsed.get("normalized_repo") or "").strip()
    if not normalized.startswith("ghcr.io/"):
        return None

    path = normalized[len("ghcr.io/"):]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    valid = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not valid.fullmatch(owner) or not valid.fullmatch(repo):
        return None

    return f"https://github.com/{owner}/{repo}"


def image_project_url(image_ref):
    """Compatibility wrapper returning only the resolved project URL."""
    link = resolve_project_link(image_ref)
    return (link or {}).get("url")


def app_github_project_url(app_item, image_ref=None):
    """
    Find a GitHub project URL without depending on one specific metadata source.

    Priority:
      1. App/container metadata already discovered by Update Monitor
      2. Per-image metadata kept in the scan
      3. OCI labels from the locally installed Docker image
      4. GHCR image-name inference
    """
    candidates = []

    for value in (
        (app_item or {}).get("project_url"),
    ):
        if value:
            candidates.append(str(value).strip())

    for item in (app_item or {}).get("items") or []:
        value = item.get("project_url")
        if value:
            candidates.append(str(value).strip())

    if image_ref:
        local_image_url = image_project_url(image_ref)
        if local_image_url:
            candidates.append(local_image_url)

        ghcr_url = github_url_from_image_ref(image_ref)
        if ghcr_url:
            candidates.append(ghcr_url)

    for candidate in candidates:
        if github_repo_from_url(candidate):
            return candidate

    return None


def _github_api_headers(token_override=None):
    headers = {
        "Accept": "application/vnd.github+json",
    }
    if token_override is None:
        token, _ = active_github_token()
    else:
        token = _normalize_github_token(token_override)
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _github_cache_key(owner, repo):
    return f"{str(owner).lower()}/{str(repo).lower()}"


def _github_cache_read(owner, repo, max_age):
    key = _github_cache_key(owner, repo)
    with GITHUB_VERSION_CACHE_LOCK:
        cache = load_json(GITHUB_VERSION_CACHE_FILE, {})
        entry = cache.get(key) if isinstance(cache, dict) else None
    if not isinstance(entry, dict):
        return None

    try:
        fetched_at = float(entry.get("fetched_at") or 0)
    except Exception:
        return None

    age = max(0.0, time.time() - fetched_at)
    if age > max_age:
        return None

    tags = entry.get("tags")
    if not isinstance(tags, list):
        return None

    tags = [str(tag or "").strip() for tag in tags if str(tag or "").strip()]
    return {
        "tags": list(dict.fromkeys(tags)),
        "source": str(entry.get("source") or "github_cache"),
        "age": age,
    }


def _github_cache_write(owner, repo, tags, source):
    key = _github_cache_key(owner, repo)
    value = {
        "fetched_at": time.time(),
        "source": str(source or "github"),
        "tags": list(dict.fromkeys(str(tag or "").strip() for tag in (tags or []) if str(tag or "").strip())),
    }
    with GITHUB_VERSION_CACHE_LOCK:
        cache = load_json(GITHUB_VERSION_CACHE_FILE, {})
        if not isinstance(cache, dict):
            cache = {}
        cache[key] = value
        save_json(GITHUB_VERSION_CACHE_FILE, cache)


def _github_rate_error(status, headers, payload):
    if status not in {403, 429}:
        return None

    remaining = None
    reset = None
    try:
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
    except Exception:
        pass

    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or "").strip()

    if str(remaining) == "0":
        suffix = f"; reset={reset}" if reset else ""
        return f"GitHub API rate limit reached{suffix}"

    if message:
        return f"GitHub API HTTP {status}: {message[:180]}"
    return f"GitHub API HTTP {status}"


def _github_list_tags_endpoint(owner, repo, endpoint, max_pages=3):
    """Read tag names from a paginated public GitHub REST endpoint."""
    tags = []
    headers = _github_api_headers()

    for page in range(1, max_pages + 1):
        url = (
            f"https://api.github.com/repos/"
            f"{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(repo, safe='')}/"
            f"{endpoint}?per_page=100&page={page}"
        )
        status, response_headers, payload = http_json(url, headers=headers, timeout=25)

        if status != 200:
            rate_error = _github_rate_error(status, response_headers, payload)
            if rate_error:
                return None, rate_error
            if status == 0 and isinstance(payload, dict):
                detail = str(payload.get("error") or "network error").strip()
                return None, f"GitHub {endpoint} network error: {detail[:180]}"
            return None, f"GitHub {endpoint} HTTP {status}"

        if not isinstance(payload, list):
            return None, f"GitHub {endpoint} returned an unexpected response"

        if endpoint == "releases":
            for entry in payload:
                if not isinstance(entry, dict) or entry.get("draft"):
                    continue
                tag = str(entry.get("tag_name") or "").strip()
                if tag:
                    tags.append(tag)
        else:
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                tag = str(entry.get("name") or "").strip()
                if tag:
                    tags.append(tag)

        if len(payload) < 100:
            break

    return list(dict.fromkeys(tags)), None


def _github_atom_tags_endpoint(owner, repo, endpoint):
    """
    GitHub Atom-feed fallback.

    This is deliberately independent of api.github.com and therefore remains
    useful when the unauthenticated REST quota is exhausted. It normally
    returns the most recent releases/tags, which is sufficient for the picker
    while a stale/full REST cache is unavailable.
    """
    feed_name = "releases.atom" if endpoint == "releases" else "tags.atom"
    url = (
        f"https://github.com/"
        f"{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/"
        f"{feed_name}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"Update-Monitor/{VERSION}",
            "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.1",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        return None, f"GitHub {feed_name} HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        return None, f"GitHub {feed_name} network error: {str(reason)[:180]}"

    tags = []
    try:
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            hrefs = []
            for link in entry.findall("atom:link", ns):
                href = str(link.attrib.get("href") or "").strip()
                if href:
                    hrefs.append(href)

            tag = None
            for href in hrefs:
                decoded = urllib.parse.unquote(href)
                if "/releases/tag/" in decoded:
                    tag = decoded.split("/releases/tag/", 1)[1].split("?", 1)[0].split("#", 1)[0]
                    break
                if endpoint == "tags" and "/tree/" in decoded:
                    tag = decoded.split("/tree/", 1)[1].split("?", 1)[0].split("#", 1)[0]
                    break

            if not tag and endpoint == "tags":
                title = entry.findtext("atom:title", default="", namespaces=ns)
                tag = str(title or "").strip()

            if tag:
                tags.append(tag.strip())
    except Exception as exc:
        return None, f"GitHub {feed_name} parse error: {str(exc)[:180]}"

    return list(dict.fromkeys(tag for tag in tags if tag)), None


def github_project_tags(project_url):
    """
    Return GitHub releases/tags with aggressive reuse and graceful fallbacks.

    Order:
      1. fresh persistent cache (6h)
      2. GitHub REST Releases
      3. GitHub REST Tags when the repo has no Releases
      4. Atom Releases/Tags if REST is rate-limited/unreachable
      5. stale persistent cache (up to 7 days)
    """
    repo_info = github_repo_from_url(project_url)
    if not repo_info:
        return None, None, "No GitHub repository URL detected"

    owner, repo = repo_info

    cached = _github_cache_read(owner, repo, GITHUB_VERSION_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached["tags"], cached["source"] + "_cache", None

    errors = []

    releases, release_error = _github_list_tags_endpoint(owner, repo, "releases")
    if releases:
        _github_cache_write(owner, repo, releases, "github_releases")
        return releases, "github_releases", None
    if release_error:
        errors.append(release_error)

    # If REST Releases worked and simply returned an empty list, ordinary REST
    # tags are worth one more request. If it failed (rate-limit/network), avoid
    # spending more REST quota and move directly to Atom.
    if release_error is None:
        tags, tag_error = _github_list_tags_endpoint(owner, repo, "tags")
        if tags:
            _github_cache_write(owner, repo, tags, "github_tags")
            return tags, "github_tags", None
        if tag_error:
            errors.append(tag_error)

    atom_releases, atom_release_error = _github_atom_tags_endpoint(owner, repo, "releases")
    if atom_releases:
        _github_cache_write(owner, repo, atom_releases, "github_releases_atom")
        return atom_releases, "github_releases_atom", None
    if atom_release_error:
        errors.append(atom_release_error)

    atom_tags, atom_tag_error = _github_atom_tags_endpoint(owner, repo, "tags")
    if atom_tags:
        _github_cache_write(owner, repo, atom_tags, "github_tags_atom")
        return atom_tags, "github_tags_atom", None
    if atom_tag_error:
        errors.append(atom_tag_error)

    stale = _github_cache_read(owner, repo, GITHUB_VERSION_CACHE_STALE_SECONDS)
    if stale is not None and stale["tags"]:
        return stale["tags"], stale["source"] + "_stale_cache", "; ".join(errors)[:500] or None

    return [], "github", "; ".join(errors)[:500] if errors else None




def github_verified_newer_registry_tags(
    current_tag,
    repo_key,
    registry_tags_value,
    github_tags_value,
    platform=None,
    limit=30,
    verify_limit=8,
):
    """Return newer GitHub releases only after exact Docker-tag verification.

    Registry tag listings are paginated and may omit the newest tags.  The
    interactive version picker already supplements them with GitHub releases,
    but the normal fixed-version scan historically did not.  This helper closes
    that gap without trusting GitHub alone: every reported candidate must resolve
    as an exact tag in the OCI registry.

    An optional leading ``v`` is the only spelling variant tried, matching the
    rest of the version-alias logic.  A candidate is accepted only when the
    registry returns at least one manifest digest for that exact spelling.
    """
    if _is_linuxserver_repo(repo_key):
        # Upstream GitHub releases are not LinuxServer image-build versions.
        # Never use them to manufacture Docker update candidates.
        return []

    visible = github_selectable_version_tags(
        github_tags_value,
        registry_tags_value,
        limit=max(verify_limit * 4, 40),
    )
    candidates = compatible_newer_tags_relaxed_prefix(
        current_tag,
        visible,
        limit=max(verify_limit, 1),
    )

    verified = []
    seen = set()
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate:
            continue

        spellings = [candidate]
        if candidate[:1].lower() == "v":
            alternate = candidate[1:]
        else:
            alternate = "v" + candidate
        if alternate and alternate not in spellings:
            spellings.append(alternate)

        accepted = None
        for exact_tag in spellings:
            key = exact_tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            digests, error = registry_manifest_digests(
                repo_key,
                exact_tag,
                platform,
                timeout=8,
            )
            if digests and not error:
                accepted = exact_tag
                break

        if accepted:
            verified.append(accepted)
            if len(verified) >= limit:
                break

    return verified


def _version_alias(tag):
    """
    Normalized comparison form for GitHub/Docker version tags.
    Only an optional leading v is ignored; the actual returned Docker/GitHub
    tag is never rewritten.
    """
    value = str(tag or "").strip()
    if value[:1].lower() == "v":
        value = value[1:]
    return value.lower()


def _sort_version_tags(tags, limit=100):
    """
    Sort semantic-version-looking tags newest first. Unknown GitHub release
    labels are kept after semantic versions instead of being silently removed.
    """
    unique = []
    seen = set()
    for raw in tags or []:
        tag = str(raw or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        unique.append(tag)

    parsed_items = []
    other_items = []
    for index, tag in enumerate(unique):
        parsed = parse_version(tag)
        if parsed:
            parsed_items.append((parsed["key"], tag))
        else:
            other_items.append((index, tag))

    parsed_items.sort(key=lambda pair: pair[0], reverse=True)
    ordered = [tag for _, tag in parsed_items]
    ordered.extend(tag for _, tag in other_items)
    return ordered[:limit]


FOLLOW_POLICY_TAG = "latest"
AUTO_UPDATE_RETRY_COOLDOWN_SECONDS = 15 * 60
AUTO_UPDATE_SUCCESS_REVERIFY_SECONDS = 5 * 60
FOLLOW_NORMALIZE_RETRY_COOLDOWN_SECONDS = 15 * 60

_DYNAMIC_REGISTRY_TAGS = {
    "latest", "stable", "main", "master", "develop", "development",
    "nightly", "edge", "testing", "test", "dev",
}
_ARCH_TAG_PREFIXES = (
    "amd64-", "x86_64-", "arm64-", "arm64v8-", "aarch64-",
    "arm32v7-", "arm32v6-", "armhf-", "i386-",
)


def _generic_version_numbers(tag):
    """
    Extract a useful numeric version/build key from non-standard Docker tags.

    Examples handled:
      1.2.3
      v1.2.3
      version-1.2.3
      1.2.3-ls224
      nightly-version-4.7.2.7675
      release-2026.09.1
    """
    value = str(tag or "").strip()
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,5})(?!\d)", value)
    if not match:
        return None

    nums = tuple(int(x) for x in match.group(1).split("."))
    nums = nums + (0,) * (6 - len(nums))
    nums = nums[:6]

    build = 0
    build_match = re.search(r"(?:^|[-._])(?:ls|r|rev|build)[-._]?(\d+)(?:$|[-._])", value, re.I)
    if build_match:
        build = int(build_match.group(1))

    return nums + (build,)


def _generic_registry_sort_key(tag):
    parsed = parse_version(tag)
    if parsed:
        return (2, parsed["key"], str(tag).lower())

    generic = _generic_version_numbers(tag)
    if generic:
        return (1, generic, str(tag).lower())

    return (0, (), str(tag).lower())


def _is_arch_specific_tag(tag):
    value = str(tag or "").strip().lower()
    return value.startswith(_ARCH_TAG_PREFIXES)


_LINUXSERVER_REPO_PREFIXES = (
    "docker.io/linuxserver/",
    "lscr.io/linuxserver/",
    "ghcr.io/linuxserver/",
)
_LINUXSERVER_CHANNEL_MARKERS = {
    "nightly": ("nightly",),
    "develop": ("develop", "development"),
    "testing": ("testing", "test", "edge"),
}


def _is_linuxserver_repo(repo_key):
    repo = str(repo_key or "").strip().lower()
    return repo.startswith(_LINUXSERVER_REPO_PREFIXES)


def _tag_has_channel_marker(tag, marker):
    value = str(tag or "").strip().lower()
    marker = str(marker or "").strip().lower()
    if not value or not marker:
        return False
    return bool(re.search(rf"(?:^|[-._]){re.escape(marker)}(?:$|[-._])", value))


def _linuxserver_tag_channel(tag):
    """Classify LinuxServer's stable/nightly/develop/testing alias families."""
    for channel, markers in _LINUXSERVER_CHANNEL_MARKERS.items():
        if any(_tag_has_channel_marker(tag, marker) for marker in markers):
            return channel
    return "stable"


def _filter_version_channel_tags(tags, repo_key=None, current_tag=None):
    """Keep selectable versions on the same release channel as the active tag.

    LinuxServer images publish several aliases for the same repository, e.g.
    ``version-3.1.0.4875`` for stable and
    ``nightly-version-3.1.3.5020`` for nightly.  Mixing these families makes a
    stable ``latest`` installation advertise nightly/develop builds as normal
    updates.  Other registries keep their existing behavior.
    """
    values = [str(value or "").strip() for value in (tags or []) if str(value or "").strip()]
    if not _is_linuxserver_repo(repo_key):
        return values

    wanted = _linuxserver_tag_channel(current_tag or "latest")
    return [tag for tag in values if _linuxserver_tag_channel(tag) == wanted]


def registry_selectable_version_tags(registry_tags_value, repo_key=None, limit=100, current_tag=None):
    """
    Build a source-neutral version list directly from the container registry.

    This deliberately supports non-SemVer naming used by LinuxServer and many
    other image publishers. The registry is the authoritative source because
    these are the exact tags Docker can install.

    LinuxServer repositories need special handling because one image digest is
    commonly published under several aliases at once, for example:
      latest, 3.1.0.4875-ls41, v2.18.1-ls243, 3.1.0, version-v2.18.1
    The human-facing Update Monitor version must be the concrete LinuxServer
    image build (``...-lsNN``), not the upstream application's GitHub release
    and not the non-build ``version-*`` alias.  This preserves image rebuilds
    such as ls40 -> ls41 even when the upstream application version is unchanged.
    """
    raw = []
    for value in registry_tags_value or []:
        tag = str(value or "").strip()
        if not tag:
            continue

        lower = tag.lower()
        if lower in _DYNAMIC_REGISTRY_TAGS:
            continue
        if _is_arch_specific_tag(tag):
            # Prefer multi-arch manifest tags to duplicate architecture tags.
            continue
        if not re.search(r"\d", tag):
            continue
        raw.append(tag)

    raw = list(dict.fromkeys(raw))
    raw = _filter_version_channel_tags(raw, repo_key, current_tag)

    if _is_linuxserver_repo(repo_key):
        channel = _linuxserver_tag_channel(current_tag or "latest")

        # Prefer the exact LinuxServer image-build family.  These tags carry the
        # lsNN build counter that changes when LinuxServer rebuilds an image even
        # without an upstream application release.
        if channel == "stable":
            build_pattern = re.compile(r"^v?\d+(?:\.\d+){1,5}-ls\d+$", re.I)
            alias_prefix = ""
        else:
            build_pattern = re.compile(
                rf"^{re.escape(channel)}-\d+(?:\.\d+){{1,5}}-ls\d+$",
                re.I,
            )
            alias_prefix = channel + "-"

        linuxserver_build_tags = [tag for tag in raw if build_pattern.fullmatch(tag)]
        if linuxserver_build_tags:
            raw = linuxserver_build_tags
        else:
            # Older/unusual LinuxServer repositories may expose only the
            # version-<upstream> alias. Keep that as a conservative fallback.
            alias_pattern = re.compile(rf"^{re.escape(alias_prefix)}version-v?\d", re.I)
            linuxserver_alias_tags = [tag for tag in raw if alias_pattern.search(tag)]
            if linuxserver_alias_tags:
                raw = linuxserver_alias_tags

    raw.sort(key=_generic_registry_sort_key, reverse=True)
    return raw[:limit]


def github_selectable_version_tags(github_tags_value, registry_tags_value=None, limit=100):
    """
    Build the visible version list from GitHub Releases/Tags.

    If the registry tag list is available, prefer its exact spelling
    (e.g. GitHub v1.2.3 -> Docker 1.2.3). If the registry does not expose a
    complete tag list, keep the GitHub release tag visible instead of hiding
    the release completely.
    """
    registry_tags_value = [str(x or "").strip() for x in (registry_tags_value or []) if str(x or "").strip()]
    by_alias = {}
    for tag in registry_tags_value:
        by_alias.setdefault(_version_alias(tag), tag)

    visible = []
    for raw in github_tags_value or []:
        github_tag = str(raw or "").strip()
        if not github_tag:
            continue

        # Prefer the actual Docker tag when an exact/v-prefix-equivalent tag
        # is known. Otherwise preserve the GitHub release tag.
        docker_tag = by_alias.get(_version_alias(github_tag))
        visible.append(docker_tag or github_tag)

    return _sort_version_tags(visible, limit=limit)


def merge_registry_and_github_selectable(
    registry_selectable,
    github_tags_value,
    registry_tags_value,
    limit=100,
    repo_key=None,
    current_tag=None,
):
    """
    Merge the registry's installable version list with the upstream GitHub
    release/tag list.

    LinuxServer is intentionally excluded from GitHub version merging.  Its
    upstream project releases (for example Lidarr's own GitHub tags) describe
    the application version, while LinuxServer's OCI tags describe the actual
    container build and add the significant ``-lsNN`` revision. Mixing the two
    families can make an old plain GitHub SemVer sort ahead of a newer
    LinuxServer build and can advertise a version that is unrelated to :latest.
    For LinuxServer, the registry build tags are therefore the sole authority.
    """
    merged = _filter_version_channel_tags(
        registry_selectable,
        repo_key,
        current_tag,
    )

    if _is_linuxserver_repo(repo_key):
        # registry_selectable is already channel-filtered and sorted by the
        # LinuxServer-aware numeric/build key. Preserve that exact order.
        return list(dict.fromkeys(merged))[:limit]

    if github_tags_value:
        github_visible = github_selectable_version_tags(
            github_tags_value,
            registry_tags_value,
            limit=max(limit, 200),
        )
        merged.extend(
            _filter_version_channel_tags(
                github_visible,
                repo_key,
                current_tag,
            )
        )

    return _sort_version_tags(merged, limit=limit)




def display_name(image_ref, containers):
    if containers:
        names = [c["name"] for c in containers]
        if len(names) == 1:
            return names[0]
    return image_ref


def icon_cache_key(url):
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()


def icon_proxy_url(url):
    if not url:
        return None
    source = str(url).strip()
    if not source:
        return None
    key = icon_cache_key(source)
    with ICON_SOURCE_MAP_GUARD:
        ICON_SOURCE_MAP[key] = source
    return f"/api/icons-v3/{key}"




def icon_source_for_key(cache_key):
    with ICON_SOURCE_MAP_GUARD:
        source = ICON_SOURCE_MAP.get(cache_key)
    if source:
        return source

    # Recovery path after a restart: successful cache entries already record
    # their original source in the metadata file.
    meta_path = ICON_CACHE_DIR / f"{cache_key}.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            source = str(meta.get("url") or "").strip()
            if source and icon_cache_key(source) == cache_key:
                with ICON_SOURCE_MAP_GUARD:
                    ICON_SOURCE_MAP[cache_key] = source
                return source
        except Exception:
            pass
    return None


def cached_icon_file(cache_key):
    data_path = ICON_CACHE_DIR / f"{cache_key}.bin"
    meta_path = ICON_CACHE_DIR / f"{cache_key}.json"
    if not data_path.exists():
        return None

    content_type = None
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            content_type = str(meta.get("content_type") or "").strip().lower()
        except Exception:
            content_type = None

    # Be tolerant of a cache file that survived without metadata.
    if not content_type:
        try:
            head = data_path.read_bytes()[:512]
            low = head.lstrip().lower()
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                content_type = "image/png"
            elif head.startswith(b"\xff\xd8\xff"):
                content_type = "image/jpeg"
            elif head.startswith((b"GIF87a", b"GIF89a")):
                content_type = "image/gif"
            elif len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                content_type = "image/webp"
            elif b"<svg" in low:
                content_type = "image/svg+xml"
        except Exception:
            pass

    return data_path, (content_type or "application/octet-stream")


def validate_public_icon_url(url):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("Unsupported icon URL")

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise RuntimeError("Local icon URLs are not cached")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError(f"Could not resolve icon host: {exc}") from exc

    ips = set()
    for entry in addresses:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        try:
            ips.add(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue

    if not ips or any(not ip.is_global for ip in ips):
        raise RuntimeError("Private or non-public icon hosts are not cached")


class SafeIconRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_icon_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


ICON_OPENER = urllib.request.build_opener(SafeIconRedirectHandler())


def icon_cache_lock(cache_key):
    with ICON_CACHE_LOCKS_GUARD:
        lock = ICON_CACHE_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            ICON_CACHE_LOCKS[cache_key] = lock
        return lock


def cached_icon(url, expected_key):
    if icon_cache_key(url) != expected_key:
        raise RuntimeError("Invalid icon cache key")

    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data_path = ICON_CACHE_DIR / f"{expected_key}.bin"
    meta_path = ICON_CACHE_DIR / f"{expected_key}.json"
    fail_path = ICON_CACHE_DIR / f"{expected_key}.fail"

    with icon_cache_lock(expected_key):
        if data_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                age = time.time() - data_path.stat().st_mtime
                if age <= ICON_CACHE_TTL_SECONDS:
                    return data_path, str(meta.get("content_type") or "application/octet-stream")
            except Exception:
                pass

        if fail_path.exists():
            try:
                if time.time() - fail_path.stat().st_mtime <= ICON_CACHE_FAILURE_TTL_SECONDS:
                    raise RuntimeError("Icon download is temporarily unavailable")
                fail_path.unlink(missing_ok=True)
            except RuntimeError:
                raise
            except Exception:
                pass

        validate_public_icon_url(url)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"Update-Monitor/{VERSION}",
                "Accept": "image/avif,image/webp,image/svg+xml,image/png,image/jpeg,image/gif,image/*;q=0.8,*/*;q=0.2",
            },
            method="GET",
        )

        try:
            with ICON_OPENER.open(req, timeout=10) as response:
                final_url = response.geturl()
                validate_public_icon_url(final_url)
                content_type = (response.headers.get_content_type() or "").lower()
                data = response.read(ICON_CACHE_MAX_BYTES + 1)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError) as exc:
            try:
                fail_path.write_text(str(exc)[:300], encoding="utf-8")
            except Exception:
                pass
            raise RuntimeError(f"Icon download failed: {exc}") from exc

        if len(data) > ICON_CACHE_MAX_BYTES:
            try:
                fail_path.write_text("Icon is too large", encoding="utf-8")
            except Exception:
                pass
            raise RuntimeError("Icon is too large")

        allowed_types = {
            "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
            "image/x-icon", "image/vnd.microsoft.icon", "image/avif",
        }
        if content_type not in allowed_types:
            head = data[:512].lstrip().lower()
            if b"<svg" in head or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower()):
                content_type = "image/svg+xml"
            else:
                try:
                    fail_path.write_text(
                        f"Unsupported icon content type: {content_type or 'unknown'}",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                raise RuntimeError(f"Unsupported icon content type: {content_type or 'unknown'}")

        tmp_data = data_path.with_suffix(".tmp")
        tmp_meta = meta_path.with_suffix(".tmp")
        tmp_data.write_bytes(data)
        tmp_meta.write_text(
            json.dumps({"url": url, "content_type": content_type}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_data.replace(data_path)
        tmp_meta.replace(meta_path)
        fail_path.unlink(missing_ok=True)
        return data_path, content_type


def _clean_compose_scalar(value):
    value = str(value or "").strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] in {"\"", "'"} and value[-1] == value[0]:
        value = value[1:-1]
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return value or None


def icon_from_compose_text(text):
    """Read the icon ZimaOS stores in the Compose x-casaos block."""
    lines = str(text or "").splitlines()
    in_x_casaos = False
    x_indent = -1

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        if re.fullmatch(r"x-casaos\s*:\s*", stripped, flags=re.I):
            in_x_casaos = True
            x_indent = indent
            continue

        if in_x_casaos:
            if indent <= x_indent:
                in_x_casaos = False
            else:
                match = re.match(r"icon\s*:\s*(.+?)\s*$", stripped, flags=re.I)
                if match:
                    value = _clean_compose_scalar(match.group(1))
                    if value:
                        return value

    return None




def casaos_icon_catalog():
    """Index ZimaOS' canonical Compose icon by all useful app/container aliases."""
    try:
        status, raw = casaos_request(
            "/v2/app_management/compose",
            method="GET",
            accept="application/json",
            timeout=20,
        )
        if status != 200:
            return {}
        payload = json.loads(raw or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return {}
    except Exception:
        return {}

    catalog = {}
    for app_id, entry in data.items():
        if not isinstance(entry, dict):
            continue

        store_info = entry.get("store_info") or {}
        compose = entry.get("compose") or {}
        x_casaos = compose.get("x-casaos") or compose.get("x_casaos") or {}

        # Prefer exactly the x-casaos.icon entry from the ZimaOS Compose.
        icon = str(x_casaos.get("icon") or store_info.get("icon") or "").strip()
        if not icon:
            continue

        aliases = {
            str(app_id or "").strip(),
            str(compose.get("name") or "").strip(),
            str(x_casaos.get("store_app_id") or "").strip(),
            str(x_casaos.get("id") or "").strip(),
            str(store_info.get("store_app_id") or "").strip(),
        }

        services = compose.get("services") or {}
        if isinstance(services, dict):
            for service_name, service in services.items():
                aliases.add(str(service_name or "").strip())
                if isinstance(service, dict):
                    aliases.add(str(service.get("container_name") or "").strip())

        for alias in aliases:
            if alias:
                catalog[alias] = icon

    return catalog


def warm_icon_cache(apps):
    urls = []
    seen = set()
    for app_item in apps or []:
        url = str(app_item.get("icon_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        return

    def fetch_one(url):
        try:
            cached_icon(url, icon_cache_key(url))
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=ICON_CACHE_WORKERS) as pool:
        list(pool.map(fetch_one, urls))






def _title_value(value):
    """Return the preferred human-facing title from a CasaOS title object."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if not isinstance(value, dict):
        return None

    # ZimaOS' custom title is the user's/app-store chosen display name.
    for key in ("custom", "de_DE", "de_de", "en_US", "en_us", "en"):
        text = str(value.get(key) or "").strip()
        if text:
            return text

    for value_item in value.values():
        text = str(value_item or "").strip()
        if text:
            return text
    return None


def casaos_title_catalog():
    """Index ZimaOS display titles by app, Compose, service and container aliases."""
    try:
        status, raw = casaos_request(
            "/v2/app_management/compose",
            method="GET",
            accept="application/json",
            timeout=20,
        )
        if status != 200:
            return {}
        payload = json.loads(raw or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return {}
    except Exception:
        return {}

    catalog = {}
    for app_id, entry in data.items():
        if not isinstance(entry, dict):
            continue

        store_info = entry.get("store_info") or {}
        compose = entry.get("compose") or {}
        x_casaos = compose.get("x-casaos") or compose.get("x_casaos") or {}

        # Prefer the exact custom display title ZimaOS shows to the user.
        title = (
            _title_value(x_casaos.get("title"))
            or _title_value(store_info.get("title"))
        )
        if not title:
            continue

        aliases = {
            str(app_id or "").strip(),
            str(compose.get("name") or "").strip(),
            str(x_casaos.get("store_app_id") or "").strip(),
            str(x_casaos.get("id") or "").strip(),
            str(store_info.get("store_app_id") or "").strip(),
        }

        services = compose.get("services") or {}
        if isinstance(services, dict):
            for service_name, service in services.items():
                aliases.add(str(service_name or "").strip())
                if isinstance(service, dict):
                    aliases.add(str(service.get("container_name") or "").strip())

        for alias in aliases:
            if alias:
                catalog[alias.casefold()] = title

    return catalog


DISPLAY_WORDS = {
    "ai": "AI",
    "api": "API",
    "cpu": "CPU",
    "dns": "DNS",
    "gpu": "GPU",
    "hdd": "HDD",
    "http": "HTTP",
    "https": "HTTPS",
    "ip": "IP",
    "mcp": "MCP",
    "nas": "NAS",
    "nvme": "NVMe",
    "pdf": "PDF",
    "raid": "RAID",
    "rss": "RSS",
    "smart": "SMART",
    "ssh": "SSH",
    "ssd": "SSD",
    "ui": "UI",
    "usb": "USB",
    "vpn": "VPN",
    "zimaos": "ZimaOS",
}


def clean_display_name(value):
    """Disciplined fallback when ZimaOS has no explicit display title."""
    text = str(value or "").strip()
    if not text:
        return text

    # Big Bear is an app-store/source prefix, not part of the app's name.
    text = re.sub(r"^(?:big[-_ ]?bear)[-_ ]+", "", text, flags=re.I)

    # Technical separators are not shown in human-facing titles.
    parts = [part for part in re.split(r"[-_.\s]+", text) if part]
    if not parts:
        return text

    pretty = []
    for part in parts:
        low = part.casefold()
        if low in DISPLAY_WORDS:
            pretty.append(DISPLAY_WORDS[low])
        elif part.isupper() and len(part) <= 5:
            pretty.append(part)
        elif any(ch.isupper() for ch in part[1:]):
            # Preserve established product casing such as LibreTranslate.
            pretty.append(part)
        else:
            pretty.append(part[:1].upper() + part[1:].lower())

    return " ".join(pretty)


GENERIC_STACK_NAMES = {
    "app", "backend", "database", "default", "docker", "frontend",
    "noble_wen", "dreamy_linda",
}


def _path_stack_name(value):
    if not value:
        return None
    value = str(value).split(",", 1)[0].strip()
    if not value or value in {".", "/"}:
        return None
    path = Path(value)
    # config_files normally points to docker-compose.yml; working_dir points to the directory.
    if path.suffix.lower() in {".yml", ".yaml"}:
        path = path.parent
    name = path.name.strip()
    return name or None


def _looks_generated_stack_name(name):
    if not name:
        return True
    low = name.lower().strip()
    if low in GENERIC_STACK_NAMES:
        return True
    if re.fullmatch(r"compose-[0-9a-f]{8,}", low):
        return True
    return False


def container_stack_metadata(container):
    config = container.get("Config", {}) or {}
    labels = config.get("Labels", {}) or {}
    name = container.get("Name", "").lstrip("/")
    project = (labels.get("com.docker.compose.project") or "").strip()
    service = (labels.get("com.docker.compose.service") or "").strip()
    container_number = (
        labels.get("com.docker.compose.container-number") or ""
    ).strip()
    working_dir = (labels.get("com.docker.compose.project.working_dir") or "").strip()
    config_files = (labels.get("com.docker.compose.project.config_files") or "").strip()
    icon_url = (
        labels.get("icon")
        or labels.get("casaos.icon")
        or labels.get("net.unraid.docker.icon")
        or ""
    ).strip()

    project_link = project_link_from_labels(labels)
    project_url = (project_link or {}).get("url") or ""
    project_provider = (project_link or {}).get("provider")
    project_url_source = (project_link or {}).get("source")

    if config_files:
        stack_key = "config:" + config_files
    elif working_dir:
        stack_key = "workdir:" + working_dir
    elif project:
        stack_key = "project:" + project
    else:
        stack_key = "container:" + name

    path_name = _path_stack_name(working_dir) or _path_stack_name(config_files)
    if path_name and not _looks_generated_stack_name(path_name):
        stack_name = path_name
    elif project and not _looks_generated_stack_name(project):
        stack_name = project
    else:
        stack_name = name

    return {
        "stack_key": stack_key,
        "stack_name": stack_name,
        "compose_project": project or None,
        "compose_service": service or None,
        "compose_container_number": container_number or None,
        "compose_working_dir": working_dir or None,
        "compose_config_files": config_files or None,
        "icon_url": icon_url or None,
        "project_url": project_url or None,
        "project_provider": project_provider,
        "project_url_source": project_url_source,
    }




def container_published_ports(container):
    """
    Return the host-side published ports for one Docker container.

    HostConfig.PortBindings is preferred because it is also available for
    stopped containers. NetworkSettings.Ports is used as a fallback/merge.
    IPv4/IPv6 duplicates of the same host port are collapsed.
    TCP is shown as the plain port number; non-TCP protocols keep a suffix,
    e.g. 19132/UDP.
    """
    values = []
    seen = set()

    def add_binding(container_port, binding):
        binding = binding or {}
        host_port = str(binding.get("HostPort") or "").strip()
        if not host_port:
            return

        raw_container_port = str(container_port or "").strip()
        protocol = "tcp"
        if "/" in raw_container_port:
            protocol = raw_container_port.rsplit("/", 1)[-1].strip().lower() or "tcp"

        label = host_port if protocol == "tcp" else f"{host_port}/{protocol.upper()}"
        key = label.casefold()
        if key not in seen:
            seen.add(key)
            values.append(label)

    host_bindings = ((container.get("HostConfig") or {}).get("PortBindings") or {})
    if isinstance(host_bindings, dict):
        for container_port, bindings in host_bindings.items():
            for binding in bindings or []:
                if isinstance(binding, dict):
                    add_binding(container_port, binding)

    network_bindings = ((container.get("NetworkSettings") or {}).get("Ports") or {})
    if isinstance(network_bindings, dict):
        for container_port, bindings in network_bindings.items():
            for binding in bindings or []:
                if isinstance(binding, dict):
                    add_binding(container_port, binding)

    def sort_key(value):
        raw = str(value or "")
        number = raw.split("/", 1)[0]
        try:
            return (0, int(number), raw)
        except Exception:
            return (1, 0, raw.lower())

    return sorted(values, key=sort_key)

def select_current_compose_containers(containers):
    """
    Remove obsolete Compose container generations from a Docker inspect list.

    Docker Compose gives every replica a stable slot:
      project + service + container-number

    When ZimaOS recreates an app, an older container generation can coexist
    briefly with the new one. The newest Created timestamp is the active
    generation for that slot. Non-Compose containers are left untouched.
    """
    slots = {}
    passthrough = []

    for container in containers or []:
        config = container.get("Config", {}) or {}
        labels = config.get("Labels", {}) or {}

        project = str(labels.get("com.docker.compose.project") or "").strip()
        service = str(labels.get("com.docker.compose.service") or "").strip()
        number = str(
            labels.get("com.docker.compose.container-number") or ""
        ).strip()

        if not (project and service and number):
            passthrough.append(container)
            continue

        key = (project, service, number)
        created = str(container.get("Created") or "")

        previous = slots.get(key)
        if previous is None:
            slots[key] = container
            continue

        previous_created = str(previous.get("Created") or "")
        if created >= previous_created:
            slots[key] = container

    return passthrough + list(slots.values())


def select_current_runtime_rows(rows):
    """
    Runtime equivalent of select_current_compose_containers().
    Keeps only the newest live row for each Compose replica slot.
    """
    slots = {}
    passthrough = []

    for row in rows or []:
        project = str(row.get("compose_project") or "").strip()
        service = str(row.get("compose_service") or "").strip()
        number = str(row.get("compose_container_number") or "").strip()

        if not (project and service and number):
            passthrough.append(row)
            continue

        key = (project, service, number)
        created = str(row.get("created_at") or "")

        previous = slots.get(key)
        if previous is None:
            slots[key] = row
            continue

        previous_created = str(previous.get("created_at") or "")
        if created >= previous_created:
            slots[key] = row

    return passthrough + list(slots.values())


def _common_container_name(containers):
    names = [str(c.get("name") or "") for c in containers if c.get("name")]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    prefix = os.path.commonprefix(names).rstrip("-_. ")
    if len(prefix) >= 3:
        return prefix
    return names[0]



def _normalize_auto_time(value):
    value = str(value or "03:00").strip()
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    return value if match else "03:00"


def _normalize_auto_days(value):
    if not isinstance(value, list):
        return [0, 1, 2, 3, 4, 5, 6]
    days = []
    for item in value:
        try:
            day = int(item)
        except Exception:
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    # An explicit empty list means no scheduled weekdays. Non-list/missing values
    # still default to all weekdays for backward compatibility.
    return sorted(days)


def _normalize_auto_timezone(value):
    value = str(value or "UTC").strip() or "UTC"
    try:
        ZoneInfo(value)
        return value
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def _normalize_backup_mode(value, default="none"):
    mode = str(value or default).strip().lower()
    if mode not in {"none", "quick", "full"}:
        return str(default or "none") if str(default or "none") in {"none", "quick", "full"} else "none"
    return mode


def get_monitor_policy(stack_key):
    with policies_lock:
        raw = dict(policies.get(str(stack_key)) or {})

    mode = str(raw.get("mode") or "fixed")
    if mode not in {"fixed", "upgrade", "follow", "notify"}:
        mode = "fixed"

    target_tag = str(raw.get("target_tag") or "").strip() or None
    auto_enabled = bool(raw.get("auto_enabled", False))
    if mode not in {"upgrade", "follow"}:
        auto_enabled = False
    auto_immediate = bool(raw.get("auto_immediate", False)) and auto_enabled

    return {
        "mode": mode,
        "target_tag": target_tag,
        "auto_enabled": auto_enabled,
        "auto_immediate": auto_immediate,
        "auto_time": _normalize_auto_time(raw.get("auto_time")),
        "auto_days": _normalize_auto_days(raw.get("auto_days")),
        "auto_timezone": _normalize_auto_timezone(raw.get("auto_timezone")),
        "auto_backup_mode": _normalize_backup_mode(raw.get("auto_backup_mode"), "none"),
        "last_auto_attempt_at": raw.get("last_auto_attempt_at"),
        "last_auto_local_date": raw.get("last_auto_local_date"),
        "last_auto_signature": raw.get("last_auto_signature"),
        "last_auto_result": raw.get("last_auto_result"),
        "last_auto_error": raw.get("last_auto_error"),
        "last_follow_normalize_attempt_at": raw.get("last_follow_normalize_attempt_at"),
        "last_follow_normalize_signature": raw.get("last_follow_normalize_signature"),
        "last_follow_normalize_result": raw.get("last_follow_normalize_result"),
        "last_follow_normalize_error": raw.get("last_follow_normalize_error"),
        "updated_at": raw.get("updated_at"),
    }


def save_monitor_policy(
    stack_key,
    mode,
    target_tag=None,
    auto_enabled=False,
    auto_immediate=False,
    auto_time="03:00",
    auto_days=None,
    auto_timezone="UTC",
    auto_backup_mode="none",
):
    mode = str(mode or "fixed")
    if mode not in {"fixed", "upgrade", "follow", "notify"}:
        mode = "fixed"

    auto_enabled = bool(auto_enabled) and mode in {"upgrade", "follow"}
    auto_immediate = bool(auto_immediate) and auto_enabled
    auto_backup_mode = _normalize_backup_mode(auto_backup_mode, "none")

    with policies_lock:
        previous = dict(policies.get(str(stack_key)) or {})

        # A meaningful automatic-update policy change should allow a newly
        # selected immediate mode to act on the currently detected update.
        previous_signature = previous.get("last_auto_signature")
        policy_changed = (
            str(previous.get("mode") or "fixed") != mode
            or str(previous.get("target_tag") or "") != str(target_tag or "")
            or bool(previous.get("auto_enabled", False)) != auto_enabled
            or bool(previous.get("auto_immediate", False)) != auto_immediate
            or _normalize_backup_mode(previous.get("auto_backup_mode"), "none") != auto_backup_mode
        )
        if policy_changed:
            previous_signature = None

        # Follow-tag normalization is independent from the automatic-update
        # toggle/schedule. Keep its retry state while remaining in follow mode,
        # but reset it when entering/leaving follow so an old failure cannot
        # poison a newly selected policy.
        follow_normalize_reset = (
            str(previous.get("mode") or "fixed") != mode
            or mode != "follow"
        )

        value = {
            "mode": mode,
            "target_tag": target_tag or None,
            "auto_enabled": auto_enabled,
            "auto_immediate": auto_immediate,
            "auto_time": _normalize_auto_time(auto_time),
            "auto_days": _normalize_auto_days(auto_days),
            "auto_timezone": _normalize_auto_timezone(auto_timezone),
            "auto_backup_mode": auto_backup_mode,
            "last_auto_attempt_at": previous.get("last_auto_attempt_at"),
            "last_auto_local_date": previous.get("last_auto_local_date"),
            "last_auto_signature": previous_signature,
            "last_auto_result": previous.get("last_auto_result"),
            "last_auto_error": previous.get("last_auto_error"),
            "last_follow_normalize_attempt_at": (
                None if follow_normalize_reset
                else previous.get("last_follow_normalize_attempt_at")
            ),
            "last_follow_normalize_signature": (
                None if follow_normalize_reset
                else previous.get("last_follow_normalize_signature")
            ),
            "last_follow_normalize_result": (
                None if follow_normalize_reset
                else previous.get("last_follow_normalize_result")
            ),
            "last_follow_normalize_error": (
                None if follow_normalize_reset
                else previous.get("last_follow_normalize_error")
            ),
            "updated_at": utc_now(),
        }
        policies[str(stack_key)] = value
        save_json(POLICY_FILE, policies)
    return dict(value)


def record_auto_update_result(
    stack_key,
    local_date,
    success,
    error=None,
    update_signature=None,
):
    with policies_lock:
        current = dict(policies.get(str(stack_key)) or {})
        current["last_auto_attempt_at"] = utc_now()
        current["last_auto_local_date"] = str(local_date)
        if update_signature:
            current["last_auto_signature"] = str(update_signature)
        current["last_auto_result"] = "success" if success else "error"
        current["last_auto_error"] = None if success else str(error or "Unknown error")
        policies[str(stack_key)] = current
        save_json(POLICY_FILE, policies)


def record_follow_normalization_result(
    stack_key,
    signature,
    success,
    error=None,
):
    with policies_lock:
        current = dict(policies.get(str(stack_key)) or {})
        current["last_follow_normalize_attempt_at"] = utc_now()
        current["last_follow_normalize_signature"] = str(signature or "") or None
        current["last_follow_normalize_result"] = "success" if success else "error"
        current["last_follow_normalize_error"] = (
            None if success else str(error or "Unknown error")
        )
        policies[str(stack_key)] = current
        save_json(POLICY_FILE, policies)


_INFRASTRUCTURE_IMAGE_HINTS = {
    "postgres", "postgresql", "postgis", "redis", "valkey", "mariadb", "mysql",
    "mongodb", "mongo", "elasticsearch", "opensearch", "rabbitmq", "memcached",
    "clickhouse", "typesense", "meilisearch", "solr", "influxdb", "prometheus",
    "grafana", "minio",
}


def _version_item_current_tag(item):
    image_ref = str((item or {}).get("image_ref") or "").strip()
    parsed = parse_image_ref(image_ref) if image_ref else {}
    return str(parsed.get("tag") or (item or {}).get("tag") or "").strip()


def _version_item_repo_key(item):
    image_ref = str((item or {}).get("image_ref") or "").strip()
    if not image_ref:
        return ""
    parsed = parse_image_ref(image_ref)
    return str(parsed.get("normalized_repo") or "").strip().lower()


def _version_item_values(item, field):
    values = list((item or {}).get(field) or [])
    if field == "newer_tags" and not values and (item or {}).get("newer_tag"):
        values = [(item or {}).get("newer_tag")]
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def _version_group_identity(item):
    """
    Images are grouped only when there is strong evidence that they share one
    upstream release version.

    A common project URL + the same currently configured version is strong
    evidence (e.g. Immich server + machine-learning). Without a project URL,
    each registry repository stays its own group so unrelated sidecars are
    never merged merely because they happen to use the same tag.
    """
    current_tag = _version_item_current_tag(item)
    current_alias = _version_alias(current_tag) or current_tag.lower()
    project_url = normalize_project_url((item or {}).get("project_url"))

    if project_url and current_alias:
        return (
            "project:"
            + project_url.casefold()
            + "|tag:"
            + current_alias
        )

    repo_key = _version_item_repo_key(item)
    return "repo:" + repo_key if repo_key else ""


def _group_common_tag_data(items, field):
    """
    Build the intersection of a tag family across all images in one version
    group. Optional leading 'v' differences are treated as aliases, while the
    exact installable tag for each image is preserved.

    Returns:
      display_tags: tags shown in the UI, ordered by the first image
      per_alias: alias -> {image_ref: exact_registry_tag}
    """
    items = list(items or [])
    if not items:
        return {"display_tags": [], "per_alias": {}}

    maps = []
    ordered_aliases = []

    for index, item in enumerate(items):
        values = _version_item_values(item, field)
        if not values:
            return {"display_tags": [], "per_alias": {}}

        alias_map = {}
        for value in values:
            alias = _version_alias(value)
            if not alias or alias in alias_map:
                continue
            alias_map[alias] = value
            if index == 0:
                ordered_aliases.append(alias)
        maps.append(alias_map)

    common_aliases = set(maps[0])
    for alias_map in maps[1:]:
        common_aliases.intersection_update(alias_map)

    display_tags = []
    per_alias = {}
    for alias in ordered_aliases:
        if alias not in common_aliases:
            continue
        display_tags.append(maps[0][alias])
        per_alias[alias] = {
            str(item.get("image_ref") or ""): alias_map[alias]
            for item, alias_map in zip(items, maps)
        }

    return {
        "display_tags": display_tags,
        "per_alias": per_alias,
    }


def build_version_groups(app_item):
    groups = {}
    for item in (app_item or {}).get("items") or []:
        if item.get("update_policy") in {"digest_pinned", "local"}:
            continue
        if not str(item.get("image_ref") or "").strip():
            continue

        group_id = _version_group_identity(item)
        if not group_id:
            continue

        group = groups.setdefault(group_id, {
            "id": group_id,
            "items": [],
        })
        group["items"].append(item)

    result = []
    for group in groups.values():
        items = group["items"]
        group["available"] = _group_common_tag_data(items, "available_version_tags")
        group["newer"] = _group_common_tag_data(items, "newer_tags")
        group["current_tags"] = [
            _version_item_current_tag(item)
            for item in items
        ]
        group["container_count"] = sum(
            len(item.get("containers") or [])
            for item in items
        )
        result.append(group)

    return result


def _version_group_name_tokens(app_item):
    values = [
        str((app_item or {}).get("name") or ""),
        str((app_item or {}).get("compose_project") or ""),
    ]
    tokens = set()
    for value in values:
        for token in re.findall(r"[a-z0-9]+", value.lower()):
            if len(token) >= 4 and token not in {
                "docker", "compose", "server", "service", "stack", "app",
            }:
                tokens.add(token)
    return tokens


def _version_group_score(group, app_item):
    items = list((group or {}).get("items") or [])
    score = 0

    # Shared upstream version across multiple different images is the strongest
    # signal and is exactly the pattern used by apps such as Immich.
    if len(items) > 1:
        score += 220 + min(40, 10 * len(items))

    score += min(25, 5 * int((group or {}).get("container_count") or 0))

    tokens = _version_group_name_tokens(app_item)
    haystack_parts = []
    infrastructure_hits = 0

    for item in items:
        repo = _version_item_repo_key(item)
        project_url = str(item.get("project_url") or "").lower()
        services = [
            str(container.get("compose_service") or "").lower()
            for container in item.get("containers") or []
        ]

        haystack_parts.extend([repo, project_url, *services])

        repo_leaf = repo.rsplit("/", 1)[-1].lower() if repo else ""
        service_set = {service for service in services if service}
        if (
            repo_leaf in _INFRASTRUCTURE_IMAGE_HINTS
            or service_set.intersection(_INFRASTRUCTURE_IMAGE_HINTS)
        ):
            infrastructure_hits += 1

    haystack = " ".join(haystack_parts)
    matched_tokens = sum(1 for token in tokens if token in haystack)
    score += min(100, matched_tokens * 50)

    if any(item.get("project_url") for item in items):
        score += 8

    if infrastructure_hits and infrastructure_hits == len(items):
        score -= 120

    return score


def _version_group_published_port_count(group):
    """Count host-published ports attached to one version family."""
    count = 0
    for item in (group or {}).get("items") or []:
        for container in item.get("containers") or []:
            count += len(container.get("ports") or [])
    return count


def _version_group_is_infrastructure(group):
    """Return True when every member looks like DB/cache/message infrastructure."""
    items = list((group or {}).get("items") or [])
    if not items:
        return False

    for item in items:
        repo = _version_item_repo_key(item)
        repo_leaf = repo.rsplit("/", 1)[-1].lower() if repo else ""
        services = {
            str(container.get("compose_service") or "").strip().lower()
            for container in item.get("containers") or []
            if str(container.get("compose_service") or "").strip()
        }
        if not (
            repo_leaf in _INFRASTRUCTURE_IMAGE_HINTS
            or services.intersection(_INFRASTRUCTURE_IMAGE_HINTS)
        ):
            return False
    return True


def select_primary_version_group(app_item):
    """
    Choose exactly one app-level version family.

    In multi-container Compose apps, a host-published port is strong evidence for
    the user-facing/main application container.  Prefer that family before the
    generic scoring logic, but never let obvious database/cache infrastructure
    win merely because it also exposes a host port.

    Safety still wins over convenience: if multiple independent image families
    remain ambiguous, no manual app version is exposed instead of mixing
    unrelated Redis/PostgreSQL/etc. versions into the main application's selector.
    """
    groups = build_version_groups(app_item)
    if not groups:
        return None
    if len(groups) == 1:
        return groups[0]

    # v0.3.303: for apps such as MediaCMS/Silo, the image family owning the
    # published host port is normally the actual web application.  Sidecars
    # (workers, migrations, Redis, PostgreSQL, etc.) must not decide the visible
    # app version.
    port_groups = [
        group for group in groups
        if _version_group_published_port_count(group) > 0
        and not _version_group_is_infrastructure(group)
    ]
    if len(port_groups) == 1:
        return port_groups[0]
    if len(port_groups) > 1:
        ordered_ports = sorted(
            port_groups,
            key=lambda group: (
                _version_group_published_port_count(group),
                _version_group_score(group, app_item),
                int((group.get("container_count") or 0)),
            ),
            reverse=True,
        )
        first = ordered_ports[0]
        second = ordered_ports[1]
        first_ports = _version_group_published_port_count(first)
        second_ports = _version_group_published_port_count(second)
        first_score = _version_group_score(first, app_item)
        second_score = _version_group_score(second, app_item)
        if first_ports > second_ports or (first_score - second_score) >= 20:
            return first

    multi_image = [group for group in groups if len(group.get("items") or []) > 1]
    if len(multi_image) == 1:
        return multi_image[0]
    if len(multi_image) > 1:
        ordered = sorted(
            multi_image,
            key=lambda group: (
                len(group.get("items") or []),
                _version_group_score(group, app_item),
            ),
            reverse=True,
        )
        if len(ordered[0].get("items") or []) > len(ordered[1].get("items") or []):
            return ordered[0]

    with_versions = [
        group
        for group in groups
        if (group.get("available") or {}).get("display_tags")
    ]
    if len(with_versions) == 1:
        return with_versions[0]

    scored = sorted(
        (
            (_version_group_score(group, app_item), group)
            for group in groups
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return None

    best_score, best_group = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -999

    # Require a meaningful and clearly better match before choosing a lone
    # main-image group from a multi-image stack.
    if best_score >= 40 and (best_score - second_score) >= 20:
        return best_group

    return None


def _version_item_installed_tag(item):
    """Best known human-readable version for the image actually installed locally."""
    resolved = str((item or {}).get("resolved_installed_tag") or "").strip()
    if resolved:
        return resolved
    return _version_item_current_tag(item)


def _version_group_installed_tag(group):
    """
    Return the concrete installed release for the selected app version family.

    A primary app can contain several images. Not every sidecar necessarily has
    a separately resolvable release tag, even though the main image already
    proves the installed app release by digest. Therefore one or more
    digest-resolved values are sufficient as long as every resolved value agrees
    on the same version alias. Conflicting resolved versions are never guessed.
    """
    items = list((group or {}).get("items") or [])
    if not items:
        return None

    # Strongest evidence: exact digest -> concrete registry tag resolution.
    resolved_values = [
        str(item.get("resolved_installed_tag") or "").strip()
        for item in items
        if str(item.get("resolved_installed_tag") or "").strip()
    ]
    if resolved_values:
        resolved_aliases = {
            _version_alias(value)
            for value in resolved_values
            if _version_alias(value)
        }
        if len(resolved_aliases) == 1:
            return resolved_values[0]
        # Two actually resolved images disagree: do not invent an app version.
        return None

    # Fallback for apps already configured with a concrete shared version.
    values = [_version_item_installed_tag(item) for item in items]
    values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if len(values) != len(items):
        return None

    aliases = {_version_alias(value) for value in values if _version_alias(value)}
    if len(aliases) != 1:
        return None

    return values[0]


def _public_version_group(group):
    if not group:
        return None

    services = []
    image_refs = []
    for item in group.get("items") or []:
        image_ref = str(item.get("image_ref") or "").strip()
        if image_ref and image_ref not in image_refs:
            image_refs.append(image_ref)
        for container in item.get("containers") or []:
            service = str(container.get("compose_service") or "").strip()
            if service and service not in services:
                services.append(service)

    return {
        "id": group.get("id"),
        "image_refs": image_refs,
        "services": services,
        "current_tags": list(group.get("current_tags") or []),
        "installed_version": _version_group_installed_tag(group),
        "available_tags": list((group.get("available") or {}).get("display_tags") or []),
        "newer_tags": list((group.get("newer") or {}).get("display_tags") or []),
        "image_count": len(image_refs),
    }


def policy_available_tags(app_item):
    """Selectable versions for the one safely identified app version family."""
    group = select_primary_version_group(app_item)
    if not group:
        return []
    return list((group.get("available") or {}).get("display_tags") or [])


def _version_group_target_map(group, target_tag, field):
    alias = _version_alias(target_tag)
    if not alias:
        return {}
    data = (group or {}).get(field) or {}
    return dict((data.get("per_alias") or {}).get(alias) or {})


def apply_monitor_policy_fields(app_item):
    policy = get_monitor_policy(app_item.get("stack_key"))
    group = select_primary_version_group(app_item)
    available_tags = (
        list((group.get("available") or {}).get("display_tags") or [])
        if group else []
    )
    common_newer_tags = (
        list((group.get("newer") or {}).get("display_tags") or [])
        if group else []
    )

    mode = policy.get("mode") or "fixed"
    target_tag = str(policy.get("target_tag") or "").strip() or None
    can_version_update = False

    current_aliases = {
        _version_alias(value)
        for value in ((group or {}).get("current_tags") or [])
        if _version_alias(value)
    }

    follow_tag_switch_only = False
    follow_target_state = None

    if app_item.get("compose_project") and group:
        if mode == "follow":
            # A different tag name does not automatically mean a different image.
            # If all mismatched members already have the exact :latest digest,
            # suppress "Update installieren" and normalize only the Compose tag.
            group_items = list((group or {}).get("items") or [])
            mismatched = [
                item
                for item in group_items
                if str(_version_item_current_tag(item) or "").strip().lower()
                != FOLLOW_POLICY_TAG
            ]

            if mismatched:
                target_states = [
                    str(item.get("follow_target_state") or "unknown")
                    for item in mismatched
                ]
                states = [item.get("follow_target_same_image") for item in mismatched]
                if any(value == "missing" for value in target_states):
                    # At least one image in this version family has no :latest
                    # tag.  A family-wide follow transition is therefore not
                    # installable and must never be auto-attempted.
                    follow_target_state = "unavailable"
                    can_version_update = False
                elif states and all(value is True for value in states):
                    follow_tag_switch_only = True
                    follow_target_state = "same-image"
                    can_version_update = False
                else:
                    follow_target_state = (
                        "different-image"
                        if any(value is False for value in states)
                        else "unknown"
                    )
                    can_version_update = True
            else:
                follow_target_state = "already-following"
                can_version_update = False

        elif mode == "upgrade" and target_tag in available_tags:
            # Manual selection may intentionally move backwards, but changing
            # to the version already configured for the whole family is a no-op.
            can_version_update = _version_alias(target_tag) not in current_aliases

    app_item["monitor_policy"] = policy
    app_item["policy_available_tags"] = available_tags
    app_item["policy_follow_tag"] = FOLLOW_POLICY_TAG
    app_item["follow_tag_switch_only"] = follow_tag_switch_only
    app_item["follow_target_state"] = follow_target_state
    app_item["can_version_update"] = can_version_update
    app_item["installed_version"] = _version_group_installed_tag(group) if group else None
    app_item["version_group"] = _public_version_group(group)
    app_item["version_group_state"] = (
        "selected"
        if group
        else ("ambiguous" if len(build_version_groups(app_item)) > 1 else "none")
    )
    return app_item


def refresh_scan_policy_fields():
    changed = False
    with scan_lock:
        for app_item in scan_state.get("apps") or []:
            before = (
                dict(app_item.get("monitor_policy") or {}),
                list(app_item.get("policy_available_tags") or []),
                bool(app_item.get("can_version_update")),
            )
            apply_monitor_policy_fields(app_item)
            after = (
                dict(app_item.get("monitor_policy") or {}),
                list(app_item.get("policy_available_tags") or []),
                bool(app_item.get("can_version_update")),
            )
            if before != after:
                changed = True

        if changed:
            save_json(SCAN_FILE, scan_state)



_LOCAL_VERSION_LABEL_KEYS = (
    "org.opencontainers.image.version",
    "org.label-schema.version",
    "org.opencontainers.image.ref.name",
    "version",
    "VERSION",
)


def _clean_local_version_value(value):
    value = str(value or "").strip()
    if not value or len(value) > 160:
        return None
    if value.lower() in _DYNAMIC_REGISTRY_TAGS:
        return None
    # Avoid treating arbitrary prose/URLs as versions. Real-world Docker release
    # labels may contain dates, hashes, suffixes (ls224, omnibus, etc.), but they
    # practically always contain at least one digit and no whitespace.
    if not re.search(r"\d", value):
        return None
    if re.search(r"\s", value):
        return None
    if "://" in value:
        return None
    return value


def _local_repo_tag_versions(item):
    """
    Return concrete tags already attached to the exact local Docker image ID.
    Since RepoTags belong to the same image ID, they are stronger/faster evidence
    than querying hundreds of remote manifests.
    """
    image_ref = str((item or {}).get("image_ref") or "").strip()
    parsed = parse_image_ref(image_ref) if image_ref else {}
    wanted_repo = str(parsed.get("normalized_repo") or "").strip()

    candidates = []
    for ref in (item or {}).get("local_repo_tags") or []:
        ref = str(ref or "").strip()
        if not ref or "@" in ref:
            continue
        p = parse_image_ref(ref)
        repo = str(p.get("normalized_repo") or "").strip()
        tag = _clean_local_version_value(p.get("tag"))
        if wanted_repo and repo != wanted_repo:
            continue
        if tag:
            candidates.append(tag)

    if not candidates:
        return []

    # Reuse the same ordering logic as the registry selector.
    return _sort_version_tags(list(dict.fromkeys(candidates)), limit=100)


def _local_version_label_hint(item):
    """
    Return a local OCI/image-label version only as a *hint*.

    Base images often leave inherited labels behind. A real example is
    ollama/ollama, whose Ubuntu base contributes:
        org.opencontainers.image.version=24.04
    That describes Ubuntu, not Ollama. Therefore an OCI label must never become
    the displayed application version until a remote manifest digest confirms it.
    """
    labels = (item or {}).get("local_image_labels") or {}
    if not isinstance(labels, dict):
        return None, None

    for key in _LOCAL_VERSION_LABEL_KEYS:
        candidate = _clean_local_version_value(labels.get(key))
        if candidate:
            return candidate, "local-image-label-hint:" + key

    return None, None


def _local_installed_version(item):
    """
    Best zero-network *authoritative* installed-version evidence.

    Only a concrete RepoTag attached to the exact same local Docker image ID is
    accepted here. OCI version labels are deliberately excluded because they can
    be inherited from the base image.
    """
    repo_tags = _local_repo_tag_versions(item)
    if repo_tags:
        return repo_tags[0], "local-repotag"

    return None, None


def _installed_version_resolution_record(item, status, source=None, detail=None):
    record = {
        "status": str(status or "unknown"),
        "source": str(source or "") or None,
        "detail": str(detail or "")[:300] or None,
        "checked_at": utc_now(),
    }
    item["installed_version_resolution"] = record
    return record


def _display_installed_version_hint(item, selectable):
    """Return a human-readable display hint without changing update safety.

    Exact digest->tag matching remains authoritative.  Some publishers rebuild
    their rolling :latest image, so its digest legitimately differs from every
    numbered release tag even though the application release itself is unchanged.
    In that case we may still show a version label, but keep it explicitly
    separate from resolved_installed_tag so it is never used as proof for an
    install/update decision.
    """
    candidates = [
        str(value or "").strip()
        for value in (selectable or [])
        if str(value or "").strip()
    ]
    if not candidates:
        return None, None

    by_alias = {}
    for candidate in candidates:
        alias = _version_alias(candidate)
        if alias and alias not in by_alias:
            by_alias[alias] = candidate

    # Strong display evidence: an OCI/image version label agrees with a concrete
    # version that actually exists in this image repository's release catalogue.
    label_hint = str((item or {}).get("local_version_label_hint") or "").strip()
    if label_hint:
        matched = by_alias.get(_version_alias(label_hint))
        if matched:
            return matched, "local-label+catalog"

    # v0.3.302: never infer the installed concrete version of a rolling tag
    # merely from the first/newest catalogue entry. A :latest image can be built
    # independently from numbered release tags and cached catalogues can be stale.
    # Showing candidates[0] in that situation produced false versions such as
    # MediaCMS 7.2.0 while the running :latest image was actually 8.4.0.
    #
    # A human-readable version is shown only when it is backed by stronger
    # evidence above (local version label + existing catalogue entry) or by the
    # authoritative digest resolver in enrich_scan_installed_versions().
    return None, None


def enrich_scan_installed_versions(results, platform):
    """
    Resolve the concrete release behind generic configured tags during the normal
    scan, without requiring the user to open the version picker first.

    Resolution order:
      1. local OCI version label / local concrete RepoTag
      2. digest-bound persistent cache
      3. registry tags + exact manifest-digest comparison
      4. GitHub Releases/Tags as supplemental candidates, followed by the same
         exact registry manifest verification
      5. display-only version hint for a verified current :latest image when the
         publisher rebuilds :latest independently from numbered release tags

    Every unresolved case stores a diagnostic reason on the scan item.
    """
    items = [
        item for item in (results or [])
        if isinstance(item, dict)
    ]

    pending = {}

    for item in items:
        current_tag = str(item.get("tag") or "").strip()

        # A concrete configured tag is already the installed version.
        if not _needs_installed_version_resolution(current_tag):
            if current_tag and not item.get("resolved_installed_tag"):
                item["resolved_installed_tag"] = current_tag
            _installed_version_resolution_record(
                item,
                "resolved",
                "configured-tag",
                current_tag,
            )
            continue

        # Reuse a prior scan result only when it already survived the local
        # digest continuity check performed while rebuilding this item.
        existing = str(item.get("resolved_installed_tag") or "").strip()
        if existing:
            _installed_version_resolution_record(
                item,
                "resolved",
                "previous-scan",
                existing,
            )
            continue

        # Strongest and cheapest zero-network evidence: a concrete RepoTag
        # attached to the exact same local image ID.
        local_version, local_source = _local_installed_version(item)
        if local_version:
            item["resolved_installed_tag"] = local_version
            local_digests = list(item.get("local_digests") or [])
            image_ref = str(item.get("image_ref") or "").strip()
            parsed = parse_image_ref(image_ref) if image_ref else {}
            repo_key = str(parsed.get("normalized_repo") or "").strip()
            if repo_key and local_digests:
                installed_version_cache_put(repo_key, local_digests, local_version)
            _installed_version_resolution_record(
                item,
                "resolved",
                local_source,
                local_version,
            )
            continue

        # OCI/image version labels are only diagnostic hints. Never publish them
        # directly as the application version.
        label_hint, label_hint_source = _local_version_label_hint(item)
        if label_hint:
            item["local_version_label_hint"] = label_hint
            item["local_version_label_hint_source"] = label_hint_source

        local_digests = tuple(sorted(
            str(value or "").strip().lower()
            for value in (item.get("local_digests") or [])
            if str(value or "").strip()
        ))

        if not local_digests:
            _installed_version_resolution_record(
                item,
                "unresolved",
                "local-image",
                "No matching local RepoDigest and no usable local version metadata",
            )
            continue

        image_ref = str(item.get("image_ref") or "").strip()
        parsed = parse_image_ref(image_ref) if image_ref else {}
        repo_key = str(parsed.get("normalized_repo") or "").strip()
        if not repo_key:
            _installed_version_resolution_record(
                item,
                "unresolved",
                "registry",
                "Could not determine registry repository",
            )
            continue

        cached = installed_version_cache_get(repo_key, local_digests, None)
        if cached:
            item["resolved_installed_tag"] = cached
            _installed_version_resolution_record(
                item,
                "resolved",
                "digest-cache",
                cached,
            )
            continue

        task_key = (repo_key, local_digests)
        pending.setdefault(task_key, []).append(item)

    if not pending:
        return 0

    def resolve_task(task_key, task_items):
        repo_key, local_digests = task_key
        diagnostics = []

        current_tag = ""
        for task_item in task_items:
            current_tag = str(task_item.get("tag") or "").strip()
            if not current_tag:
                image_ref = str(task_item.get("image_ref") or "").strip()
                if image_ref:
                    current_tag = str(parse_image_ref(image_ref).get("tag") or "").strip()
            if current_tag:
                break

        # Reuse any already-known version catalogue first, but sanitize it
        # through the active release channel so stale nightly/develop entries
        # cannot survive from an older scan/picker result.
        registry_selectable = []
        seen = set()
        for task_item in task_items:
            for value in task_item.get("available_version_tags") or []:
                value = str(value or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    registry_selectable.append(value)

        registry_selectable = _filter_version_channel_tags(
            registry_selectable,
            repo_key,
            current_tag,
        )

        registry_values = []
        registry_error = None
        registry_fetched_fresh = False

        if not registry_selectable:
            try:
                # A deeper crawl than the ordinary UI call. Some registries have
                # thousands of tags and do not promise newest-first pagination.
                registry_values, registry_error = registry_tags(
                    repo_key,
                    max_pages=10,
                )
            except Exception as exc:
                registry_values, registry_error = [], str(exc)

            registry_values = registry_values or []
            if registry_error:
                diagnostics.append("registry: " + str(registry_error)[:180])
            else:
                registry_fetched_fresh = True
                registry_selectable = registry_selectable_version_tags(
                    registry_values,
                    repo_key=repo_key,
                    limit=600,
                    current_tag=current_tag,
                )

        if registry_selectable:
            # A local OCI label is merely a candidate-order hint. It must pass
            # the same exact registry digest check as every other version tag.
            hinted = []
            for task_item in task_items:
                hint = str(task_item.get("local_version_label_hint") or "").strip()
                if hint and hint in registry_selectable and hint not in hinted:
                    hinted.append(hint)
            ordered_selectable = hinted + [
                tag for tag in registry_selectable if tag not in set(hinted)
            ]

            resolved = resolve_installed_version_tag(
                repo_key,
                local_digests,
                ordered_selectable,
                platform,
                batch_size=4,
                deadline_seconds=28.0,
            )
            if resolved:
                return task_key, resolved, registry_selectable, "registry-digest", diagnostics, None
            diagnostics.append(
                f"registry: no digest match in {len(registry_selectable)} concrete tags"
            )
        else:
            diagnostics.append("registry: no usable concrete version tags")

        # The version picker already supplements the registry with GitHub.
        # Do the same here only after the registry-only pass failed, so normal
        # scans do not consume GitHub requests unnecessarily.
        project_url = None
        for task_item in task_items:
            candidate = str(task_item.get("project_url") or "").strip()
            if candidate and github_repo_from_url(candidate):
                project_url = candidate
                break

        if not project_url:
            for task_item in task_items:
                image_ref = str(task_item.get("image_ref") or "").strip()
                candidate = github_url_from_image_ref(image_ref)
                if candidate and github_repo_from_url(candidate):
                    project_url = candidate
                    break

        github_values = []
        github_source = None
        github_error = None
        if project_url and not _is_linuxserver_repo(repo_key):
            try:
                github_values, github_source, github_error = github_project_tags(project_url)
            except Exception as exc:
                github_error = str(exc)
            github_values = github_values or []

        if github_error:
            diagnostics.append("github: " + str(github_error)[:180])

        if github_values:
            merged = merge_registry_and_github_selectable(
                registry_selectable,
                github_values,
                registry_values,
                limit=600,
                repo_key=repo_key,
                current_tag=current_tag,
            )
            if merged:
                resolved = resolve_installed_version_tag(
                    repo_key,
                    local_digests,
                    merged,
                    platform,
                    batch_size=4,
                    deadline_seconds=28.0,
                )
                if resolved:
                    source = "registry+" + str(github_source or "github")
                    return task_key, resolved, merged, source, diagnostics, None
                diagnostics.append(
                    f"github supplement: no digest match in {len(merged)} candidates"
                )
        elif project_url:
            diagnostics.append("github: no release/tag candidates")

        # v0.3.303 display-only fallback for rolling tags.  If Docker has already
        # proven that every local image behind this repository equals the current
        # remote rolling tag, the newest upstream release can safely be used as a
        # human-readable *hint*.  It never becomes resolved_installed_tag and is
        # therefore never used to authorize an update.  This is especially useful
        # when publishers rebuild :latest separately from numbered image tags.
        rolling_current = bool(task_items) and all(
            str(task_item.get("status") or "").upper() == "CURRENT"
            and _needs_installed_version_resolution(task_item.get("tag"))
            for task_item in task_items
        )
        display_hint = None
        hint_source = None

        if rolling_current and github_values and "stale_cache" not in str(github_source or ""):
            visible_github = github_selectable_version_tags(
                github_values,
                registry_values,
                limit=50,
            )
            visible_github = _filter_version_channel_tags(
                visible_github,
                repo_key,
                current_tag,
            )
            if visible_github:
                display_hint = visible_github[0]
                hint_source = "current-rolling+" + str(github_source or "github")

        # If no GitHub release mapping exists, a freshly fetched registry
        # catalogue is the next-best display hint.  Never use a carried-over
        # catalogue from an older scan here; that was the source of stale values.
        if (
            rolling_current
            and not display_hint
            and registry_fetched_fresh
            and registry_selectable
        ):
            display_hint = registry_selectable[0]
            hint_source = "current-rolling+fresh-registry"

        if display_hint:
            diagnostics.append(
                f"display hint: {display_hint} from {hint_source}; exact numbered-tag digest match unavailable"
            )

        return task_key, None, registry_selectable, None, diagnostics, (display_hint, hint_source)

    resolved_count = 0

    # v0.3.110 could create up to 4 outer tasks * 8 manifest requests each.
    # Limit the full scan to two repositories at a time; each repository uses
    # four manifest workers => at most eight manifest requests concurrently.
    worker_count = max(1, min(2, len(pending)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(resolve_task, task_key, task_items): task_key
            for task_key, task_items in pending.items()
        }

        for future in concurrent.futures.as_completed(futures):
            task_key = futures[future]
            task_items = pending.get(task_key) or []

            try:
                _, resolved, selectable, source, diagnostics, task_display_hint = future.result()
            except Exception as exc:
                resolved = None
                selectable = []
                source = None
                diagnostics = ["resolver exception: " + str(exc)[:180]]
                task_display_hint = None

            if selectable:
                visible_selectable = list(selectable[:100])
                for item in task_items:
                    if not item.get("available_version_tags"):
                        item["available_version_tags"] = visible_selectable

            if resolved:
                for item in task_items:
                    item["resolved_installed_tag"] = resolved
                    _installed_version_resolution_record(
                        item,
                        "resolved",
                        source or "digest",
                        resolved,
                    )
                    resolved_count += 1
            else:
                detail = "; ".join(diagnostics)[:300] or "No verified version match found"
                for item in task_items:
                    display_hint, display_source = (task_display_hint or (None, None))
                    if not display_hint:
                        display_hint, display_source = _display_installed_version_hint(
                            item,
                            selectable,
                        )
                    if display_hint:
                        item["display_installed_version"] = display_hint
                        _installed_version_resolution_record(
                            item,
                            "hint",
                            display_source,
                            (
                                "Exact digest-to-numbered-tag match unavailable; "
                                f"displaying {display_hint} as the current :latest release hint"
                            ),
                        )
                    else:
                        _installed_version_resolution_record(
                            item,
                            "unresolved",
                            "digest-resolution",
                            detail,
                        )

    return resolved_count


def enrich_scan_available_versions(results, platform):
    """Resolve a concrete release tag for a changed rolling image.

    IMAGE_UPDATE proves only that a rolling tag such as :latest now points to a
    different remote digest.  Publish a human-readable available version only
    when a concrete registry tag is proven to point to that exact same remote
    digest.  Otherwise the UI keeps the conservative "new image" fallback.
    """
    resolved_count = 0
    tags_cache = {}

    for item in (results or []):
        if not isinstance(item, dict) or item.get("status") != "IMAGE_UPDATE":
            continue

        image_ref = str(item.get("image_ref") or "").strip()
        if not image_ref:
            continue
        parsed = parse_image_ref(image_ref)
        current_tag = str(parsed.get("tag") or item.get("tag") or "").strip()
        if not _needs_installed_version_resolution(current_tag):
            continue

        repo_key = str(parsed.get("normalized_repo") or "").strip()
        if not repo_key:
            continue

        remote_candidates = {
            str(value or "").strip().lower()
            for value in (item.get("remote_digest"), item.get("remote_platform_digest"))
            if str(value or "").strip()
        }
        if not remote_candidates:
            continue

        selectable = _filter_version_channel_tags(
            [
                str(value or "").strip()
                for value in (item.get("available_version_tags") or [])
                if str(value or "").strip()
            ],
            repo_key,
            current_tag,
        )

        # A stale resolved_available_tag from an older catalogue must never win
        # over a newly corrected release-channel catalogue.
        stale_resolved = str(item.get("resolved_available_tag") or "").strip()
        if stale_resolved and stale_resolved not in selectable:
            item["resolved_available_tag"] = None
            item.pop("available_version_resolution", None)

        if not selectable:
            if repo_key not in tags_cache:
                try:
                    values, error = registry_tags(repo_key, max_pages=5)
                except Exception as exc:
                    values, error = [], str(exc)
                tags_cache[repo_key] = (
                    registry_selectable_version_tags(
                        values or [],
                        repo_key=repo_key,
                        limit=120,
                        current_tag=current_tag,
                    )
                    if not error else []
                )
            selectable = list(tags_cache.get(repo_key) or [])
            if selectable and not item.get("available_version_tags"):
                item["available_version_tags"] = list(selectable[:100])

        if not selectable:
            continue

        resolved = resolve_installed_version_tag(
            repo_key,
            remote_candidates,
            selectable,
            platform,
            batch_size=4,
            deadline_seconds=16.0,
            cache_result=False,
        )
        if not resolved:
            item["available_version_resolution"] = {
                "status": "unresolved",
                "source": "remote-digest",
                "detail": f"No concrete version tag matched the new remote digest in {len(selectable)} candidates",
                "checked_at": utc_now(),
            }
            continue

        item["resolved_available_tag"] = resolved
        item["available_version_resolution"] = {
            "status": "resolved",
            "source": "remote-digest",
            "detail": resolved,
            "checked_at": utc_now(),
        }
        resolved_count += 1

    return resolved_count


def enrich_follow_policy_targets(results, platform):
    """
    Compare a saved follow policy's target tag (:latest) with the image that is
    already installed locally.

    same:
      The current numbered tag and `latest` point to the exact same image digest.
      No image update is required; only the Compose image reference must change.
    different:
      `latest` is a genuinely different image and remains a normal update.
    unknown:
      The comparison could not be proven, so stay conservative and keep the
      normal update path.
    """
    items = [item for item in (results or []) if isinstance(item, dict)]
    pending = []

    for item in items:
        stack_key = str(item.get("stack_key") or "").strip()
        policy = get_monitor_policy(stack_key)

        if str(policy.get("mode") or "") != "follow":
            item.pop("follow_target_same_image", None)
            item.pop("follow_target_state", None)
            item.pop("follow_target_error", None)
            item.pop("follow_target_digests", None)
            continue

        current_tag = str(item.get("tag") or "").strip().lower()
        item["follow_target_tag"] = FOLLOW_POLICY_TAG

        if current_tag == FOLLOW_POLICY_TAG:
            item["follow_target_same_image"] = True
            item["follow_target_state"] = "already-following"
            item["follow_target_error"] = None
            item["follow_target_digests"] = list(item.get("local_digests") or [])
            continue

        if item.get("update_policy") in {"digest_pinned", "local"}:
            item["follow_target_same_image"] = None
            item["follow_target_state"] = "unsupported"
            item["follow_target_error"] = "No comparable registry-backed local digest"
            item["follow_target_digests"] = []
            continue

        local = {
            str(value or "").strip().lower()
            for value in (item.get("local_digests") or [])
            if str(value or "").strip()
        }

        image_ref = str(item.get("image_ref") or "").strip()
        parsed = parse_image_ref(image_ref) if image_ref else {}
        repo_key = str(parsed.get("normalized_repo") or "").strip()

        if not local or not repo_key:
            item["follow_target_same_image"] = None
            item["follow_target_state"] = "unknown"
            item["follow_target_error"] = "Missing local digest or registry repository"
            item["follow_target_digests"] = []
            continue

        pending.append((item, repo_key, local))

    if not pending:
        return

    def check_one(item, repo_key, local):
        try:
            remote, error = registry_manifest_digests(
                repo_key,
                FOLLOW_POLICY_TAG,
                platform,
                timeout=10,
            )
        except Exception as exc:
            remote, error = set(), str(exc)

        remote = {
            str(value or "").strip().lower()
            for value in (remote or [])
            if str(value or "").strip()
        }

        if error or not remote:
            detail = str(error or "No remote latest digest")[:240]
            state = "missing" if "HTTP 404" in detail else "unknown"
            return item, None, state, detail, []

        digest_groups = list(item.get("local_digest_groups") or [])
        same = (
            _all_local_digest_groups_match_remote(digest_groups, remote)
            if digest_groups
            else bool(local) and local.issubset(remote)
        )
        return item, same, ("same" if same else "different"), None, sorted(remote)

    worker_count = max(1, min(4, len(pending)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(check_one, item, repo_key, local)
            for item, repo_key, local in pending
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                item, same, state, error, digests = future.result()
            except Exception:
                continue
            item["follow_target_same_image"] = same
            item["follow_target_state"] = state
            item["follow_target_error"] = error
            item["follow_target_digests"] = list(digests or [])




# ---------------------------------------------------------------------------
# Automatic Docker image source discovery + compatibility checking
# ---------------------------------------------------------------------------
#
# Every Compose-backed Docker app can open "Image Quelle".  Discovery is done
# only when the user opens that view; a full update scan does not spend GitHub
# or registry requests on source switching.
#
# Discovery order:
#   1. current image source
#   2. verified/curated alternatives (currently Tautulli)
#   3. same-publisher GHCR mirrors, especially LinuxServer -> GHCR
#   4. GHCR package derived from the app's GitHub project
#   5. conservative exact-name GitHub repository search if no project metadata
#
# A discovered repository is shown only when its GHCR manifest exists for the
# active Docker platform.  Unknown runtime contracts are never called "green":
# they remain yellow until compatibility is explicitly known.  Compose dry-run,
# architecture/manifest checks and obvious current-runtime conflicts can still
# produce red and block switching.

IMAGE_SOURCE_CATALOG = {
    "tautulli": {
        "match_repos": {
            "lscr.io/linuxserver/tautulli",
            "docker.io/linuxserver/tautulli",
            "ghcr.io/linuxserver/tautulli",
            "ghcr.io/tautulli/tautulli",
        },
        "sources": [
            {
                "id": "curated:linuxserver-tautulli",
                "label_de": "LinuxServer Image",
                "label_en": "LinuxServer image",
                "image_ref": "lscr.io/linuxserver/tautulli:latest",
                "match_repos": {
                    "lscr.io/linuxserver/tautulli",
                    "docker.io/linuxserver/tautulli",
                    "ghcr.io/linuxserver/tautulli",
                },
                "required_mounts": {"/config"},
                "required_ports": {"8181/tcp"},
                "required_env": {"PUID", "PGID", "TZ"},
                "supported_env": {"PUID", "PGID", "TZ"},
                "health_port": "8181",
                "project_url": "https://github.com/linuxserver/docker-tautulli",
                "compatibility_mode": "curated",
            },
            {
                "id": "curated:official-tautulli",
                "label_de": "Offizielles Tautulli Image",
                "label_en": "Official Tautulli image",
                "image_ref": "ghcr.io/tautulli/tautulli:latest",
                "match_repos": {"ghcr.io/tautulli/tautulli"},
                "required_mounts": {"/config"},
                "required_ports": {"8181/tcp"},
                "required_env": {"TZ"},
                "supported_env": {"PUID", "PGID", "TZ"},
                "health_port": "8181",
                "project_url": "https://github.com/Tautulli/Tautulli",
                "compatibility_mode": "curated",
            },
        ],
    },
}

IMAGE_SOURCE_DISCOVERY_CACHE = {}
IMAGE_SOURCE_DISCOVERY_CACHE_LOCK = threading.Lock()
IMAGE_SOURCE_DISCOVERY_TTL_SECONDS = 30 * 60


def _image_source_repo(image_ref):
    try:
        return str(
            parse_image_ref(str(image_ref or "").strip()).get("normalized_repo") or ""
        ).strip()
    except Exception:
        return ""




def _image_source_state_key(compose_project, compose_service):
    project = str(compose_project or "").strip()
    service = str(compose_service or "").strip()
    if not project or not service:
        return None
    return project + "\n" + service


def _ensure_image_source_state_loaded():
    global IMAGE_SOURCE_STATE, IMAGE_SOURCE_STATE_LOADED
    with IMAGE_SOURCE_STATE_LOCK:
        if IMAGE_SOURCE_STATE_LOADED:
            return
        loaded = load_json(IMAGE_SOURCE_STATE_FILE, {})
        IMAGE_SOURCE_STATE = loaded if isinstance(loaded, dict) else {}
        IMAGE_SOURCE_STATE_LOADED = True


def saved_image_source_ref(compose_project, compose_service):
    key = _image_source_state_key(compose_project, compose_service)
    if not key:
        return None
    _ensure_image_source_state_loaded()
    with IMAGE_SOURCE_STATE_LOCK:
        row = IMAGE_SOURCE_STATE.get(key)
        if not isinstance(row, dict):
            return None
        value = str(row.get("image_ref") or "").strip()
        return value or None


def remember_image_source_assignment(
    compose_project,
    compose_service,
    image_ref,
    *,
    stack_key=None,
    verified_digests=None,
    source="compose",
):
    global IMAGE_SOURCE_STATE

    key = _image_source_state_key(compose_project, compose_service)
    image_ref = str(image_ref or "").strip()
    if not key or not image_ref:
        return False

    _ensure_image_source_state_loaded()

    normalized_digests = sorted({
        str(value or "").strip().lower()
        for value in (verified_digests or [])
        if str(value or "").strip()
    })

    with IMAGE_SOURCE_STATE_LOCK:
        previous = IMAGE_SOURCE_STATE.get(key)
        if (
            isinstance(previous, dict)
            and str(previous.get("image_ref") or "").strip() == image_ref
            and sorted(previous.get("verified_digests") or [])
                == normalized_digests
        ):
            return False

        IMAGE_SOURCE_STATE[key] = {
            "compose_project": str(compose_project or "").strip(),
            "compose_service": str(compose_service or "").strip(),
            "stack_key": str(stack_key or "").strip() or None,
            "image_ref": image_ref,
            "verified_digests": normalized_digests,
            "source": str(source or "compose"),
            "updated_at": utc_now(),
        }
        save_json(IMAGE_SOURCE_STATE_FILE, IMAGE_SOURCE_STATE)

    return True


def casaos_compose_service_image_catalog():
    """Read ZimaOS structured Compose data: project -> service -> image."""
    try:
        status, raw = casaos_request(
            "/v2/app_management/compose",
            method="GET",
            accept="application/json",
            timeout=20,
        )
        if status != 200:
            return {}
        payload = json.loads(raw or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return {}
    except Exception:
        return {}

    catalog = {}

    for app_id, entry in data.items():
        if not isinstance(entry, dict):
            continue

        compose = entry.get("compose") or {}
        if not isinstance(compose, dict):
            continue

        services = compose.get("services") or {}
        if not isinstance(services, dict):
            services = {}

        mapping = {}
        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            image_ref = str(service.get("image") or "").strip()
            if image_ref:
                mapping[str(service_name)] = image_ref

        aliases = {
            str(app_id or "").strip(),
            str(compose.get("name") or "").strip(),
        }
        x_casaos = compose.get("x-casaos") or compose.get("x_casaos") or {}
        if isinstance(x_casaos, dict):
            aliases.add(str(x_casaos.get("id") or "").strip())
            aliases.add(str(x_casaos.get("store_app_id") or "").strip())

        for alias in aliases:
            if alias:
                catalog[alias] = dict(mapping)

    return catalog


def compose_service_image_map_from_yaml(yaml_text):
    """Return service -> configured image, including quoted service names."""
    lines = str(yaml_text or "").splitlines()
    result = {}

    services_line = None
    services_indent = None

    for index, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue

        # Match top-level services: with optional quoting only around the key.
        match = re.match(
            r'^(?P<indent>[ \t]*)(?:["\']?services["\']?)\s*:\s*(?:#.*)?$',
            raw.rstrip("\r\n"),
            flags=re.I,
        )
        if match:
            services_line = index
            services_indent = len(match.group("indent").expandtabs(4))
            break

    if services_line is None:
        return result

    index = services_line + 1
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()

        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        leading = len(raw) - len(raw.lstrip(" \t"))
        indent_width = len(raw[:leading].expandtabs(4))
        if indent_width <= services_indent:
            break

        service_match = re.match(
            r'^(?P<indent>[ \t]*)(?P<quote>["\']?)'
            r'(?P<name>[A-Za-z0-9_.-]+)(?P=quote)\s*:\s*(?:#.*)?$',
            raw.rstrip("\r\n"),
        )
        if not service_match:
            index += 1
            continue

        service_name = service_match.group("name")
        service_indent = len(service_match.group("indent").expandtabs(4))
        if service_indent <= services_indent:
            index += 1
            continue

        service_end = len(lines)
        probe = index + 1

        while probe < len(lines):
            child = lines[probe]
            child_stripped = child.strip()

            if not child_stripped or child_stripped.startswith("#"):
                probe += 1
                continue

            child_leading = len(child) - len(child.lstrip(" \t"))
            child_indent = len(child[:child_leading].expandtabs(4))
            if child_indent <= service_indent:
                service_end = probe
                break

            probe += 1

        for child_index in range(index + 1, service_end):
            child = lines[child_index]
            child_stripped = child.strip()
            if not child_stripped or child_stripped.startswith("#"):
                continue

            child_leading = len(child) - len(child.lstrip(" \t"))
            child_indent = len(child[:child_leading].expandtabs(4))
            if child_indent <= service_indent:
                continue

            image_match = re.match(
                r'^[ \t]*(?:["\']?image["\']?)[ \t]*:[ \t]*(.+?)\s*$',
                child.rstrip("\r\n"),
                flags=re.I,
            )
            if not image_match:
                continue

            # Strip a trailing YAML comment first, then remove matching
            # quotes. _clean_compose_scalar() historically did those operations
            # in the opposite order, which leaves quotes behind for:
            #   image: 'repo/image:tag' # comment
            raw_value = str(image_match.group(1) or "").strip()
            raw_value = re.split(r"\s+#", raw_value, maxsplit=1)[0].strip()
            if (
                len(raw_value) >= 2
                and raw_value[0] in {"\"", "'"}
                and raw_value[-1] == raw_value[0]
            ):
                raw_value = raw_value[1:-1].strip()

            if raw_value:
                result[service_name] = raw_value
            break

        index = service_end

    return result



def compose_literal_image_ref(
    compose_project,
    compose_service,
    compose_map_cache=None,
):
    """Return the literal image: value from the ZimaOS Compose YAML.

    This deliberately does NOT use structured ZimaOS data, persistent source
    state, Docker Config.Image, RepoDigests or image IDs. Those values may
    contain a digest that Docker/ZimaOS resolved internally and therefore
    cannot prove that the user actually pinned the Compose source by digest.
    """
    project = str(compose_project or "").strip()
    service = str(compose_service or "").strip()

    if not project or not service:
        return None

    cache = compose_map_cache if isinstance(compose_map_cache, dict) else {}

    if project not in cache:
        try:
            cache[project] = compose_service_image_map_from_yaml(
                casaos_compose_yaml(project)
            )
        except Exception:
            cache[project] = {}

    mapping = cache.get(project) or {}
    value = str(mapping.get(service) or "").strip()

    if not value:
        return None

    # Environment/template expressions are not a concrete literal image source.
    # In that case we cannot safely prove an explicit digest pin from YAML.
    if re.search(r"\$\{|\$[A-Za-z_]|\{\{|\}\}", value):
        return None

    return value


def compose_literal_digest_pin(image_ref):
    """Return the explicitly configured digest, or None when not proven."""
    value = str(image_ref or "").strip()
    if not value:
        return None
    return parse_image_ref(value).get("pinned_digest")


def zimaos_managed_latest_digest_ref(image_ref, runtime_refs=None, local_repo_tags=None):
    """Return the mutable :latest tracking ref for a ZimaOS-managed digest lock.

    ZimaOS/CasaOS may persist `repo:latest@sha256:<manifest>` while Docker runs
    the service from `repo:latest`. That representation is used to track the
    exact resolved latest image and must not be confused with a user-selected
    immutable digest policy.

    We only normalize this very specific case:
      * the Compose reference explicitly contains the `latest` tag AND a digest;
      * the same tag-only reference is observed from Docker Config.Image or
        local RepoTags.

    Digest-only refs (`repo@sha256:...`), numbered tags with digests, and
    `latest@sha256` refs without runtime/tag evidence remain true pins.
    """
    value = str(image_ref or "").strip()
    if not value or "@" not in value:
        return None

    parsed = parse_image_ref(value)
    if (
        str(parsed.get("tag") or "").strip().lower() != "latest"
        or not parsed.get("pinned_digest")
    ):
        return None

    tag_ref = str(parsed.get("base") or "").strip()
    if not tag_ref:
        return None

    normalized_target = tag_ref.lower()

    evidence = set()
    for source in (runtime_refs or [], local_repo_tags or []):
        for candidate in source if isinstance(source, (list, tuple, set)) else []:
            candidate = str(candidate or "").strip()
            if candidate:
                evidence.add(candidate.lower())

    if normalized_target in evidence:
        return tag_ref

    return None


def _restore_pin_state_key(compose_project, compose_service):
    project = str(compose_project or "").strip()
    service = str(compose_service or "").strip()
    if not project or not service:
        return None
    return project + "\n" + service


def _restore_pin_refs_match(current_ref, pinned_ref):
    current = parse_image_ref(str(current_ref or "").strip())
    expected = parse_image_ref(str(pinned_ref or "").strip())

    current_digest = str(current.get("pinned_digest") or "").strip().lower()
    expected_digest = str(expected.get("pinned_digest") or "").strip().lower()

    return bool(
        current_digest
        and expected_digest
        and current_digest == expected_digest
        and str(current.get("normalized_repo") or "").strip().lower()
            == str(expected.get("normalized_repo") or "").strip().lower()
    )


def remember_restore_pin_assignments(
    compose_project,
    stack_key,
    mappings,
    *,
    backup_id=None,
    source="backup-restore",
):
    """Persist the user's original tracking source behind our restore digest pin."""
    project = str(compose_project or "").strip()
    stack_key = str(stack_key or "").strip()
    if not project:
        return False

    loaded = load_json(RESTORE_PIN_STATE_FILE, {})
    state = loaded if isinstance(loaded, dict) else {}
    changed = False

    with RESTORE_PIN_STATE_LOCK:
        for mapping in mappings or []:
            if not isinstance(mapping, dict):
                continue

            tracking_ref = str(mapping.get("tracking_ref") or "").strip()
            pinned_ref = str(mapping.get("pinned_ref") or "").strip()
            if (
                not tracking_ref
                or not pinned_ref
                or parse_image_ref(tracking_ref).get("pinned_digest")
                or not parse_image_ref(pinned_ref).get("pinned_digest")
            ):
                continue

            for service in mapping.get("services") or []:
                service = str(service or "").strip()
                key = _restore_pin_state_key(project, service)
                if not key:
                    continue

                row = {
                    "compose_project": project,
                    "compose_service": service,
                    "stack_key": stack_key or None,
                    "tracking_ref": tracking_ref,
                    "pinned_ref": pinned_ref,
                    "backup_id": str(backup_id or "").strip() or None,
                    "source": str(source or "backup-restore"),
                    "updated_at": utc_now(),
                }

                previous = state.get(key)
                if not isinstance(previous, dict) or any(
                    previous.get(field) != row.get(field)
                    for field in (
                        "tracking_ref",
                        "pinned_ref",
                        "stack_key",
                        "backup_id",
                        "source",
                    )
                ):
                    state[key] = row
                    changed = True

        if changed:
            save_json(RESTORE_PIN_STATE_FILE, state)

    return changed


def _restore_pin_from_persistent_state(
    stack_key,
    compose_project,
    services,
    literal_refs,
):
    loaded = load_json(RESTORE_PIN_STATE_FILE, {})
    state = loaded if isinstance(loaded, dict) else {}

    tracking_refs = set()
    rows = []

    for service in sorted(services or []):
        key = _restore_pin_state_key(compose_project, service)
        row = state.get(key)
        if not isinstance(row, dict):
            return None

        current_ref = str((literal_refs or {}).get(service) or "").strip()
        pinned_ref = str(row.get("pinned_ref") or "").strip()
        tracking_ref = str(row.get("tracking_ref") or "").strip()

        if (
            not current_ref
            or not _restore_pin_refs_match(current_ref, pinned_ref)
            or not tracking_ref
            or parse_image_ref(tracking_ref).get("pinned_digest")
        ):
            return None

        saved_stack = str(row.get("stack_key") or "").strip()
        if saved_stack and stack_key and saved_stack != stack_key:
            return None

        tracking_refs.add(tracking_ref)
        rows.append(row)

    if len(tracking_refs) != 1 or not rows:
        return None

    return {
        "tracking_ref": next(iter(tracking_refs)),
        "source": "restore-state",
        "backup_id": rows[0].get("backup_id"),
    }


def _restore_pin_from_backup_history(
    stack_key,
    compose_project,
    services,
    literal_refs,
):
    """Infer provenance for backups restored before v0.3.163."""
    app_stub = {
        "stack_key": str(stack_key or "").strip(),
        "compose_project": str(compose_project or "").strip(),
    }

    try:
        records = _backup_records_for_app(app_stub)
    except Exception:
        return None

    service_set = {
        str(value or "").strip()
        for value in (services or [])
        if str(value or "").strip()
    }
    if not service_set:
        return None

    for record in records:
        meta = record.get("metadata") or {}

        for image in meta.get("images") or []:
            if not isinstance(image, dict):
                continue

            tracking_ref = str(image.get("image_ref") or "").strip()
            pinned_ref = _preferred_backup_digest(image)

            if (
                not tracking_ref
                or not pinned_ref
                or parse_image_ref(tracking_ref).get("pinned_digest")
                or not parse_image_ref(pinned_ref).get("pinned_digest")
            ):
                continue

            image_services = {
                str(container.get("compose_service") or "").strip()
                for container in (image.get("containers") or [])
                if isinstance(container, dict)
                and str(container.get("compose_service") or "").strip()
            }

            if not service_set.issubset(image_services):
                continue

            if not all(
                _restore_pin_refs_match(
                    (literal_refs or {}).get(service),
                    pinned_ref,
                )
                for service in service_set
            ):
                continue

            mapping = {
                "tracking_ref": tracking_ref,
                "pinned_ref": pinned_ref,
                "services": sorted(service_set),
            }
            remember_restore_pin_assignments(
                compose_project,
                stack_key,
                [mapping],
                backup_id=record.get("backup_id"),
                source="legacy-backup-inference",
            )

            return {
                "tracking_ref": tracking_ref,
                "source": "backup-history",
                "backup_id": record.get("backup_id"),
            }

    return None


def restore_generated_digest_pin_info(
    stack_key,
    compose_project,
    services,
    literal_refs,
):
    """Identify only digest pins created by Update Monitor backup restore."""
    service_set = {
        str(value or "").strip()
        for value in (services or [])
        if str(value or "").strip()
    }
    if not service_set:
        return None

    # Every relevant Compose service must currently be digest-pinned.
    if not all(
        compose_literal_digest_pin((literal_refs or {}).get(service))
        for service in service_set
    ):
        return None

    persisted = _restore_pin_from_persistent_state(
        stack_key,
        compose_project,
        service_set,
        literal_refs,
    )
    if persisted:
        return persisted

    return _restore_pin_from_backup_history(
        stack_key,
        compose_project,
        service_set,
        literal_refs,
    )



def compose_configured_image_ref(
    compose_project,
    compose_service,
    runtime_ref,
    compose_map_cache=None,
    compose_catalog=None,
    stack_key=None,
):
    """Resolve one canonical source with persistent fallback."""
    project = str(compose_project or "").strip()
    service = str(compose_service or "").strip()
    fallback = str(runtime_ref or "").strip()

    if not project or not service:
        return fallback

    configured = None

    catalog = compose_catalog
    if catalog is None:
        catalog = casaos_compose_service_image_catalog()

    if isinstance(catalog, dict):
        project_map = catalog.get(project)
        if isinstance(project_map, dict):
            configured = str(project_map.get(service) or "").strip() or None

    cache = compose_map_cache if isinstance(compose_map_cache, dict) else {}

    if not configured:
        if project not in cache:
            try:
                cache[project] = compose_service_image_map_from_yaml(
                    casaos_compose_yaml(project)
                )
            except Exception:
                cache[project] = {}

        mapping = cache.get(project) or {}
        configured = str(mapping.get(service) or "").strip() or None

    if configured:
        remember_image_source_assignment(
            project,
            service,
            configured,
            stack_key=stack_key,
            source="zimaos-compose",
        )
        return configured

    persisted = saved_image_source_ref(project, service)
    return persisted or fallback


def _image_source_compose_configured_ref(
    app_item,
    item,
    *,
    compose_catalog=None,
    compose_map_cache=None,
):
    marker = str((item or {}).get("_canonical_source_ref") or "").strip()
    if marker:
        return marker

    project = str((app_item or {}).get("compose_project") or "").strip()
    services = _image_source_service_names(item or {})
    fallback = str((item or {}).get("image_ref") or "").strip()
    stack_key = str((app_item or {}).get("stack_key") or "").strip()

    if not project or not services:
        return fallback

    found = []

    for service in services:
        value = compose_configured_image_ref(
            project,
            service,
            fallback,
            compose_map_cache,
            compose_catalog,
            stack_key=stack_key,
        )
        value = str(value or "").strip()

        if value and value not in found:
            found.append(value)

    return found[0] if len(found) == 1 else fallback


def _image_source_live_item(
    app_item,
    item,
    *,
    compose_catalog=None,
    compose_map_cache=None,
):
    live = copy.deepcopy(item or {})

    current_ref = _image_source_compose_configured_ref(
        app_item,
        live,
        compose_catalog=compose_catalog,
        compose_map_cache=compose_map_cache,
    )
    current_ref = str(current_ref or "").strip()

    if current_ref:
        previous_ref = str(live.get("image_ref") or "").strip()
        live["image_ref"] = current_ref
        live["_canonical_source_ref"] = current_ref

        if previous_ref and previous_ref != current_ref:
            live["previous_image_ref"] = previous_ref

        try:
            parsed = parse_image_ref(current_ref)
            live["tag"] = parsed.get("tag")
        except Exception:
            pass

    return live



def _image_source_item_key(item):
    """Stable identity across Docker Hub <-> GHCR source changes."""
    stack_key = str((item or {}).get("stack_key") or "").strip()
    services = sorted(_image_source_service_names(item or {}))
    image_ref = str((item or {}).get("image_ref") or "").strip()

    identity = [stack_key] + services
    if not services:
        identity.append(image_ref)

    raw = "\n".join(identity)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:20]


def _image_source_items(app_item):
    result = []
    seen = set()

    for item in (app_item or {}).get("items") or []:
        if not isinstance(item, dict):
            continue

        image_ref = str(item.get("image_ref") or "").strip()
        if not image_ref:
            continue

        repo = _image_source_repo(image_ref)
        if not repo or repo.startswith("local/"):
            continue

        services = _image_source_service_names(item)
        containers = _image_source_container_names(item)
        if not services or not containers:
            continue

        image_key = _image_source_item_key(item)
        if image_key in seen:
            continue
        seen.add(image_key)
        result.append(item)

    return result


def _image_source_find_item(app_item, image_key):
    wanted = str(image_key or "").strip()
    if not wanted:
        return None
    for item in _image_source_items(app_item):
        if _image_source_item_key(item) == wanted:
            return item
    return None


def image_source_available_for_app(app_item):
    # The entry is visible for every normal Compose stack with at least one
    # registry-backed image. Every image is investigated separately when opened.
    if not str((app_item or {}).get("compose_project") or "").strip():
        return False
    return bool(_image_source_items(app_item))

def _image_source_current_platform():
    with scan_lock:
        value = str(scan_state.get("platform") or "").strip()
    if value:
        return value

    rc, raw, error = run(
        ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
        timeout=20,
    )
    if rc != 0:
        raise RuntimeError(error or "Could not determine Docker platform")
    platform_map = {
        "linux/x86_64": "linux/amd64",
        "linux/amd64": "linux/amd64",
        "linux/aarch64": "linux/arm64",
        "linux/arm64": "linux/arm64",
        "linux/armv7l": "linux/arm/v7",
    }
    return platform_map.get(raw.strip(), raw.strip())


def _image_source_inspect_container(container_name):
    rc, raw, error = run(["docker", "inspect", str(container_name)], timeout=30)
    if rc != 0:
        raise RuntimeError(error or f"Could not inspect container {container_name}")
    try:
        payload = json.loads(raw or "[]")
    except Exception as exc:
        raise RuntimeError(f"Invalid Docker inspect data for {container_name}") from exc
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise RuntimeError(f"No Docker inspect data for {container_name}")
    return payload[0]


def _image_source_env_map(container):
    result = {}
    for entry in ((container.get("Config") or {}).get("Env") or []):
        raw = str(entry or "")
        key, sep, value = raw.partition("=")
        if sep and key:
            result[key] = value
    return result


def _image_source_port_keys(container):
    values = set()
    host = container.get("HostConfig") or {}
    for key in (host.get("PortBindings") or {}):
        key = str(key or "").strip().lower()
        if key:
            values.add(key)
    for key in ((container.get("Config") or {}).get("ExposedPorts") or {}):
        key = str(key or "").strip().lower()
        if key:
            values.add(key)
    return values


def _image_source_mount_destinations(container):
    return {
        str(mount.get("Destination") or "").strip()
        for mount in container.get("Mounts") or []
        if str(mount.get("Destination") or "").strip()
    }


def _image_source_check(level, key, label_de, label_en, detail_de, detail_en):
    return {
        "level": level,
        "key": key,
        "label_de": label_de,
        "label_en": label_en,
        "detail_de": detail_de,
        "detail_en": detail_en,
    }


def _image_source_level(checks):
    levels = {str(item.get("level") or "") for item in checks or []}
    if "error" in levels:
        return "error"
    if "warn" in levels:
        return "warn"
    return "ok"


def _image_source_service_names(item):
    result = []
    for container in item.get("containers") or []:
        value = str(container.get("compose_service") or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _image_source_container_names(item):
    result = []
    for container in item.get("containers") or []:
        value = str(container.get("name") or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _image_source_registry_label(image_ref):
    repo = _image_source_repo(image_ref)
    if repo.startswith("lscr.io/linuxserver/") or repo.startswith("docker.io/linuxserver/"):
        return "LinuxServer"
    if repo.startswith("ghcr.io/"):
        return "GitHub Container Registry"
    if repo.startswith("docker.io/"):
        return "Docker Hub"
    host = repo.split("/", 1)[0] if "/" in repo else repo
    return host or "Docker Registry"


def _image_source_source_id(image_ref):
    repo = _image_source_repo(image_ref).lower()
    safe = re.sub(r"[^a-z0-9_.:/-]+", "-", repo).strip("-")
    return "auto:" + safe[:180]


def _image_source_tag_for_candidate(current_ref):
    parsed = parse_image_ref(str(current_ref or "").strip())
    tag = str(parsed.get("tag") or "latest").strip()
    # Digest pins cannot be transferred to another repository.
    if parsed.get("pinned_digest"):
        return "latest"
    return tag or "latest"


def _image_source_manifest_exists(image_ref, platform):
    parsed = parse_image_ref(str(image_ref or "").strip())
    repo = str(parsed.get("normalized_repo") or "").strip()
    tag = str(parsed.get("tag") or "latest").strip()
    if not repo:
        return False, set(), "Missing repository"
    digests, error = registry_manifest_digests(
        repo,
        tag,
        platform,
        timeout=12,
    )
    normalized = {
        str(value or "").strip().lower()
        for value in (digests or [])
        if str(value or "").strip()
    }
    return bool(normalized and not error), normalized, error


def _image_source_target_version(source, platform, remote_digests):
    image_ref = str(source.get("image_ref") or "").strip()
    parsed = parse_image_ref(image_ref)
    repo_key = str(parsed.get("normalized_repo") or "").strip()
    current_tag = str(parsed.get("tag") or "latest").strip()

    try:
        values, error = registry_tags(repo_key, max_pages=5)
    except Exception as exc:
        values, error = [], str(exc)

    if error or not values:
        return None, str(error or "No registry tags returned")

    selectable = registry_selectable_version_tags(
        values,
        repo_key=repo_key,
        limit=120,
        current_tag=current_tag,
    )
    if not selectable:
        return None, "No concrete version tags were found"

    try:
        resolved = resolve_installed_version_tag(
            repo_key,
            set(remote_digests or []),
            selectable,
            platform,
            batch_size=4,
            deadline_seconds=18.0,
            cache_result=False,
        )
    except Exception as exc:
        return None, str(exc)

    return (str(resolved).strip() or None), None


def _image_source_github_project_url(app_item, item, current_ref):
    """
    Resolve the GitHub project for exactly one image.

    Multi-image stacks must not inherit an unrelated app-level project URL
    from another service. Per-image scan metadata and local OCI labels win.
    The app-level URL is used only when the stack has exactly one image.
    """
    candidates = []

    value = (item or {}).get("project_url")
    if value:
        candidates.append(str(value).strip())

    if current_ref:
        local_image_url = image_project_url(current_ref)
        if local_image_url:
            candidates.append(local_image_url)

        ghcr_url = github_url_from_image_ref(current_ref)
        if ghcr_url:
            candidates.append(ghcr_url)

    if len(_image_source_items(app_item)) == 1:
        value = (app_item or {}).get("project_url")
        if value:
            candidates.append(str(value).strip())

    for candidate in candidates:
        if github_repo_from_url(candidate):
            return candidate
    return None


def _image_source_github_search_exact(app_item, item, current_ref):
    """
    Conservative fallback for one concrete image. Exact names only.
    A repository is still ignored unless a matching GHCR manifest exists.
    """
    names = []

    repo = _image_source_repo(current_ref)
    base_name = repo.rsplit("/", 1)[-1] if repo else ""
    if base_name:
        names.append(base_name)
        if base_name.startswith("docker-"):
            names.append(base_name[len("docker-"):])

    for service in _image_source_service_names(item or {}):
        names.append(service)

    if len(_image_source_items(app_item)) == 1:
        app_name = str((app_item or {}).get("name") or "").strip()
        if app_name:
            names.append(app_name)

    normalized_names = []
    for value in names:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
        if clean and clean.casefold() not in {x.casefold() for x in normalized_names}:
            normalized_names.append(clean)

    if not normalized_names:
        return []

    headers = _github_api_headers()
    found = []
    seen = set()

    for name in normalized_names[:3]:
        query = urllib.parse.quote(f"{name} in:name", safe="")
        url = f"https://api.github.com/search/repositories?q={query}&per_page=8"
        req = urllib.request.Request(
            url,
            headers={**headers, "User-Agent": f"Update-Monitor/{VERSION}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8", "replace"))
        except Exception:
            continue

        for entry in (payload or {}).get("items") or []:
            if not isinstance(entry, dict):
                continue

            repo_name = str(entry.get("name") or "").strip()
            full_name = str(entry.get("full_name") or "").strip()
            html_url = normalize_project_url(entry.get("html_url"))
            if not repo_name or not full_name or not html_url:
                continue
            if repo_name.casefold() != name.casefold():
                continue
            if full_name.casefold() in seen:
                continue

            seen.add(full_name.casefold())
            found.append({
                "project_url": html_url,
                "owner": full_name.split("/", 1)[0],
                "repo": full_name.split("/", 1)[1],
                "source": "github-search-exact",
            })

        if found:
            break

    return found[:4]

def _image_source_curated_sources(app_item, current_ref):
    current_repo = _image_source_repo(current_ref)
    results = []
    for catalog in IMAGE_SOURCE_CATALOG.values():
        if current_repo not in set(catalog.get("match_repos") or set()):
            continue
        for source in catalog.get("sources") or []:
            results.append(dict(source))
    return results


def _image_source_auto_candidates(app_item, item, current_ref, platform):
    current_parsed = parse_image_ref(str(current_ref or "").strip())
    current_repo = str(current_parsed.get("normalized_repo") or "").strip()
    current_tag = _image_source_tag_for_candidate(current_ref)
    candidates = []
    seen_repos = {current_repo.casefold()}

    def add(image_ref, *, project_url=None, source_kind="github-project",
            compatibility_mode="discovered", label_de=None, label_en=None):
        parsed = parse_image_ref(str(image_ref or "").strip())
        repo = str(parsed.get("normalized_repo") or "").strip()
        if not repo or repo.casefold() in seen_repos:
            return

        seen_repos.add(repo.casefold())
        exists, digests, _ = _image_source_manifest_exists(image_ref, platform)
        if not exists:
            return

        candidates.append({
            "id": _image_source_source_id(image_ref),
            "label_de": label_de or "GitHub Image",
            "label_en": label_en or "GitHub image",
            "image_ref": image_ref,
            "project_url": project_url,
            "compatibility_mode": compatibility_mode,
            "discovery_source": source_kind,
            "_discovered_digests": sorted(digests),
        })

    # LinuxServer -> same image family in GHCR.
    linuxserver_prefixes = (
        "lscr.io/linuxserver/",
        "docker.io/linuxserver/",
    )
    for prefix in linuxserver_prefixes:
        if current_repo.startswith(prefix):
            name = current_repo[len(prefix):].split("/", 1)[0]
            if name:
                add(
                    f"ghcr.io/linuxserver/{name}:{current_tag}",
                    project_url=f"https://github.com/linuxserver/docker-{name}",
                    source_kind="linuxserver-ghcr",
                    compatibility_mode="mirror",
                    label_de="LinuxServer GitHub Image",
                    label_en="LinuxServer GitHub image",
                )
            break

    github_repos = []
    seen_projects = set()

    github_url = _image_source_github_project_url(app_item, item, current_ref)
    if github_url:
        parsed_repo = github_repo_from_url(github_url)
        if parsed_repo:
            full_key = f"{parsed_repo[0]}/{parsed_repo[1]}".casefold()
            seen_projects.add(full_key)
            github_repos.append({
                "project_url": github_url,
                "owner": parsed_repo[0],
                "repo": parsed_repo[1],
                "source": "project-metadata",
            })

    # Also perform exact-name discovery. In a multi-image stack this is what
    # lets redis/postgres/worker/etc. resolve independently of the main app.
    for repo_entry in _image_source_github_search_exact(app_item, item, current_ref):
        full_key = f"{repo_entry['owner']}/{repo_entry['repo']}".casefold()
        if full_key in seen_projects:
            continue
        seen_projects.add(full_key)
        github_repos.append(repo_entry)

    for repo_entry in github_repos:
        owner = repo_entry["owner"]
        repo_name = repo_entry["repo"]
        project_url = repo_entry["project_url"]

        repo_variants = [repo_name]
        if repo_name.lower().startswith("docker-") and len(repo_name) > 7:
            repo_variants.append(repo_name[7:])

        for variant in repo_variants:
            add(
                f"ghcr.io/{owner}/{variant}:{current_tag}",
                project_url=project_url,
                source_kind=repo_entry.get("source") or "github-project",
                compatibility_mode="discovered",
                label_de="GitHub Image",
                label_en="GitHub image",
            )

            if current_tag.lower() != "latest":
                add(
                    f"ghcr.io/{owner}/{variant}:latest",
                    project_url=project_url,
                    source_kind=repo_entry.get("source") or "github-project",
                    compatibility_mode="discovered",
                    label_de="GitHub Image",
                    label_en="GitHub image",
                )

    return candidates


def _image_source_current_fallback_option(item, image_key, error=None):
    """
    Always provide the current image source even when project/registry discovery
    fails. The current source must never disappear from the Image Quelle UI.
    """
    current_ref = str((item or {}).get("image_ref") or "").strip()
    services = _image_source_service_names(item or {})
    containers = _image_source_container_names(item or {})
    current_version = str(
        (item or {}).get("resolved_installed_tag")
        or (item or {}).get("display_installed_version")
        or (item or {}).get("tag")
        or ""
    ).strip() or None

    checks = [
        _image_source_check(
            "ok",
            "current_source",
            "Aktuelle Quelle",
            "Current source",
            f"Aktuelles Image: {current_ref or '-'}",
            f"Current image: {current_ref or '-'}",
        )
    ]

    if error:
        checks.append(
            _image_source_check(
                "warn",
                "discovery",
                "Quellensuche",
                "Source discovery",
                f"Weitere Quellen konnten nicht vollständig untersucht werden: {str(error)[:220]}",
                f"Additional sources could not be fully inspected: {str(error)[:220]}",
            )
        )

    return {
        "catalog_key": None,
        "image_key": image_key,
        "source_id": _image_source_source_id(current_ref),
        "label_de": _image_source_registry_label(current_ref),
        "label_en": _image_source_registry_label(current_ref),
        "image_ref": current_ref,
        "project_url": None,
        "discovery_source": "current-fallback",
        "current": True,
        "version": current_version,
        "level": "warn" if error else "ok",
        "can_apply": False,
        "requires_warning_confirmation": False,
        "checks": checks,
        "compose_preview": {
            "current_image": current_ref,
            "target_image": current_ref,
            "services": list(services),
        },
    }


def _image_source_discovery_cache_key(app_item, item, current_ref, platform):
    return "|".join([
        str((app_item or {}).get("stack_key") or ""),
        _image_source_item_key(item),
        _image_source_repo(current_ref),
        str(parse_image_ref(str(current_ref or "")).get("tag") or "latest"),
        str(platform or ""),
    ])

def _image_source_discover_sources(app_item, item):
    if not item:
        raise RuntimeError("Image not found in this app")

    current_ref = _image_source_compose_configured_ref(app_item, item)
    current_repo = _image_source_repo(current_ref)
    if not current_repo:
        raise RuntimeError("Could not determine the current Docker image repository")

    platform = _image_source_current_platform()
    cache_key = _image_source_discovery_cache_key(app_item, item, current_ref, platform)
    now = time.time()

    with IMAGE_SOURCE_DISCOVERY_CACHE_LOCK:
        cached = IMAGE_SOURCE_DISCOVERY_CACHE.get(cache_key)
        if cached and now - float(cached.get("time") or 0) <= IMAGE_SOURCE_DISCOVERY_TTL_SECONDS:
            return item, platform, [dict(x) for x in cached.get("sources") or []], list(cached.get("errors") or [])

    discovery_errors = []

    try:
        project_url = _image_source_github_project_url(app_item, item, current_ref)
    except Exception as exc:
        project_url = None
        discovery_errors.append(f"Project metadata: {str(exc)[:180]}")

    current_source = {
        "id": _image_source_source_id(current_ref),
        "label_de": _image_source_registry_label(current_ref),
        "label_en": _image_source_registry_label(current_ref),
        "image_ref": current_ref,
        "project_url": project_url,
        "compatibility_mode": "current",
        "discovery_source": "current",
    }

    sources = [current_source]
    seen = {current_repo.casefold()}

    try:
        curated_sources = _image_source_curated_sources(app_item, current_ref)
    except Exception as exc:
        curated_sources = []
        discovery_errors.append(f"Verified sources: {str(exc)[:180]}")

    for source in curated_sources:
        try:
            repo = _image_source_repo(source.get("image_ref"))
            if not repo or repo.casefold() in seen:
                continue
            exists, digests, error = _image_source_manifest_exists(
                source.get("image_ref"),
                platform,
            )
            if not exists:
                if error:
                    discovery_errors.append(
                        f"{source.get('image_ref')}: {str(error)[:140]}"
                    )
                continue
            source["_discovered_digests"] = sorted(digests)
            sources.append(source)
            seen.add(repo.casefold())
        except Exception as exc:
            discovery_errors.append(
                f"{source.get('image_ref') or 'verified source'}: {str(exc)[:140]}"
            )

    try:
        automatic_sources = _image_source_auto_candidates(
            app_item,
            item,
            current_ref,
            platform,
        )
    except Exception as exc:
        automatic_sources = []
        discovery_errors.append(f"Automatic GitHub/GHCR search: {str(exc)[:180]}")

    for source in automatic_sources:
        try:
            repo = _image_source_repo(source.get("image_ref"))
            if not repo or repo.casefold() in seen:
                continue
            sources.append(source)
            seen.add(repo.casefold())
        except Exception as exc:
            discovery_errors.append(
                f"{source.get('image_ref') or 'automatic source'}: {str(exc)[:140]}"
            )

    with IMAGE_SOURCE_DISCOVERY_CACHE_LOCK:
        IMAGE_SOURCE_DISCOVERY_CACHE[cache_key] = {
            "time": now,
            "sources": [dict(x) for x in sources],
            "errors": list(discovery_errors),
        }

    return item, platform, sources, discovery_errors

def _image_source_source_for_id(app_item, image_key, source_id):
    item = _image_source_find_item(app_item, image_key)
    if not item:
        raise RuntimeError("The selected image is no longer available")

    item = _image_source_live_item(app_item, item)
    item, platform, sources, discovery_errors = _image_source_discover_sources(app_item, item)
    for source in sources:
        if str(source.get("id") or "") == str(source_id or ""):
            return item, platform, source, sources

    raise RuntimeError("The selected image source is no longer available")

def build_image_source_option(app_item, image_key, source_id):
    item, platform, source, sources = _image_source_source_for_id(app_item, image_key, source_id)

    project = str(app_item.get("compose_project") or "").strip()
    if not project:
        raise RuntimeError("Image source switching requires a ZimaOS Compose app")

    service_names = _image_source_service_names(item)
    container_names = _image_source_container_names(item)
    if not service_names or not container_names:
        raise RuntimeError("Could not resolve the Compose service for this image")

    current_ref = _image_source_compose_configured_ref(app_item, item)
    current_repo = _image_source_repo(current_ref)
    target_ref = str(source.get("image_ref") or "").strip()
    target_repo = _image_source_repo(target_ref)
    is_current = current_repo.casefold() == target_repo.casefold()
    compatibility_mode = str(source.get("compatibility_mode") or "discovered")

    checks = []
    target_version = None
    remote_digests = set(source.get("_discovered_digests") or [])
    current_container = None

    try:
        current_container = _image_source_inspect_container(container_names[0])
    except RuntimeError as exc:
        checks.append(_image_source_check(
            "error", "docker_inspect",
            "Docker Konfiguration", "Docker configuration",
            str(exc), str(exc),
        ))

    if current_container is not None:
        mounts = _image_source_mount_destinations(current_container)
        ports = _image_source_port_keys(current_container)
        env = _image_source_env_map(current_container)
        health_test = " ".join(
            str(value or "")
            for value in (((current_container.get("Config") or {}).get("Healthcheck") or {}).get("Test") or [])
        )

        if compatibility_mode == "curated":
            required_mounts = set(source.get("required_mounts") or set())
            missing_mounts = sorted(required_mounts - mounts)
            checks.append(_image_source_check(
                "error" if missing_mounts else "ok",
                "config_path",
                "Konfigurationspfad", "Configuration path",
                ("Fehlt: " + ", ".join(missing_mounts)) if missing_mounts
                else "Die bekannten Konfigurationspfade sind kompatibel.",
                ("Missing: " + ", ".join(missing_mounts)) if missing_mounts
                else "The known configuration paths are compatible.",
            ))

            required_ports = {
                str(value).lower() for value in source.get("required_ports") or set()
            }
            missing_ports = sorted(required_ports - ports)
            checks.append(_image_source_check(
                "error" if missing_ports else "ok",
                "ports",
                "Port", "Port",
                ("Benötigter Container Port fehlt: " + ", ".join(missing_ports))
                if missing_ports else "Die bekannten Container Ports sind kompatibel.",
                ("Required container port is missing: " + ", ".join(missing_ports))
                if missing_ports else "The known container ports are compatible.",
            ))

            required_env = set(source.get("required_env") or set())
            missing_env = sorted(
                key for key in required_env
                if not str(env.get(key) or "").strip()
            )
            checks.append(_image_source_check(
                "error" if missing_env else "ok",
                "environment",
                "Umgebungsvariablen", "Environment variables",
                ("Pflichtwerte fehlen: " + ", ".join(missing_env))
                if missing_env else "Die bekannten Umgebungsvariablen sind kompatibel.",
                ("Required values are missing: " + ", ".join(missing_env))
                if missing_env else "The known environment variables are compatible.",
            ))

            health_port = str(source.get("health_port") or "")
            if health_test and health_port and health_port not in health_test:
                checks.append(_image_source_check(
                    "warn", "healthcheck",
                    "Healthcheck", "Health check",
                    f"Der vorhandene Healthcheck enthält Port {health_port} nicht eindeutig.",
                    f"The existing health check does not clearly reference port {health_port}.",
                ))
            else:
                checks.append(_image_source_check(
                    "ok", "healthcheck",
                    "Healthcheck", "Health check",
                    "Der vorhandene Healthcheck ist mit dem bekannten Zielprofil vereinbar.",
                    "The existing health check is compatible with the known target profile.",
                ))

        elif compatibility_mode in {"current", "mirror"}:
            checks.extend([
                _image_source_check(
                    "ok", "config_path",
                    "Konfigurationspfad", "Configuration path",
                    f"{len(mounts)} vorhandene Mounts bleiben unverändert.",
                    f"{len(mounts)} existing mounts remain unchanged.",
                ),
                _image_source_check(
                    "ok", "ports",
                    "Ports", "Ports",
                    f"{len(ports)} vorhandene Container Ports bleiben unverändert.",
                    f"{len(ports)} existing container ports remain unchanged.",
                ),
                _image_source_check(
                    "ok", "environment",
                    "Umgebungsvariablen", "Environment variables",
                    f"{len(env)} vorhandene Variablen bleiben unverändert.",
                    f"{len(env)} existing variables remain unchanged.",
                ),
                _image_source_check(
                    "ok", "runtime_contract",
                    "Image Familie", "Image family",
                    (
                        "Aktuelle Quelle."
                        if compatibility_mode == "current"
                        else "Gleicher Publisher und gleiche Image Familie; nur die Registry Quelle ändert sich."
                    ),
                    (
                        "Current source."
                        if compatibility_mode == "current"
                        else "Same publisher and image family; only the registry source changes."
                    ),
                ),
            ])
        else:
            # Automatically discovered upstream/alternative packages are kept
            # yellow because registry metadata alone cannot prove that their
            # internal filesystem/env contract matches the current container.
            checks.extend([
                _image_source_check(
                    "warn", "config_path",
                    "Konfigurationspfad", "Configuration path",
                    f"Die aktuellen Mounts ({len(mounts)}) würden unverändert übernommen; das Ziel Image beschreibt die Pfadkompatibilität nicht eindeutig.",
                    f"The current mounts ({len(mounts)}) would be preserved; the target image does not describe path compatibility unambiguously.",
                ),
                _image_source_check(
                    "warn", "ports",
                    "Ports", "Ports",
                    f"Die aktuellen Ports ({len(ports)}) würden unverändert übernommen; die Zielanwendung muss dieselben internen Ports verwenden.",
                    f"The current ports ({len(ports)}) would be preserved; the target application must use the same internal ports.",
                ),
                _image_source_check(
                    "warn", "environment",
                    "Umgebungsvariablen", "Environment variables",
                    f"Die aktuellen Variablen ({len(env)}) würden übernommen; ihre Bedeutung kann bei einem anderen Image abweichen.",
                    f"The current variables ({len(env)}) would be preserved; their meaning may differ in another image.",
                ),
                _image_source_check(
                    "warn", "runtime_contract",
                    "Image Vertrag", "Image contract",
                    "GitHub/GHCR Quelle automatisch gefunden. Der vollständige Laufzeitvertrag kann ohne Start des Zielcontainers nicht sicher bestätigt werden.",
                    "GitHub/GHCR source was discovered automatically. The complete runtime contract cannot be proven without starting the target container.",
                ),
            ])

    # Target manifest + architecture.
    try:
        if not remote_digests:
            exists, remote_digests, manifest_error = _image_source_manifest_exists(
                target_ref,
                platform,
            )
            if not exists:
                raise RuntimeError(manifest_error or "No target digest returned")

        checks.append(_image_source_check(
            "ok", "architecture",
            "Image und Architektur", "Image and architecture",
            f"Ziel Image ist für {platform} erreichbar.",
            f"Target image is available for {platform}.",
        ))

        if is_current:
            target_version = str(
                item.get("resolved_installed_tag")
                or item.get("display_installed_version")
                or item.get("tag")
                or ""
            ).strip() or None
        else:
            target_version, version_error = _image_source_target_version(
                source,
                platform,
                remote_digests,
            )
            if target_version:
                checks.append(_image_source_check(
                    "ok", "version",
                    "Version", "Version",
                    f"Zielversion erkannt: {target_version}",
                    f"Target version detected: {target_version}",
                ))
            else:
                checks.append(_image_source_check(
                    "warn", "version",
                    "Version", "Version",
                    "Das Ziel Image existiert, aber seine konkrete Version konnte nicht eindeutig aufgelöst werden.",
                    "The target image exists, but its concrete version could not be resolved unambiguously.",
                ))
    except Exception as exc:
        checks.append(_image_source_check(
            "error", "architecture",
            "Image und Architektur", "Image and architecture",
            f"Ziel Image konnte für die aktuelle Plattform nicht bestätigt werden: {str(exc)[:220]}",
            f"The target image could not be confirmed for the current platform: {str(exc)[:220]}",
        ))

    compose_preview = {
        "current_image": current_ref,
        "target_image": target_ref,
        "services": list(service_names),
    }

    # Current source does not need to rewrite Compose just to be displayed.
    if is_current:
        checks.append(_image_source_check(
            "ok", "compose",
            "Docker Compose", "Docker Compose",
            "Aktuelle Compose Quelle ist aktiv.",
            "The current Compose source is active.",
        ))
    else:
        try:
            yaml_text = casaos_compose_yaml(project)
            updated_yaml, replacements, changed_services = replace_compose_service_images(
                yaml_text,
                service_names,
                target_ref,
            )
            if replacements < 1:
                raise RuntimeError("No matching Compose image entry was found")
            casaos_apply_compose(project, updated_yaml, dry_run=True)
            checks.append(_image_source_check(
                "ok", "compose",
                "Docker Compose", "Docker Compose",
                "ZimaOS Dry Run erfolgreich. Die übrige Compose Konfiguration bleibt erhalten.",
                "ZimaOS dry run succeeded. The remaining Compose configuration is preserved.",
            ))
            compose_preview["services"] = list(changed_services or service_names)
        except Exception as exc:
            checks.append(_image_source_check(
                "error", "compose",
                "Docker Compose", "Docker Compose",
                f"ZimaOS Dry Run fehlgeschlagen: {str(exc)[:220]}",
                f"ZimaOS dry run failed: {str(exc)[:220]}",
            ))

    level = _image_source_level(checks)
    return {
        "catalog_key": None,
        "image_key": _image_source_item_key(item),
        "source_id": source.get("id"),
        "label_de": source.get("label_de"),
        "label_en": source.get("label_en"),
        "image_ref": target_ref,
        "project_url": source.get("project_url"),
        "discovery_source": source.get("discovery_source"),
        "current": is_current,
        "version": target_version,
        "level": level,
        "can_apply": (not is_current and level != "error"),
        "requires_warning_confirmation": level == "warn",
        "checks": checks,
        "compose_preview": compose_preview,
        "_remote_digests": sorted(remote_digests),
        "_platform": platform,
        "_service_names": service_names,
        "_container_names": container_names,
        "_current_ref": current_ref,
    }


def image_source_options_for_app(app_item):
    raw_items = _image_source_items(app_item)
    if not raw_items:
        raise RuntimeError("This app has no registry-backed Compose images")

    compose_catalog = casaos_compose_service_image_catalog()
    compose_map_cache = {}

    items = [
        _image_source_live_item(
            app_item,
            item,
            compose_catalog=compose_catalog,
            compose_map_cache=compose_map_cache,
        )
        for item in raw_items
    ]

    def inspect_one(item):
        image_key = _image_source_item_key(item)
        current_ref = str(item.get("image_ref") or "").strip()
        services = _image_source_service_names(item)
        containers = _image_source_container_names(item)
        current_version = str(
            item.get("resolved_installed_tag")
            or item.get("display_installed_version")
            or item.get("tag")
            or ""
        ).strip() or None

        options = []
        discovery_errors = []

        try:
            _, _, sources, discovery_errors = _image_source_discover_sources(
                app_item,
                item,
            )

            for source in sources:
                try:
                    option = build_image_source_option(
                        app_item,
                        image_key,
                        source.get("id"),
                    )
                    options.append({
                        key: value
                        for key, value in option.items()
                        if not str(key).startswith("_")
                    })
                except Exception as exc:
                    is_current = (
                        _image_source_repo(source.get("image_ref"))
                        == _image_source_repo(current_ref)
                    )
                    if is_current:
                        # Current source is mandatory. Fall back to a local
                        # descriptor instead of dropping the entire image.
                        options.append(
                            _image_source_current_fallback_option(
                                item,
                                image_key,
                                error=exc,
                            )
                        )
                    else:
                        discovery_errors.append(
                            f"{source.get('image_ref') or 'alternative'}: {str(exc)[:160]}"
                        )

        except Exception as exc:
            discovery_errors.append(str(exc)[:220])
            options = [
                _image_source_current_fallback_option(
                    item,
                    image_key,
                    error=exc,
                )
            ]

        # Absolute safety net: there must always be a visible current source.
        if not options:
            options = [
                _image_source_current_fallback_option(
                    item,
                    image_key,
                    error=(
                        "; ".join(discovery_errors[:2])
                        or "Source discovery returned no usable result"
                    ),
                )
            ]

        current_option = next((x for x in options if x.get("current")), None)
        if current_option is None:
            current_option = _image_source_current_fallback_option(
                item,
                image_key,
                error="Current image source was missing from the discovery result",
            )
            options.insert(0, current_option)

        current_source_id = str(
            current_option.get("source_id")
            or _image_source_source_id(current_ref)
        )
        alternative_count = sum(1 for x in options if not x.get("current"))

        if alternative_count:
            message_de = (
                f"{alternative_count} alternative Image Quelle(n) für dieses Image automatisch gefunden."
            )
            message_en = (
                f"{alternative_count} alternative image source(s) found automatically for this image."
            )
        elif discovery_errors:
            message_de = (
                "Aktuelle Image Quelle erkannt. Die automatische Suche nach weiteren Quellen "
                "konnte nicht vollständig abgeschlossen werden."
            )
            message_en = (
                "Current image source detected. Automatic discovery of additional sources "
                "could not be completed fully."
            )
        else:
            try:
                github_url = _image_source_github_project_url(
                    app_item,
                    item,
                    current_ref,
                )
            except Exception:
                github_url = None

            if github_url:
                message_de = (
                    "GitHub Projekt erkannt, aber kein zusätzliches erreichbares GHCR Image gefunden."
                )
                message_en = (
                    "GitHub project detected, but no additional reachable GHCR image was found."
                )
            else:
                message_de = (
                    "Keine alternative GitHub/GHCR Image Quelle automatisch gefunden."
                )
                message_en = (
                    "No alternative GitHub/GHCR image source was found automatically."
                )

        error_text = "; ".join(
            str(value) for value in discovery_errors[:3] if str(value).strip()
        )[:500] or None

        return {
            "image_key": image_key,
            "image_ref": current_ref,
            "services": services,
            "containers": containers,
            "service_label": ", ".join(services) if services else current_ref,
            "current_version": current_version,
            "current_source_id": current_source_id,
            "options": options,
            "alternative_count": alternative_count,
            "discovery_message_de": message_de,
            "discovery_message_en": message_en,
            # Discovery problems are warnings, not fatal image errors, because
            # the current source remains usable and visible.
            "warning_de": error_text,
            "warning_en": error_text,
            "error_de": None,
            "error_en": None,
        }

    if len(items) > 1:
        workers = min(4, len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            images = list(executor.map(inspect_one, items))
    else:
        images = [inspect_one(items[0])]

    alternative_count = sum(int(image.get("alternative_count") or 0) for image in images)
    warning_count = sum(1 for image in images if image.get("warning_de"))

    return {
        "schema_version": 2,
        "backend_version": VERSION,
        "stack_key": app_item.get("stack_key"),
        "app": app_item.get("name"),
        "image_count": len(images),
        "alternative_count": alternative_count,
        "warning_count": warning_count,
        "error_count": 0,
        "images": images,
        "backup": {
            "required": True,
            "default_mode": "full",
        },
    }

def perform_image_source_switch(app_item, image_key, source_id, accept_warnings=False):
    option = build_image_source_option(app_item, image_key, source_id)

    if option.get("current"):
        raise RuntimeError("The selected image source is already active")
    if option.get("level") == "error":
        raise RuntimeError(
            "The selected image source is not compatible with the current Compose configuration"
        )
    if option.get("level") == "warn" and not accept_warnings:
        raise RuntimeError(
            "Compatibility warnings must be accepted before switching this image source"
        )

    project = str(app_item.get("compose_project") or "").strip()
    stack_key = str(app_item.get("stack_key") or "").strip()
    target_ref = str(option.get("image_ref") or "").strip()
    current_ref = str(option.get("_current_ref") or "").strip()
    service_names = list(option.get("_service_names") or [])
    container_names = list(option.get("_container_names") or [])
    platform = str(option.get("_platform") or "").strip() or _image_source_current_platform()

    if not project or not stack_key or not target_ref or not current_ref:
        raise RuntimeError("Incomplete image source switch plan")

    update_action_progress(
        stack_key,
        18,
        determinate=True,
        phase="preparing",
    )

    original_yaml = casaos_compose_yaml(project)
    updated_yaml, replacements, changed_services = replace_compose_service_images(
        original_yaml,
        service_names,
        target_ref,
    )
    if replacements < 1:
        raise RuntimeError("No matching Compose image entry was found")

    # Re-run the dry-run immediately before mutation.
    update_action_progress(
        stack_key,
        20,
        determinate=True,
        phase="compose_check",
    )
    casaos_apply_compose(project, updated_yaml, dry_run=True)

    original_states = {
        name: (docker_container_state(name) or "unknown")
        for name in container_names
    }
    original_runtime_snapshots = {
        name: (docker_container_runtime_snapshot(name) or {})
        for name in container_names
    }
    original_restart_counts = {
        name: int(
            (original_runtime_snapshots.get(name) or {}).get("restart_count")
            or 0
        )
        for name in container_names
    }

    original_container_ids = {
        name: docker_container_id(name)
        for name in container_names
    }
    for name, state in original_states.items():
        if state not in {"running", "exited", "created", "dead"}:
            raise RuntimeError(
                f"Unsupported container state for safe image source switch: {name} ({state})"
            )
        if not original_container_ids.get(name):
            raise RuntimeError(
                f"Could not read the current Docker container ID for {name}"
            )

    update_action_progress(
        stack_key,
        30,
        determinate=True,
        phase="pulling",
    )
    pulled = docker_pull_verified(
        target_ref,
        platform,
        expected_digests=option.get("_remote_digests") or None,
    )
    expected_digests = list(pulled.get("expected_digests") or [])
    if not expected_digests:
        raise RuntimeError(
            "No verified target digest is available for the selected image source"
        )

    update_action_progress(
        stack_key,
        55,
        determinate=True,
        phase="pull_complete",
    )

    applied = False
    rollback_error = None
    try:
        update_action_progress(
            stack_key,
            60,
            determinate=True,
            phase="recreating",
        )
        casaos_apply_compose(project, updated_yaml, dry_run=False)
        applied = True

        # The image was already pulled before Compose mutation. A source switch
        # must therefore not sit silently for ten minutes waiting for ZimaOS.
        # Three minutes is ample for the local Compose recreation; otherwise
        # fail and roll back instead of looking like an endless loop.
        activation = wait_for_image_source_compose_activation(
            project,
            container_names,
            original_container_ids,
            target_ref,
            expected_digests,
            timeout=180,
            recreate_after=20,
            stack_key=stack_key,
        )

        update_action_progress(
            stack_key,
            75,
            determinate=True,
            phase="container_ready",
        )

        update_action_progress(
            stack_key,
            80,
            determinate=True,
            phase="verifying",
        )
        verified_runtime = {}
        verify_total = max(1, len(container_names))
        for verify_index, name in enumerate(container_names, start=1):
            # Cross-registry source changes can be configuration-only when
            # both registries publish the exact same digest. Verify real image
            # bits instead of demanding a new container ID / Config.Image text.
            verified = wait_for_image_source_target_runtime(
                name,
                target_ref,
                expected_digests,
                timeout=90,
            )

            current_id = str(verified.get("container_id") or "")
            baseline_restarts = (
                original_restart_counts.get(name, 0)
                if current_id == str(original_container_ids.get(name) or "")
                else 0
            )

            # Then explicitly reject boot loops / unhealthy startup.
            original_runtime = original_runtime_snapshots.get(name) or {}
            stable = wait_for_image_source_runtime_stable(
                name,
                timeout=120,
                stable_seconds=20,
                baseline_restart_count=baseline_restarts,
                expected_state=original_states.get(name) or "running",
                expected_exit_code=original_runtime.get("exit_code"),
                expected_runtime_snapshot=original_runtime,
                unhealthy_grace_seconds=30,
            )
            verified["runtime_stability"] = stable
            verified_runtime[name] = verified

            update_action_progress(
                stack_key,
                min(94, 80 + int(round((14 * verify_index) / verify_total))),
                determinate=True,
                phase="verifying",
            )

        update_action_progress(
            stack_key,
            96,
            determinate=True,
            phase="finalizing",
        )

        restored_states = {}
        for name, previous_state in original_states.items():
            current_state = docker_container_state(name)
            if previous_state != "running" and current_state == "running":
                docker_stop_container(name)
                current_state = docker_container_state(name)
            if previous_state == "running" and current_state != "running":
                raise RuntimeError(
                    f"Image source switch reached the target image, but {name} is not running "
                    f"(state={current_state or 'unknown'})"
                )
            restored_states[name] = current_state

        previous_policy = get_monitor_policy(stack_key)
        policy_after = save_monitor_policy(
            stack_key,
            "follow",
            None,
            auto_enabled=bool(previous_policy.get("auto_enabled")),
            auto_immediate=bool(previous_policy.get("auto_immediate")),
            auto_time=previous_policy.get("auto_time"),
            auto_days=previous_policy.get("auto_days"),
            auto_timezone=previous_policy.get("auto_timezone"),
            auto_backup_mode=previous_policy.get("auto_backup_mode"),
        )

        return {
            "project": project,
            "old_image_ref": current_ref,
            "new_image_ref": target_ref,
            "activation": activation,
            "source_id": source_id,
            "target_version": option.get("version"),
            "compose_services": changed_services,
            "containers": container_names,
            "verified_runtime": verified_runtime,
            "target_verification": pulled,
            "final_states": restored_states,
            "policy_after_update": policy_after,
            "engine": "auto-source-discovery+verified-switch+zimaos-compose",
        }

    except Exception as exc:
        if applied:
            try:
                update_action_progress(
                    stack_key,
                    determinate=False,
                    phase="rollback",
                )
                casaos_apply_compose(project, original_yaml, dry_run=True)
                casaos_apply_compose(project, original_yaml, dry_run=False)
                wait_for_version_compose_update(
                    project,
                    container_names,
                    current_ref,
                    timeout=180,
                )
                for name, previous_state in original_states.items():
                    current_state = docker_container_state(name)
                    if previous_state != "running" and current_state == "running":
                        docker_stop_container(name)
                    elif previous_state == "running" and current_state != "running":
                        docker_start_container(name)
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)

        message = str(exc)
        if rollback_error:
            message += f" | Automatic rollback also failed: {rollback_error}"
        elif applied:
            message += " | Previous Compose/image source was restored automatically."
        raise RuntimeError(message) from exc




_SELF_CONTAINER_IDENTITY_LOCK = threading.Lock()
_SELF_CONTAINER_IDENTITY = None


def update_monitor_self_identity():
    """Return the current Update Monitor container id/name when Docker can resolve it.

    Docker normally sets HOSTNAME to the running container id. Resolve that id through
    the mounted Docker socket once and cache the result. The configured/default
    container name is retained as a safe fallback for installations where Docker
    cannot resolve HOSTNAME for any reason.
    """
    global _SELF_CONTAINER_IDENTITY
    if _SELF_CONTAINER_IDENTITY is not None:
        return dict(_SELF_CONTAINER_IDENTITY)

    with _SELF_CONTAINER_IDENTITY_LOCK:
        if _SELF_CONTAINER_IDENTITY is not None:
            return dict(_SELF_CONTAINER_IDENTITY)

        fallback_name = str(
            os.getenv("UPDATE_MONITOR_CONTAINER_NAME", "update-monitor") or "update-monitor"
        ).strip().lstrip("/") or "update-monitor"

        candidates = []
        env_hostname = str(os.getenv("HOSTNAME", "") or "").strip()
        if env_hostname:
            candidates.append(env_hostname)
        try:
            file_hostname = Path("/etc/hostname").read_text(encoding="utf-8").strip()
        except Exception:
            file_hostname = ""
        if file_hostname and file_hostname not in candidates:
            candidates.append(file_hostname)

        resolved_id = None
        resolved_name = None
        for candidate in candidates:
            rc, out, _ = run(
                ["docker", "inspect", "--format", "{{.Id}}|{{.Name}}", candidate],
                timeout=8,
            )
            if rc != 0 or not str(out or "").strip():
                continue
            raw_id, _, raw_name = str(out).strip().partition("|")
            resolved_id = raw_id.strip() or None
            resolved_name = raw_name.strip().lstrip("/") or None
            if resolved_id or resolved_name:
                break

        _SELF_CONTAINER_IDENTITY = {
            "container_id": resolved_id,
            "container_name": resolved_name or fallback_name,
        }
        return dict(_SELF_CONTAINER_IDENTITY)


def is_update_monitor_self_app(app_item):
    """True when a scanned app contains the currently running Update Monitor container."""
    if not isinstance(app_item, dict):
        return False

    identity = update_monitor_self_identity()
    self_name = str(identity.get("container_name") or "").strip().lstrip("/").casefold()
    container_names = {
        str(container.get("name") or "").strip().lstrip("/").casefold()
        for container in (app_item.get("containers") or [])
        if isinstance(container, dict) and str(container.get("name") or "").strip()
    }

    # Some internal call sites may pass an item before app-level containers were
    # aggregated, so also inspect nested image items.
    for item in (app_item.get("items") or []):
        if not isinstance(item, dict):
            continue
        for container in (item.get("containers") or []):
            if not isinstance(container, dict):
                continue
            name = str(container.get("name") or "").strip().lstrip("/").casefold()
            if name:
                container_names.add(name)

    if self_name and self_name in container_names:
        return True

    # Fallback for the standard installation if Docker identity resolution was
    # unavailable. This is deliberately exact, not a substring match.
    fallback_name = str(
        os.getenv("UPDATE_MONITOR_CONTAINER_NAME", "update-monitor") or "update-monitor"
    ).strip().lstrip("/").casefold()
    return bool(fallback_name and fallback_name in container_names)


def annotate_self_protection(scan_payload):
    if not isinstance(scan_payload, dict):
        return scan_payload
    for app_item in (scan_payload.get("apps") or []):
        if isinstance(app_item, dict):
            app_item["self_protected"] = is_update_monitor_self_app(app_item)
    return scan_payload


def reject_self_destructive_action(app_item, action):
    if not is_update_monitor_self_app(app_item):
        return
    action = str(action or "").strip().lower()
    if action in {"stop", "restart", "uninstall"}:
        raise HTTPException(
            status_code=403,
            detail=(
                "Self-protection active: Update Monitor cannot stop, restart "
                "or uninstall itself"
            ),
        )


def self_update_handoff_owned_by_current_process(stack_key=None):
    """True while this process is the old instance waiting to be replaced."""
    marker = load_json(SELF_UPDATE_STATE_FILE, {})
    if not isinstance(marker, dict) or marker.get("consumed_at"):
        return False

    requested_stack = str(stack_key or "").strip()
    marker_stack = str(marker.get("stack_key") or "").strip()
    if requested_stack and marker_stack and requested_stack != marker_stack:
        return False

    old_id = str(marker.get("old_container_id") or "").strip()
    if not old_id:
        return False
    current_id = str(
        update_monitor_self_identity().get("container_id") or ""
    ).strip()
    return bool(current_id and current_id == old_id)


_SELF_UPDATE_HELPER_SCRIPT = r"""\
import base64
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

marker_path = Path('/app/cache/self-update.json')
payload = json.loads(base64.b64decode(os.environ['UM_SELF_UPDATE_PAYLOAD']).decode('utf-8'))


def write_marker(status, **extra):
    current = {}
    try:
        current = json.loads(marker_path.read_text(encoding='utf-8'))
        if not isinstance(current, dict):
            current = {}
    except Exception:
        current = {}
    current.update(payload)
    current.update(extra)
    current['status'] = status
    current['updated_at_epoch'] = time.time()
    tmp = marker_path.with_name(marker_path.name + '.tmp')
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding='utf-8')
    os.replace(tmp, marker_path)


def docker_json(*args):
    proc = subprocess.run(
        ['docker', *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except Exception:
        return None


try:
    time.sleep(4.0)
    write_marker('recreate-requested')

    base = Path('/host/run/casaos/app-management.url').read_text(encoding='utf-8').strip().rstrip('/')
    old_id = str(payload['old_container_id'])
    project = str(payload.get('compose_project') or '').strip()
    query = {'pull': 'true', 'force': 'true'}
    if project:
        query['app:name'] = project
    url = (
        base
        + '/v2/app_management/container/'
        + urllib.parse.quote(old_id, safe='')
        + '?'
        + urllib.parse.urlencode(query)
    )
    request = urllib.request.Request(
        url,
        data=b'',
        method='PATCH',
        headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        status = int(getattr(response, 'status', 200) or 200)
        response.read(1024 * 1024)
    if not 200 <= status < 300:
        raise RuntimeError(f'ZimaOS recreate returned HTTP {status}')
    write_marker('recreate-accepted', recreate_http_status=status)

    name = str(payload['container_name'])
    expected = {
        str(value).strip().lower()
        for value in (payload.get('expected_digests') or [])
        if str(value).strip()
    }
    deadline = time.time() + 600
    last_state = None

    while time.time() < deadline:
        rows = docker_json('inspect', name)
        row = rows[0] if isinstance(rows, list) and rows else None
        if isinstance(row, dict):
            current_id = str(row.get('Id') or '').strip()
            state = row.get('State') if isinstance(row.get('State'), dict) else {}
            running = bool(state.get('Running'))
            restarting = bool(state.get('Restarting'))
            health = str(((state.get('Health') or {}).get('Status') or '')).strip().lower()
            image_id = str(row.get('Image') or '').strip()
            repo_digests = set()
            if image_id:
                image_rows = docker_json('image', 'inspect', image_id)
                image_row = image_rows[0] if isinstance(image_rows, list) and image_rows else None
                if isinstance(image_row, dict):
                    repo_digests = {
                        str(value).rsplit('@', 1)[-1].strip().lower()
                        for value in (image_row.get('RepoDigests') or [])
                        if '@sha256:' in str(value)
                    }
            last_state = {
                'container_id': current_id,
                'running': running,
                'restarting': restarting,
                'health': health or None,
                'image_id': image_id,
                'repo_digests': sorted(repo_digests),
            }
            if (
                current_id
                and current_id != old_id
                and running
                and not restarting
                and health != 'unhealthy'
                and expected
                and repo_digests.intersection(expected)
            ):
                write_marker(
                    'restarted-verified',
                    new_container_id=current_id,
                    new_image_id=image_id,
                    matched_digests=sorted(repo_digests.intersection(expected)),
                )
                break
        time.sleep(2.0)
    else:
        write_marker('verification-timeout', last_runtime=last_state)
except Exception as exc:
    write_marker('failed', error=str(exc)[:1000])
"""


def _launch_self_update_helper(marker):
    payload = base64.b64encode(
        json.dumps(marker, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    old_id = str(marker.get("old_container_id") or "").strip()
    image_ref = str(marker.get("image_ref") or "").strip()
    if not old_id or not image_ref:
        raise RuntimeError("Self update handoff is missing Docker identity information")

    helper_name = "update-monitor-self-update-" + secrets.token_hex(5)
    rc, out, err = run(
        [
            "docker", "run", "-d", "--rm",
            "--name", helper_name,
            "--network", "host",
            "--volumes-from", old_id,
            "--entrypoint", "python",
            "-e", "UM_SELF_UPDATE_PAYLOAD=" + payload,
            image_ref,
            "-c", _SELF_UPDATE_HELPER_SCRIPT,
        ],
        30,
    )
    if rc != 0:
        raise RuntimeError(
            "Could not start the self update helper: " + str(err or out or f"Exit {rc}")
        )

    marker["helper_container_name"] = helper_name
    marker["helper_container_id"] = str(out or "").strip() or None
    marker["status"] = "helper-started"
    marker["helper_started_at"] = utc_now()
    save_json(SELF_UPDATE_STATE_FILE, marker)
    return helper_name


def perform_self_image_update_handoff(app_item):
    """Pull and verify our own update, then hand recreation to a helper."""
    if not is_update_monitor_self_app(app_item):
        raise RuntimeError("Self update handoff was requested for a different app")

    project = str(app_item.get("compose_project") or "").strip()
    if not project:
        raise RuntimeError("ZimaOS Compose app ID is missing")

    identity = update_monitor_self_identity()
    self_name = str(identity.get("container_name") or "update-monitor").strip().lstrip("/")
    old_container_id = str(identity.get("container_id") or "").strip()
    if not old_container_id:
        old_container_id = str(docker_container_id(self_name) or "").strip()
    if not old_container_id:
        raise RuntimeError("Could not resolve the running Update Monitor container")

    target = None
    for item in (app_item.get("items") or []):
        if item.get("status") != "IMAGE_UPDATE" or item.get("update_policy") != "follow_tag":
            continue
        names = {
            str(container.get("name") or "").strip().lstrip("/")
            for container in (item.get("containers") or [])
            if isinstance(container, dict)
        }
        if self_name in names:
            target = item
            break
    if target is None:
        raise RuntimeError("No eligible same-tag self update was found")

    image_ref = str(
        target.get("tracking_image_ref") or target.get("image_ref") or ""
    ).strip()
    if not image_ref:
        raise RuntimeError("Could not determine the Update Monitor image")

    with scan_lock:
        update_platform = str(scan_state.get("platform") or "").strip()
    if not update_platform:
        rc, platform_raw, platform_error = run(
            ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
            timeout=20,
        )
        if rc != 0:
            raise RuntimeError(platform_error or "Could not determine Docker platform")
        platform_map = {
            "linux/x86_64": "linux/amd64",
            "linux/amd64": "linux/amd64",
            "linux/aarch64": "linux/arm64",
            "linux/arm64": "linux/arm64",
            "linux/armv7l": "linux/arm/v7",
        }
        update_platform = platform_map.get(platform_raw.strip(), platform_raw.strip())

    stack_key = str(app_item.get("stack_key") or "").strip()
    update_action_progress(stack_key, phase="pulling")
    pulled = docker_pull_verified(image_ref, update_platform)
    pulled["method"] = "verified-docker-pull+self-update-handoff"
    expected_digests = sorted({
        str(value or "").strip().lower()
        for value in (pulled.get("expected_digests") or [])
        if str(value or "").strip()
    })
    if not expected_digests:
        raise RuntimeError("No verified remote digest is available for the self update")

    before_image_id = str(docker_container_image_id(self_name) or "").strip()
    marker = {
        "schema": 1,
        "stack_key": stack_key,
        "compose_project": project,
        "container_name": self_name,
        "old_container_id": old_container_id,
        "old_image_id": before_image_id or None,
        "image_ref": image_ref,
        "expected_digests": expected_digests,
        "requested_at": utc_now(),
        "service_started_at": SERVICE_STARTED_AT,
        "status": "prepared",
    }
    save_json(SELF_UPDATE_STATE_FILE, marker)
    update_action_progress(stack_key, phase="restarting")
    helper_name = _launch_self_update_helper(marker)

    return {
        "project": project,
        "containers": [self_name],
        "before_image_ids": {self_name: before_image_id} if before_image_id else {},
        "target_verification": {image_ref: pulled},
        "self_update_handoff": True,
        "restart_expected": True,
        "service_started_at": SERVICE_STARTED_AT,
        "helper_container_name": helper_name,
        "engine": "verified-pull+self-update-helper",
    }


def reconcile_self_update_after_restart():
    """Verify a helper-driven replacement and queue one targeted self scan."""
    marker = load_json(SELF_UPDATE_STATE_FILE, {})
    if not isinstance(marker, dict) or marker.get("consumed_at"):
        return False

    old_id = str(marker.get("old_container_id") or "").strip()
    stack_key = str(marker.get("stack_key") or "").strip()
    image_ref = str(marker.get("image_ref") or "").strip()
    expected = {
        str(value or "").strip().lower()
        for value in (marker.get("expected_digests") or [])
        if str(value or "").strip()
    }
    if not old_id or not stack_key or not image_ref or not expected:
        return False

    deadline = time.time() + 90
    while time.time() < deadline:
        identity = update_monitor_self_identity()
        current_id = str(identity.get("container_id") or "").strip()
        name = str(identity.get("container_name") or marker.get("container_name") or "update-monitor").strip().lstrip("/")
        if current_id and current_id != old_id:
            image_id = str(docker_container_image_id(name) or "").strip()
            repo = str(parse_image_ref(image_ref).get("normalized_repo") or "").strip()
            local_digests = {
                str(value or "").strip().lower()
                for value in (docker_image_repo_digests(image_id, repo) if image_id else [])
                if str(value or "").strip()
            }
            runtime = docker_container_runtime_snapshot(name) or {}
            if (
                image_id
                and local_digests.intersection(expected)
                and str(runtime.get("status") or "") == "running"
                and not runtime.get("restarting")
                and runtime.get("health") != "unhealthy"
            ):
                marker["status"] = "restarted-verified"
                marker["new_container_id"] = current_id
                marker["new_image_id"] = image_id
                marker["matched_digests"] = sorted(local_digests.intersection(expected))
                marker["consumed_at"] = utc_now()
                save_json(SELF_UPDATE_STATE_FILE, marker)
                verification = {
                    "target_verification": {
                        image_ref: {
                            "image_ref": image_ref,
                            "expected_digests": sorted(expected),
                            "local_digests": sorted(local_digests),
                            "method": "self-update-restart-verification",
                        }
                    }
                }
                schedule_app_scan(
                    stack_key,
                    delay=0.25,
                    verification_result=verification,
                )
                return True
        time.sleep(1.0)

    marker["reconcile_error"] = "Replacement container did not reach the verified target digest"
    marker["reconcile_failed_at"] = utc_now()
    save_json(SELF_UPDATE_STATE_FILE, marker)
    return False

def _container_completed_one_shot(container):
    """Return True for a successfully finished on-failure one-shot container.

    Docker Compose commonly uses restart: on-failure for migration/init jobs.
    An exited container with exit code 0 under that policy is completed work,
    not a stopped/failed long-running service.
    """
    if not isinstance(container, dict):
        return False
    state = str(container.get("state") or "").strip().lower()
    restart_policy = str(container.get("restart_policy") or "").strip().lower()
    try:
        exit_code = int(container.get("exit_code"))
    except Exception:
        exit_code = None
    return state == "exited" and exit_code == 0 and restart_policy == "on-failure"


def _aggregate_app_runtime(containers):
    containers = [item for item in (containers or []) if isinstance(item, dict)]
    completed = [item for item in containers if _container_completed_one_shot(item)]
    active = [item for item in containers if not _container_completed_one_shot(item)]
    states = [str(item.get("state") or "unknown").strip().lower() for item in active]
    runtime_state, running_count = aggregate_runtime_state(states)
    # An app consisting only of completed one-shot jobs is completed rather than stopped.
    if not active and completed:
        runtime_state = "completed"
    return runtime_state, running_count, len(completed), len(active)


def build_apps(results):
    grouped = {}
    for item in results:
        key = item.get("stack_key") or ("image:" + item.get("image_ref", "unknown"))
        app_item = grouped.setdefault(key, {
            "stack_key": key,
            "name": item.get("display_title") or item.get("stack_name") or item.get("display_name") or item.get("image_ref"),
            "has_display_title": bool(item.get("display_title")),
            "compose_project": item.get("compose_project"),
            "compose_working_dir": item.get("compose_working_dir"),
            "compose_config_files": item.get("compose_config_files"),
            "icon_url": item.get("icon_url"),
            "project_url": item.get("project_url"),
            "project_provider": item.get("project_provider"),
            "project_url_source": item.get("project_url_source"),
            "items": [],
            "containers": [],
        })
        if not app_item.get("icon_url") and item.get("icon_url"):
            app_item["icon_url"] = item.get("icon_url")
        if not app_item.get("project_url") and item.get("project_url"):
            app_item["project_url"] = item.get("project_url")
            app_item["project_provider"] = item.get("project_provider")
            app_item["project_url_source"] = item.get("project_url_source")
        if not app_item.get("has_display_title") and item.get("display_title"):
            app_item["name"] = item.get("display_title")
            app_item["has_display_title"] = True
        app_item["items"].append(item)
        for container in item.get("containers", []):
            if not any(existing.get("name") == container.get("name") for existing in app_item["containers"]):
                app_item["containers"].append(container)

    backup_index = backup_summary_index()

    apps = []
    for app_item in grouped.values():
        items = app_item["items"]
        statuses = [x.get("status") for x in items]
        update_count = sum(1 for s in statuses if s in {"IMAGE_UPDATE", "VERSION_UPDATE"})
        image_update_count = statuses.count("IMAGE_UPDATE")
        version_update_count = statuses.count("VERSION_UPDATE")
        error_count = statuses.count("ERROR")
        pinned_count = statuses.count("PINNED")
        local_count = statuses.count("LOCAL")
        fixed_count = sum(1 for x in items if x.get("update_policy") == "fixed_version")
        held_update_count = sum(
            1 for x in items
            if x.get("update_policy") == "fixed_version" and x.get("newer_tag")
        )
        can_image_update = any(
            x.get("status") == "IMAGE_UPDATE"
            and x.get("update_policy") == "follow_tag"
            and x.get("compose_project")
            for x in items
        )

        if image_update_count:
            aggregate_status = "IMAGE_UPDATE"
        elif version_update_count:
            aggregate_status = "VERSION_UPDATE"
        elif error_count:
            aggregate_status = "ERROR"
        elif statuses and all(s == "PINNED" for s in statuses):
            aggregate_status = "PINNED"
        elif statuses and all(s == "LOCAL" for s in statuses):
            aggregate_status = "LOCAL"
        else:
            aggregate_status = "CURRENT"

        name = app_item.get("name")
        if not app_item.get("has_display_title"):
            if _looks_generated_stack_name(name) or _looks_generated_stack_name(app_item.get("compose_project")):
                name = _common_container_name(app_item["containers"]) or name
            name = clean_display_name(name)

        runtime_state, running_count, completed_count, active_container_count = (
            _aggregate_app_runtime(app_item["containers"])
        )

        created_values = [
            str(container.get("created_at") or "").strip()
            for container in app_item["containers"]
            if str(container.get("created_at") or "").strip()
        ]
        last_updated_at = max(created_values) if created_values else None
        app_ports = []
        seen_app_ports = set()
        for container in app_item["containers"]:
            for port_value in container.get("ports") or []:
                port_value = str(port_value or "").strip()
                if not port_value:
                    continue
                port_key = port_value.casefold()
                if port_key in seen_app_ports:
                    continue
                seen_app_ports.add(port_key)
                app_ports.append(port_value)

        def app_port_sort_key(value):
            raw = str(value or "")
            number = raw.split("/", 1)[0]
            try:
                return (0, int(number), raw)
            except Exception:
                return (1, 0, raw.lower())

        app_ports.sort(key=app_port_sort_key)

        built_app = {
            "stack_key": app_item["stack_key"],
            "name": name,
            "compose_project": app_item.get("compose_project"),
            "icon_url": app_item.get("icon_url"),
            "project_url": app_item.get("project_url"),
            "project_provider": app_item.get("project_provider"),
            "project_url_source": app_item.get("project_url_source"),
            "status": aggregate_status,
            "runtime_state": runtime_state,
            "running_count": running_count,
            "completed_count": completed_count,
            "active_container_count": active_container_count,
            "last_updated_at": last_updated_at,
            "update_count": update_count,
            "image_update_count": image_update_count,
            "version_update_count": version_update_count,
            "error_count": error_count,
            "pinned_count": pinned_count,
            "local_count": local_count,
            "fixed_count": fixed_count,
            "held_update_count": held_update_count,
            "can_image_update": can_image_update,
            "image_count": len(items),
            "container_count": len(app_item["containers"]),
            "ports": app_ports,
            "containers": sorted(app_item["containers"], key=lambda x: str(x.get("name", "")).lower()),
            "items": sorted(items, key=lambda x: str(x.get("image_ref", "")).lower()),
            "backup_summary": backup_summary_for_app(
                backup_index,
                app_item["stack_key"],
                app_item.get("compose_project"),
            ),
            "image_source_available": image_source_available_for_app(app_item),
            "self_protected": is_update_monitor_self_app(app_item),
        }
        apply_monitor_policy_fields(built_app)

        # v0.3.310: A discovered newer VERSION is informational while the
        # app policy is "fixed".  "Version fixiert" means the user explicitly
        # chose to keep the configured version, so that newer tag must not be
        # counted or presented as an actionable update.  Same-tag IMAGE updates
        # remain actionable because they do not change the configured version.
        policy_mode = str((built_app.get("monitor_policy") or {}).get("mode") or "fixed")
        effective_version_update_count = (
            0 if policy_mode == "fixed" else int(built_app.get("version_update_count") or 0)
        )
        if policy_mode != "fixed" and built_app.get("can_version_update"):
            effective_version_update_count = max(1, effective_version_update_count)
        effective_image_update_count = int(built_app.get("image_update_count") or 0)
        effective_update_count = effective_image_update_count + effective_version_update_count

        if effective_image_update_count:
            effective_status = "IMAGE_UPDATE"
        elif effective_version_update_count:
            effective_status = "VERSION_UPDATE"
        elif built_app.get("error_count"):
            effective_status = "ERROR"
        elif statuses and all(s == "PINNED" for s in statuses):
            effective_status = "PINNED"
        elif statuses and all(s == "LOCAL" for s in statuses):
            effective_status = "LOCAL"
        else:
            effective_status = "CURRENT"

        built_app["effective_update_count"] = effective_update_count
        built_app["effective_version_update_count"] = effective_version_update_count
        built_app["effective_status"] = effective_status
        apps.append(built_app)

    apps.sort(key=lambda x: str(x.get("name", "")).lower())
    summary = {
        "UPDATES": sum(1 for x in apps if int(x.get("effective_update_count") or 0) > 0),
        "CURRENT": sum(1 for x in apps if int(x.get("effective_update_count") or 0) == 0 and x["error_count"] == 0 and x["pinned_count"] == 0 and x["local_count"] == 0),
        "FIXED": sum(1 for x in apps if x["fixed_count"] > 0),
        "PINNED": sum(1 for x in apps if x["pinned_count"] > 0),
        "LOCAL": sum(1 for x in apps if x["local_count"] > 0),
        "ERROR": sum(1 for x in apps if x["error_count"] > 0),
        "TOTAL": len(apps),
    }
    return apps, summary



def update_scan_progress(step, percent, current=0, total=0):
    """Publish lightweight in-memory progress for the running scan."""
    try:
        percent = max(0, min(100, int(percent)))
    except Exception:
        percent = 0

    with scan_lock:
        if scan_state.get("state") != "scanning":
            return
        scan_state["progress_step"] = str(step or "starting")
        scan_state["progress_percent"] = percent
        scan_state["progress_current"] = max(0, int(current or 0))
        scan_state["progress_total"] = max(0, int(total or 0))



LOCAL_SCAN_CACHE_SCHEMA = 1
PIN_CLASSIFICATION_SCHEMA = 4


def _local_scan_cache_hash(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _local_scan_cache_stack_rows(results):
    """Build comparable local-only rows from persisted scan results."""
    grouped = {}

    for item in results or []:
        if not isinstance(item, dict):
            continue
        stack_key = str(item.get("stack_key") or "").strip()
        image_ref = str(item.get("image_ref") or "").strip()
        if not stack_key or not image_ref:
            continue

        grouped.setdefault(stack_key, []).append({
            "image_ref": image_ref,
            "image_ids": sorted(
                {
                    str(value or "").strip()
                    for value in (item.get("local_image_ids") or [])
                    if str(value or "").strip()
                }
            ),
            "local_digests": sorted(
                {
                    str(value or "").strip().lower()
                    for value in (item.get("local_digests") or [])
                    if str(value or "").strip()
                }
            ),
            "local_image_created": str(
                item.get("local_image_created") or ""
            ).strip(),
        })

    return grouped


def local_scan_cache_snapshot_from_results(results):
    """Convert a completed scan into a persistent local comparison snapshot."""
    grouped = _local_scan_cache_stack_rows(results)
    stacks = {}

    for stack_key, rows in grouped.items():
        rows = sorted(
            rows,
            key=lambda row: (
                row.get("image_ref") or "",
                tuple(row.get("image_ids") or []),
            ),
        )
        strict_rows = [
            {
                "image_ref": row["image_ref"],
                "image_ids": list(row.get("image_ids") or []),
            }
            for row in rows
        ]
        legacy_rows = [
            {
                "image_ref": row["image_ref"],
                "local_digests": list(row.get("local_digests") or []),
                "local_image_created": row.get("local_image_created") or "",
            }
            for row in rows
        ]

        has_strict_ids = all(bool(row.get("image_ids")) for row in rows)
        stacks[stack_key] = {
            "signature": (
                _local_scan_cache_hash(strict_rows)
                if has_strict_ids
                else None
            ),
            # Migration path for older scan.json files created before v0.3.152.
            "legacy_signature": _local_scan_cache_hash(legacy_rows),
            "image_count": len(rows),
        }

    return {
        "schema_version": LOCAL_SCAN_CACHE_SCHEMA,
        "generated_at": utc_now(),
        "stacks": stacks,
    }


def collect_current_local_scan_cache_snapshot():
    """Read only local Docker state for fast startup validation.

    This function deliberately performs no registry, GitHub, GHCR or Docker Hub
    requests. It is safe to use on every Update Monitor restart.
    """
    rc, ids_raw, err = run(["docker", "ps", "-aq"], timeout=20)
    if rc != 0:
        raise RuntimeError(err or "docker ps failed during startup cache check")

    ids = [value.strip() for value in ids_raw.splitlines() if value.strip()]
    if not ids:
        return {
            "schema_version": LOCAL_SCAN_CACHE_SCHEMA,
            "generated_at": utc_now(),
            "stacks": {},
        }

    rc, raw, err = run(["docker", "inspect"] + ids, timeout=60)
    if rc != 0:
        raise RuntimeError(err or "docker inspect failed during startup cache check")

    try:
        containers_raw = select_current_compose_containers(json.loads(raw))
    except Exception as exc:
        raise RuntimeError("Could not parse local Docker state for startup cache") from exc

    deployments = {}
    compose_image_map_cache = {}
    compose_literal_image_map_cache = {}
    compose_image_catalog = casaos_compose_service_image_catalog()
    for container in containers_raw:
        runtime_ref = str(((container.get("Config") or {}).get("Image")) or "").strip()
        image_id = str(container.get("Image") or "").strip()
        if not runtime_ref or not image_id:
            continue

        meta = container_stack_metadata(container)
        literal_compose_ref = compose_literal_image_ref(
            meta.get("compose_project"),
            meta.get("compose_service"),
            compose_literal_image_map_cache,
        )
        ref = compose_configured_image_ref(
            meta.get("compose_project"),
            meta.get("compose_service"),
            runtime_ref,
            compose_image_map_cache,
            compose_image_catalog,
            stack_key=meta.get("stack_key"),
        )
        if literal_compose_ref:
            ref = literal_compose_ref

        managed_latest_ref = zimaos_managed_latest_digest_ref(
            ref,
            runtime_refs=[runtime_ref] if runtime_ref else [],
            local_repo_tags=[],
        )
        if managed_latest_ref:
            ref = managed_latest_ref

        stack_key = str(meta.get("stack_key") or "").strip()
        if not stack_key:
            continue

        key = (stack_key, ref)
        entry = deployments.setdefault(key, {
            "stack_key": stack_key,
            "image_ref": ref,
            "image_ids": set(),
            "container_names": set(),
            "compose_project": str(meta.get("compose_project") or "").strip(),
        })
        entry["image_ids"].add(image_id)

        name = str(container.get("Name") or "").lstrip("/").strip()
        if name:
            entry["container_names"].add(name)

    image_meta_cache = {}
    grouped = {}

    for (_, _), deployment in deployments.items():
        ref = deployment["image_ref"]
        parsed = parse_image_ref(ref)
        local_digests = set()
        newest_created = ""

        for image_id in deployment["image_ids"]:
            if image_id not in image_meta_cache:
                inspect_template = (
                    '{"repo_digests":{{json .RepoDigests}},'
                    '"created":{{json .Created}}}'
                )
                rc, out, _ = run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        image_id,
                        "--format",
                        inspect_template,
                    ],
                    timeout=20,
                )
                if rc == 0 and out:
                    try:
                        payload = json.loads(out)
                    except Exception:
                        payload = {}
                else:
                    payload = {}
                image_meta_cache[image_id] = (
                    payload if isinstance(payload, dict) else {}
                )

            image_meta = image_meta_cache.get(image_id) or {}
            for repo_digest in image_meta.get("repo_digests") or []:
                repo, digest = parse_repo_digest(repo_digest)
                if repo == parsed["normalized_repo"] and digest:
                    local_digests.add(str(digest).strip().lower())

            created = str(image_meta.get("created") or "").strip()
            if created and created > newest_created:
                newest_created = created

        grouped.setdefault(deployment["stack_key"], []).append({
            "image_ref": ref,
            "image_ids": sorted(deployment["image_ids"]),
            "local_digests": sorted(local_digests),
            "local_image_created": newest_created,
            "compose_project": deployment.get("compose_project") or "",
            "container_names": sorted(deployment["container_names"]),
        })

    stacks = {}
    for stack_key, rows in grouped.items():
        rows = sorted(
            rows,
            key=lambda row: (
                row.get("image_ref") or "",
                tuple(row.get("image_ids") or []),
            ),
        )

        strict_rows = [
            {
                "image_ref": row["image_ref"],
                "image_ids": list(row.get("image_ids") or []),
            }
            for row in rows
        ]
        legacy_rows = [
            {
                "image_ref": row["image_ref"],
                "local_digests": list(row.get("local_digests") or []),
                "local_image_created": row.get("local_image_created") or "",
            }
            for row in rows
        ]

        stacks[stack_key] = {
            "signature": _local_scan_cache_hash(strict_rows),
            "legacy_signature": _local_scan_cache_hash(legacy_rows),
            "image_count": len(rows),
            "compose_project": next(
                (
                    row.get("compose_project")
                    for row in rows
                    if row.get("compose_project")
                ),
                "",
            ),
            "container_names": sorted({
                name
                for row in rows
                for name in (row.get("container_names") or [])
                if name
            }),
        }

    return {
        "schema_version": LOCAL_SCAN_CACHE_SCHEMA,
        "generated_at": utc_now(),
        "stacks": stacks,
    }


def _scan_cache_stack_signature_matches(cached, current):
    """Prefer exact image-ID comparison; fall back once for legacy cache data."""
    cached = cached if isinstance(cached, dict) else {}
    current = current if isinstance(current, dict) else {}

    cached_signature = str(cached.get("signature") or "").strip()
    current_signature = str(current.get("signature") or "").strip()

    if cached_signature and current_signature:
        return secrets.compare_digest(cached_signature, current_signature)

    cached_legacy = str(cached.get("legacy_signature") or "").strip()
    current_legacy = str(current.get("legacy_signature") or "").strip()
    if cached_legacy and current_legacy:
        return secrets.compare_digest(cached_legacy, current_legacy)

    return False


def _scan_cache_interval_due(now_utc=None):
    """The 1/6/12/24-hour clock is based only on a real FULL scan."""
    interval = int(settings.get("scan_interval_seconds", 0) or 0)
    if interval <= 0:
        return False

    now_utc = now_utc or datetime.now(timezone.utc)
    anchor = _parse_utc_timestamp(scan_state.get("last_full_scan_at"))
    if anchor is None:
        return True

    return (now_utc - anchor).total_seconds() >= interval


def _set_startup_cache_status(status, **extra):
    with scan_lock:
        payload = {
            "status": str(status or ""),
            "checked_at": utc_now(),
        }
        payload.update(extra)
        scan_state["startup_cache"] = payload
        save_json(SCAN_FILE, scan_state)


def _prune_removed_stacks_from_scan_snapshot(stack_keys):
    """Remove locally disappeared stacks without any internet update check."""
    remove_keys = {
        str(value or "").strip()
        for value in (stack_keys or [])
        if str(value or "").strip()
    }
    if not remove_keys:
        return False

    with scan_lock:
        results = [
            item
            for item in (scan_state.get("results") or [])
            if str((item or {}).get("stack_key") or "").strip() not in remove_keys
        ]

        counter = Counter(
            str(item.get("status") or "")
            for item in results
            if isinstance(item, dict)
        )
        fixed = sum(
            1
            for item in results
            if isinstance(item, dict)
            and item.get("update_policy") == "fixed_version"
        )
        apps, app_summary = build_apps(results)

        scan_state["results"] = results
        scan_state["apps"] = apps
        scan_state["summary"] = {
            "CURRENT": counter["CURRENT"],
            "IMAGE_UPDATE": counter["IMAGE_UPDATE"],
            "VERSION_UPDATE": counter["VERSION_UPDATE"],
            "PINNED": counter["PINNED"],
            "LOCAL": counter["LOCAL"],
            "ERROR": counter["ERROR"],
            "FIXED": fixed,
        }
        scan_state["app_summary"] = app_summary

        cached = scan_state.get("local_cache_snapshot")
        if isinstance(cached, dict):
            cached_stacks = dict(cached.get("stacks") or {})
            for key in remove_keys:
                cached_stacks.pop(key, None)
            cached["stacks"] = cached_stacks
            cached["generated_at"] = utc_now()
            scan_state["local_cache_snapshot"] = cached

        save_json(SCAN_FILE, scan_state)

    return True


def startup_cache_refresh_if_needed(baseline_finished_at):
    """Fast restart path: cached UI immediately, then local-only comparison.

    Decision:
      - configured interval is due -> one normal full scan
      - new stack appeared -> one normal full scan (it is not in old cache yet)
      - removed stack -> prune it locally, no internet scan
      - existing stack image/signature changed -> targeted scan only for that app
      - nothing local changed -> zero update checks
    """
    baseline_finished_at = str(baseline_finished_at or "")

    with scan_lock:
        current_finished_at = str(scan_state.get("finished_at") or "")
        scanning = scan_state.get("state") == "scanning"
        thread = scan_thread
        previous_results = list(scan_state.get("results") or [])
        cached_snapshot = copy.deepcopy(
            scan_state.get("local_cache_snapshot")
            if isinstance(scan_state.get("local_cache_snapshot"), dict)
            else {}
        )

    # A scan finished after startup or another Docker operation already owns the
    # common gate. Never stack a startup decision behind it.
    if current_finished_at != baseline_finished_at:
        return "superseded"
    if scanning or (thread and thread.is_alive()):
        return "busy"
    if full_scan_block_reason() or has_pending_app_scan():
        return "busy"

    # Respect the configured interval. Restarting the program never resets it.
    if _scan_cache_interval_due():
        _set_startup_cache_status("interval-due")
        return "full-scan" if start_scan(origin="startup-interval") else "busy"

    try:
        current_snapshot = collect_current_local_scan_cache_snapshot()
    except Exception as exc:
        _set_startup_cache_status(
            "local-check-failed",
            error=str(exc).splitlines()[0][:220],
        )
        # Preserve old behavior as the safe fallback when local validation itself
        # is impossible.
        return "full-scan" if start_scan(origin="startup-cache-fallback") else "busy"

    current_stacks = dict(current_snapshot.get("stacks") or {})
    cached_stacks = dict(cached_snapshot.get("stacks") or {})

    # First upgrade from older scan.json: derive a legacy comparable cache from
    # persisted results, then immediately seed the new strict image-ID cache if
    # local Docker still matches. This avoids forcing one unnecessary full scan
    # merely because v0.3.152 introduced the new cache schema.
    if not cached_stacks and previous_results:
        legacy_snapshot = local_scan_cache_snapshot_from_results(previous_results)
        cached_stacks = dict(legacy_snapshot.get("stacks") or {})

    cached_keys = set(cached_stacks)
    current_keys = set(current_stacks)

    added = sorted(current_keys - cached_keys)
    removed = sorted(cached_keys - current_keys)
    shared = sorted(current_keys & cached_keys)
    changed = sorted(
        key
        for key in shared
        if not _scan_cache_stack_signature_matches(
            cached_stacks.get(key),
            current_stacks.get(key),
        )
    )

    # A brand-new stack has no old app record from which a targeted scan can
    # safely resolve the Compose target. One full discovery scan is required.
    if added:
        _set_startup_cache_status(
            "topology-added",
            added_stacks=added,
            removed_stacks=removed,
            changed_stacks=changed,
        )
        return "full-scan" if start_scan(origin="startup-new-stack") else "busy"

    if removed:
        _prune_removed_stacks_from_scan_snapshot(removed)

    if changed:
        # Do not replace the cache signatures for changed apps yet. Their old
        # signatures intentionally remain until the targeted verification has
        # completed; a crash/restart therefore retries only those apps.
        for index, stack_key in enumerate(changed):
            schedule_app_scan(
                stack_key,
                delay=0.5 + (index * 0.15),
            )

        _set_startup_cache_status(
            "targeted-refresh",
            changed_stacks=changed,
            removed_stacks=removed,
        )
        return "targeted-refresh"

    # Pure cache hit (possibly after pruning removed apps): seed/refresh the
    # strict image-ID snapshot and keep the old full-scan timestamp untouched.
    with scan_lock:
        scan_state["local_cache_snapshot"] = current_snapshot
        scan_state["startup_cache"] = {
            "status": "hit",
            "checked_at": utc_now(),
            "changed_stacks": [],
            "removed_stacks": removed,
        }
        save_json(SCAN_FILE, scan_state)

    return "cache-hit"



def scan_all(stack_key=None):
    global scan_state

    post_update_verified_targets = {}
    if stack_key:
        with post_scan_lock:
            pending_context = dict(post_scan_pending.get(str(stack_key)) or {})
        post_update_verified_targets = dict(
            pending_context.get("verified_targets") or {}
        )

    previous_results = []
    previous_result_map = {}
    target_container_names = []
    target_compose_project = None

    with scan_lock:
        if scan_state.get("state") == "scanning":
            return

        previous_results = list(scan_state.get("results") or [])
        previous_result_map = {
            (
                str(previous.get("stack_key") or ""),
                str(previous.get("image_ref") or ""),
            ): previous
            for previous in previous_results
            if isinstance(previous, dict)
        }

        if stack_key:
            target_app = next(
                (
                    app_item
                    for app_item in (scan_state.get("apps") or [])
                    if app_item.get("stack_key") == stack_key
                ),
                None,
            )
            if not target_app:
                return

            target_compose_project = str(
                target_app.get("compose_project") or ""
            ).strip() or None

            for container in target_app.get("containers") or []:
                name = str(container.get("name") or "").strip()
                if name and name not in target_container_names:
                    target_container_names.append(name)

            if not target_compose_project and not target_container_names:
                return

        previous_last_full_scan_at = (
            scan_state.get("last_full_scan_at")
            or scan_state.get("finished_at")
        )
        previous_local_cache_snapshot = copy.deepcopy(
            scan_state.get("local_cache_snapshot")
            if isinstance(scan_state.get("local_cache_snapshot"), dict)
            else {}
        )

        scan_state = {
            "state": "scanning",
            "scan_scope": "app" if stack_key else "all",
            "scan_stack_key": stack_key,
            "started_at": utc_now(),
            "finished_at": None,
            "last_full_scan_at": previous_last_full_scan_at,
            "local_cache_snapshot": previous_local_cache_snapshot,
            "progress_step": "starting",
            "progress_percent": 2,
            "progress_current": 0,
            "progress_total": 0,
            "results": previous_results,
            "apps": scan_state.get("apps", []),
            "summary": scan_state.get("summary", {}),
            "app_summary": scan_state.get("app_summary", {}),
        }
        save_json(SCAN_FILE, scan_state)

    try:
        update_scan_progress("docker", 5)
        rc, platform_raw, err = run(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"])
        if rc != 0:
            raise RuntimeError(err or "docker info failed")
        platform_map = {
            "linux/x86_64": "linux/amd64",
            "linux/amd64": "linux/amd64",
            "linux/aarch64": "linux/arm64",
            "linux/arm64": "linux/arm64",
            "linux/armv7l": "linux/arm/v7",
        }
        platform = platform_map.get(platform_raw.strip(), platform_raw.strip())

        update_scan_progress("containers", 10)
        if stack_key:
            if target_compose_project:
                # IMPORTANT:
                # Re-discover CURRENT containers from Docker. Do not trust names
                # stored by the previous scan because ZimaOS may have recreated
                # or replaced the containers during the update.
                rc, ids_raw, err = run([
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={target_compose_project}",
                ])
                if rc != 0:
                    raise RuntimeError(err or "docker ps project filter failed")
                ids = [x for x in ids_raw.splitlines() if x.strip()]

                # Very short recreate windows can temporarily contain no match.
                # Fall back to known names only for this one attempt.
                if not ids:
                    ids = list(target_container_names)
            else:
                ids = list(target_container_names)
        else:
            rc, ids_raw, err = run(["docker", "ps", "-aq"])
            if rc != 0:
                raise RuntimeError(err or "docker ps failed")
            ids = [x for x in ids_raw.splitlines() if x.strip()]

        if not ids:
            update_scan_progress("results", 92)
            results = []
        else:
            update_scan_progress("inspect", 16, 0, len(ids))
            rc, raw, err = run(["docker", "inspect"] + ids, 60)
            if rc != 0:
                raise RuntimeError(err or "docker inspect failed")
            containers_raw = select_current_compose_containers(
                json.loads(raw)
            )

            # Keep the proven v0.3.0 logo path unchanged.
            # Titles are a separate display-only concern: use ZimaOS' own custom
            # app title where available, without changing stack keys or update logic.
            zima_title_catalog = casaos_title_catalog()

            # One deployment record per image + Compose stack. This prevents a shared
            # image (for example redis:alpine) from merging two unrelated apps.
            #
            # v0.3.155: ZimaOS Compose is the canonical source for Compose apps.
            # Docker Config.Image may legally keep an old registry alias after a
            # cross-registry source switch where both refs resolve to the same
            # image digest.
            deployments = {}
            compose_image_map_cache = {}
            compose_literal_image_map_cache = {}
            compose_image_catalog = casaos_compose_service_image_catalog()
            for c in containers_raw:
                name = c.get("Name", "").lstrip("/")
                runtime_ref = (c.get("Config", {}) or {}).get("Image", "")
                image_id = c.get("Image", "")
                state_name = (c.get("State", {}) or {}).get("Status", "unknown")
                if not runtime_ref:
                    continue
                meta = container_stack_metadata(c)

                literal_compose_ref = compose_literal_image_ref(
                    meta.get("compose_project"),
                    meta.get("compose_service"),
                    compose_literal_image_map_cache,
                )

                ref = compose_configured_image_ref(
                    meta.get("compose_project"),
                    meta.get("compose_service"),
                    runtime_ref,
                    compose_image_map_cache,
                    compose_image_catalog,
                    stack_key=meta.get("stack_key"),
                )

                # The literal Compose YAML is the source of truth whenever it
                # contains a concrete image value. Structured ZimaOS/Docker
                # metadata may append an internally resolved @sha256 digest.
                if literal_compose_ref:
                    ref = literal_compose_ref

                if not ref:
                    continue

                display_title = None
                for alias in (
                    meta.get("compose_project"),
                    meta.get("stack_name"),
                    meta.get("compose_service"),
                    name,
                ):
                    alias_key = str(alias or "").strip().casefold()
                    if alias_key and zima_title_catalog.get(alias_key):
                        display_title = zima_title_catalog[alias_key]
                        break

                key = (ref, meta["stack_key"])
                entry = deployments.setdefault(key, {
                    "image_ref": ref,
                    "stack_key": meta["stack_key"],
                    "stack_name": meta["stack_name"],
                    "compose_project": meta["compose_project"],
                    "compose_working_dir": meta["compose_working_dir"],
                    "compose_config_files": meta["compose_config_files"],
                    "icon_url": meta.get("icon_url"),
                    "project_url": meta.get("project_url"),
                    "project_provider": meta.get("project_provider"),
                    "project_url_source": meta.get("project_url_source"),
                    "display_title": display_title,
                    "containers": [],
                    "image_ids": set(),
                    "runtime_image_refs": set(),
                    "compose_literal_refs": {},
                })
                if not entry.get("icon_url") and meta.get("icon_url"):
                    entry["icon_url"] = meta.get("icon_url")
                if not entry.get("project_url") and meta.get("project_url"):
                    entry["project_url"] = meta.get("project_url")
                    entry["project_provider"] = meta.get("project_provider")
                    entry["project_url_source"] = meta.get("project_url_source")
                if not entry.get("display_title") and display_title:
                    entry["display_title"] = display_title
                state_payload = c.get("State") or {}
                restart_payload = ((c.get("HostConfig") or {}).get("RestartPolicy") or {})
                restart_policy = str(restart_payload.get("Name") or "").strip().lower()
                try:
                    exit_code = int(state_payload.get("ExitCode"))
                except Exception:
                    exit_code = None
                one_shot_completed = bool(
                    state_name == "exited"
                    and exit_code == 0
                    and restart_policy == "on-failure"
                )
                entry["containers"].append({
                    "name": name,
                    "state": state_name,
                    "exit_code": exit_code,
                    "restart_policy": restart_policy or None,
                    "one_shot_completed": one_shot_completed,
                    "compose_service": meta["compose_service"],
                    "compose_container_number": meta.get("compose_container_number"),
                    "created_at": c.get("Created"),
                    "ports": container_published_ports(c),
                    "network_mode": str((c.get("HostConfig") or {}).get("NetworkMode") or ""),
                    "runtime_image_ref": runtime_ref,
                })
                entry["image_ids"].add(image_id)
                if runtime_ref:
                    entry["runtime_image_refs"].add(str(runtime_ref))
                if literal_compose_ref and meta.get("compose_service"):
                    entry["compose_literal_refs"][
                        str(meta.get("compose_service"))
                    ] = str(literal_compose_ref)

            image_cache = {}
            remote_cache = {}
            tags_cache = {}
            github_scan_cache = {}
            results = []

            ordered_deployments = sorted(
                deployments.items(),
                key=lambda kv: (
                    str(kv[1]["stack_name"]).lower(),
                    str(kv[1]["image_ref"]).lower(),
                ),
            )
            deployment_total = len(ordered_deployments)
            update_scan_progress("images", 22, 0, deployment_total)

            for deployment_index, (_, group) in enumerate(ordered_deployments, start=1):
                before_percent = 22 + int(((deployment_index - 1) / max(1, deployment_total)) * 68)
                update_scan_progress(
                    "images",
                    before_percent,
                    deployment_index - 1,
                    deployment_total,
                )
                image_ref = group["image_ref"]

                item_services = {
                    str(container.get("compose_service") or "").strip()
                    for container in (group.get("containers") or [])
                    if str(container.get("compose_service") or "").strip()
                }
                literal_refs = dict(group.get("compose_literal_refs") or {})

                explicit_pin_values = [
                    compose_literal_digest_pin(literal_refs.get(service))
                    for service in sorted(item_services)
                ]
                explicit_digest_pin = bool(
                    item_services
                    and all(service in literal_refs for service in item_services)
                    and explicit_pin_values
                    and all(explicit_pin_values)
                )
                explicit_pinned_digest = (
                    str(explicit_pin_values[0]).strip()
                    if explicit_digest_pin
                    else None
                )

                restore_pin_info = (
                    restore_generated_digest_pin_info(
                        group.get("stack_key"),
                        group.get("compose_project"),
                        item_services,
                        literal_refs,
                    )
                    if explicit_digest_pin
                    else None
                )

                # Initial parse is corrected after Docker runtime/local tag
                # evidence has been collected below.
                tracking_image_ref = str(
                    (restore_pin_info or {}).get("tracking_ref")
                    or image_ref
                ).strip()
                parsed = parse_image_ref(tracking_image_ref)
                local = set()
                local_all_digests = set()
                local_digest_groups = []
                local_repo_tags = set()
                local_image_labels = {}
                local_image_created = None

                for image_id in group["image_ids"]:
                    if image_id not in image_cache:
                        inspect_template = (
                            '{"repo_digests":{{json .RepoDigests}},'
                            '"repo_tags":{{json .RepoTags}},'
                            '"labels":{{json .Config.Labels}},'
                            '"created":{{json .Created}}}'
                        )
                        rc, out, _ = run(
                            ["docker", "image", "inspect", image_id, "--format", inspect_template],
                            20,
                        )
                        try:
                            payload = json.loads(out) if rc == 0 and out else {}
                            image_cache[image_id] = payload if isinstance(payload, dict) else {}
                        except Exception:
                            image_cache[image_id] = {}

                    image_meta = image_cache.get(image_id) or {}
                    image_repo_digests = set()

                    for rd in image_meta.get("repo_digests") or []:
                        repo, digest = parse_repo_digest(rd)
                        if digest:
                            local_all_digests.add(
                                str(digest).strip().lower()
                            )
                        if repo == parsed["normalized_repo"] and digest:
                            normalized_digest = str(digest).strip().lower()
                            local.add(normalized_digest)
                            image_repo_digests.add(normalized_digest)

                    # Keep one digest set per active Docker image ID.  An empty set
                    # is retained deliberately: an unverified active image must not
                    # be hidden by another container that happens to match remote.
                    local_digest_groups.append(sorted(image_repo_digests))

                    for repo_tag in image_meta.get("repo_tags") or []:
                        repo_tag = str(repo_tag or "").strip()
                        if repo_tag:
                            local_repo_tags.add(repo_tag)

                    labels = image_meta.get("labels") or {}
                    if isinstance(labels, dict):
                        for key, value in labels.items():
                            if key not in local_image_labels and value is not None:
                                local_image_labels[str(key)] = value

                    created = str(image_meta.get("created") or "").strip()
                    if created and (not local_image_created or created > local_image_created):
                        local_image_created = created

                # Supplement local evidence from the canonical tag itself.
                canonical_local_digests = docker_image_repo_digests(
                    image_ref,
                    parsed["normalized_repo"],
                )
                if canonical_local_digests:
                    local.update(canonical_local_digests)
                    local_all_digests.update(canonical_local_digests)
                    if len(local_digest_groups) == 1:
                        local_digest_groups[0] = sorted(
                            set(local_digest_groups[0]).union(canonical_local_digests)
                        )

                zimaos_latest_tracking_ref = None
                if explicit_digest_pin and not restore_pin_info:
                    zimaos_latest_tracking_ref = zimaos_managed_latest_digest_ref(
                        image_ref,
                        runtime_refs=group.get("runtime_image_refs") or [],
                        local_repo_tags=local_repo_tags,
                    )

                if zimaos_latest_tracking_ref:
                    tracking_image_ref = zimaos_latest_tracking_ref
                    parsed = parse_image_ref(tracking_image_ref)

                # Immediate post-source verification may legitimately see the
                # target digest only under the old registry alias. The digest
                # hash identifies the image content independently of that alias.
                post_target_seed = _post_update_target_for_image(
                    post_update_verified_targets,
                    image_ref,
                )
                post_expected_seed = {
                    str(value or "").strip().lower()
                    for value in ((post_target_seed or {}).get("digests") or [])
                    if str(value or "").strip()
                }
                if (
                    post_target_seed
                    and not local
                    and local_all_digests.intersection(post_expected_seed)
                ):
                    local.update(
                        local_all_digests.intersection(post_expected_seed)
                    )

                resolved_project_link = resolve_project_link(
                    tracking_image_ref,
                    metadata_url=group.get("project_url"),
                    metadata_source=group.get("project_url_source"),
                )

                item = {
                    "image_ref": image_ref,
                    "tracking_image_ref": (
                        tracking_image_ref
                        if tracking_image_ref != image_ref
                        else None
                    ),
                    "restore_generated_digest_pin": bool(restore_pin_info),
                    "zimaos_managed_latest_digest": bool(
                        zimaos_latest_tracking_ref
                    ),
                    "zimaos_managed_latest_tracking_ref": (
                        zimaos_latest_tracking_ref or None
                    ),
                    "restore_pin_source": (
                        (restore_pin_info or {}).get("source")
                        if restore_pin_info
                        else None
                    ),
                    "restore_backup_id": (
                        (restore_pin_info or {}).get("backup_id")
                        if restore_pin_info
                        else None
                    ),
                    "display_name": display_name(tracking_image_ref, group["containers"]),
                    "stack_key": group["stack_key"],
                    "stack_name": group["stack_name"],
                    "compose_project": group.get("compose_project"),
                    "compose_working_dir": group.get("compose_working_dir"),
                    "compose_config_files": group.get("compose_config_files"),
                    "icon_url": group.get("icon_url"),
                    "project_url": (resolved_project_link or {}).get("url"),
                    "project_provider": (resolved_project_link or {}).get("provider"),
                    "project_url_source": (resolved_project_link or {}).get("source"),
                    "display_title": group.get("display_title"),
                    "containers": group["containers"],
                    "tag": parsed["tag"],
                    "resolved_installed_tag": None,
                    "display_installed_version": None,
                    "installed_version_resolution": None,
                    "local_digests": sorted(local),
                    "local_all_digests": sorted(local_all_digests),
                    "local_digest_groups": [list(values) for values in local_digest_groups],
                    "local_image_ids": sorted(
                        str(value or "").strip()
                        for value in group.get("image_ids") or []
                        if str(value or "").strip()
                    ),
                    "local_repo_tags": sorted(local_repo_tags),
                    "runtime_image_refs": sorted(
                        str(value or "").strip()
                        for value in group.get("runtime_image_refs") or []
                        if str(value or "").strip()
                    ),
                    "compose_literal_refs": dict(
                        group.get("compose_literal_refs") or {}
                    ),
                    "compose_digest_pin_proven": False,
                    "compose_pinned_digest": None,
                    "pin_classification_schema": PIN_CLASSIFICATION_SCHEMA,
                    "local_image_labels": local_image_labels,
                    "local_image_created": local_image_created,
                    "remote_digest": None,
                    "remote_digest_short": None,
                    "remote_platform_digest": None,
                    "resolved_available_tag": None,
                    "available_version_resolution": None,
                    "newer_tag": None,
                    "newer_tags": [],
                    "available_version_tags": [],
                    "status": None,
                    "update_policy": None,
                    "detail": "",
                    "scan_warning": None,
                }

                previous = previous_result_map.get(
                    (str(group["stack_key"]), str(image_ref))
                )
                previous_local = sorted(
                    str(value)
                    for value in ((previous or {}).get("local_digests") or [])
                    if str(value)
                )
                current_local = sorted(str(value) for value in local if str(value))
                previous_resolved = str(
                    (previous or {}).get("resolved_installed_tag") or ""
                ).strip()
                previous_resolution_source = str(
                    ((previous or {}).get("installed_version_resolution") or {}).get("source")
                    or ""
                ).strip()

                previous_is_untrusted_label = previous_resolution_source.startswith(
                    "local-image-label"
                )

                if (
                    previous_resolved
                    and previous_local == current_local
                    and not previous_is_untrusted_label
                ):
                    item["resolved_installed_tag"] = previous_resolved
                elif not _needs_installed_version_resolution(parsed["tag"]):
                    item["resolved_installed_tag"] = parsed["tag"]

                user_digest_pin = bool(
                    explicit_digest_pin
                    and not restore_pin_info
                    and not zimaos_latest_tracking_ref
                )

                item["compose_digest_pin_proven"] = user_digest_pin
                item["compose_pinned_digest"] = (
                    explicit_pinned_digest
                    if user_digest_pin
                    else None
                )

                # Backup restore digest locks and ZimaOS-managed `latest@digest`
                # refs are tracking mechanics, not user-selected immutable pins.
                #
                # Some perfectly normal registry images can temporarily have no
                # RepoDigests in local Docker metadata (retag/import/recreate).
                # Before calling such an image LOCAL, verify it against the
                # registry by comparing the active Docker image ID (OCI config
                # digest) with the config digest of the remote platform manifest.
                remote_config_same = None
                remote_registry_exists = False
                remote_probe_error = None
                remote_config_digest_value = None
                if not local and not user_digest_pin:
                    if parsed["base"] not in remote_cache:
                        remote_cache[parsed["base"]] = remote_digests(parsed["base"], platform)
                    probe_top, probe_platform_digest, probe_error = remote_cache[parsed["base"]]
                    if probe_error:
                        remote_probe_error = probe_error
                    else:
                        remote_registry_exists = True
                        probe_manifest_digest = probe_platform_digest or probe_top
                        remote_config_digest_value, remote_config_error = remote_manifest_config_digest(
                            parsed["base"],
                            probe_manifest_digest,
                        )
                        local_config_ids = {
                            str(value or "").strip().lower()
                            for value in (group.get("image_ids") or [])
                            if str(value or "").strip().lower().startswith("sha256:")
                        }
                        if remote_config_digest_value and local_config_ids:
                            remote_config_same = all(
                                value == remote_config_digest_value
                                for value in local_config_ids
                            )
                            item["local_config_digest_match"] = bool(remote_config_same)
                            item["remote_config_digest"] = remote_config_digest_value
                        else:
                            remote_probe_error = remote_config_error or "Local image ID unavailable"

                if user_digest_pin:
                    item.update(
                        status="PINNED",
                        update_policy="digest_pinned",
                        remote_digest=explicit_pinned_digest,
                        remote_digest_short=explicit_pinned_digest.replace("sha256:", "")[:12],
                        detail="Image is explicitly pinned by digest in Compose",
                    )
                elif not local and remote_config_same is None:
                    # A genuine registry image must never be labeled "LOCAL"
                    # merely because its local RepoDigests field is empty.  If
                    # the registry exists but we cannot complete config-digest
                    # verification, report a check error instead.  Only a real
                    # registry miss is classified as LOCAL.
                    if remote_registry_exists:
                        item.update(
                            status="ERROR",
                            update_policy=(
                                "fixed_version"
                                if parse_version(parsed["tag"])
                                else "follow_tag"
                            ),
                            detail=(
                                "Registry image found, but local image could not be verified: "
                                + str(remote_probe_error or "missing config digest").splitlines()[0][:180]
                            ),
                        )
                    elif remote_probe_error and _remote_digest_error_is_transient(remote_probe_error):
                        item.update(
                            status="ERROR",
                            update_policy=(
                                "fixed_version"
                                if parse_version(parsed["tag"])
                                else "follow_tag"
                            ),
                            detail=str(remote_probe_error).splitlines()[0][:180],
                        )
                    else:
                        item.update(
                            status="LOCAL",
                            update_policy="local",
                            detail="No registry digest and no matching local RepoDigest",
                        )
                else:
                    post_target = _post_update_target_for_image(
                        post_update_verified_targets,
                        image_ref,
                    )
                    post_expected = {
                        str(value or "").strip().lower()
                        for value in ((post_target or {}).get("digests") or [])
                        if str(value or "").strip()
                    }

                    if post_target:
                        # Immediate post-update verification is deliberately
                        # registry-independent. The update path already pulled and
                        # verified the exact target digest, so prove that the newly
                        # scanned local image still matches that target instead of
                        # asking Docker Hub/GHCR again and risking a false 429 ERROR.
                        if not post_expected or not local.intersection(post_expected):
                            item.update(
                                status="ERROR",
                                update_policy=(
                                    "fixed_version"
                                    if parse_version(parsed["tag"])
                                    else "follow_tag"
                                ),
                                detail=(
                                    "Post-update local verification failed: "
                                    f"local={sorted(local)}, expected={sorted(post_expected)}"
                                )[:500],
                            )
                        else:
                            verified_digest = sorted(local.intersection(post_expected))[0]
                            item["remote_digest"] = verified_digest
                            item["remote_digest_short"] = verified_digest.replace("sha256:", "")[:12]
                            item["post_update_verified"] = True
                            item["scan_warning"] = None
                            if parse_version(parsed["tag"]):
                                item["update_policy"] = "fixed_version"
                                item["status"] = "CURRENT"
                                item["resolved_installed_tag"] = parsed["tag"]
                                item["detail"] = "Post-update local digest verified"
                            else:
                                item["update_policy"] = "follow_tag"
                                item["status"] = "CURRENT"

                                # If this was a fixed-version -> :latest tag-only
                                # transition, the update path already knew the
                                # concrete version and proved the digest identical.
                                # Carry that version forward and cache it by digest.
                                # If :latest actually changed to a different image,
                                # no hint exists and the targeted resolver below is
                                # intentionally allowed to determine the new version
                                # for this one app only.
                                version_hint = str(
                                    (post_target or {}).get("resolved_version_hint") or ""
                                ).strip()
                                if version_hint and parse_version(version_hint):
                                    item["resolved_installed_tag"] = version_hint
                                    installed_version_cache_put(
                                        str(parsed.get("normalized_repo") or "").strip(),
                                        sorted(local.intersection(post_expected)),
                                        version_hint,
                                    )
                                    _installed_version_resolution_record(
                                        item,
                                        "resolved",
                                        "post-update-known-version",
                                        version_hint,
                                    )
                                    item["detail"] = (
                                        "Post-update local digest and known version verified"
                                    )
                                else:
                                    item["detail"] = "Post-update local digest verified"
                    else:
                        if parsed["base"] not in remote_cache:
                            remote_cache[parsed["base"]] = remote_digests(parsed["base"], platform)
                        top, platform_digest, error = remote_cache[parsed["base"]]
                        if error:
                            previous = previous_result_map.get(
                                (str(group["stack_key"]), str(image_ref))
                            )
                            previous_local = sorted(
                                str(value)
                                for value in ((previous or {}).get("local_digests") or [])
                                if str(value)
                            )
                            current_local = sorted(str(value) for value in local if str(value))
                            can_keep_previous = bool(
                                previous
                                and previous.get("status") not in {None, "ERROR", "PINNED"}
                                and previous.get("update_policy") != "digest_pinned"
                                and previous.get("remote_digest")
                                and previous_local == current_local
                            )

                            if can_keep_previous:
                                item["status"] = previous.get("status")
                                item["update_policy"] = previous.get("update_policy")
                                item["remote_digest"] = previous.get("remote_digest")
                                item["remote_digest_short"] = previous.get("remote_digest_short")
                                item["remote_platform_digest"] = previous.get("remote_platform_digest")
                                item["resolved_available_tag"] = previous.get("resolved_available_tag")
                                if previous.get("available_version_resolution"):
                                    item["available_version_resolution"] = dict(previous.get("available_version_resolution") or {})
                                item["newer_tag"] = previous.get("newer_tag")
                                item["newer_tags"] = list(previous.get("newer_tags") or [])
                                item["available_version_tags"] = list(
                                    previous.get("available_version_tags") or []
                                )
                                if previous.get("resolved_installed_tag"):
                                    item["resolved_installed_tag"] = previous.get("resolved_installed_tag")
                                if previous.get("installed_version_resolution"):
                                    item["installed_version_resolution"] = dict(
                                        previous.get("installed_version_resolution") or {}
                                    )
                                short_error = str(error).splitlines()[0][:140]
                                item["scan_warning"] = short_error
                                item["detail"] = (
                                    "Remote check temporarily failed; "
                                    "previous successful result retained: "
                                    + short_error
                                )
                            else:
                                item.update(
                                    status="ERROR",
                                    update_policy="follow_tag",
                                    detail=str(error).splitlines()[0][:180],
                                )
                        else:
                            item["remote_digest"] = top
                            item["remote_digest_short"] = top.replace("sha256:", "")[:12]
                            item["remote_platform_digest"] = platform_digest
                            candidates = {top}
                            if platform_digest:
                                candidates.add(platform_digest)
                            same = (
                                bool(remote_config_same)
                                if remote_config_same is not None
                                else _all_local_digest_groups_match_remote(
                                    local_digest_groups,
                                    candidates,
                                )
                            )
                            version = parse_version(parsed["tag"])
                            if version:
                                item["update_policy"] = "fixed_version"
                                repo_key = parsed["normalized_repo"]
                                if repo_key not in tags_cache:
                                    tags_cache[repo_key] = registry_tags(repo_key)
                                tags, tag_error = tags_cache[repo_key]
                                if not tag_error:
                                    item["newer_tags"] = compatible_newer_tags(parsed["tag"], tags)
                                    item["newer_tag"] = item["newer_tags"][0] if item["newer_tags"] else None

                                # v0.3.128: the normal fixed-version scan must use
                                # the same GitHub supplement as the version picker.
                                # Registry tag-list endpoints can omit a new release
                                # because of pagination/order.  Never trust GitHub
                                # alone, though: verify the exact Docker tag before
                                # advertising it as an installable update.
                                if not item["newer_tag"] and same:
                                    project_url = str(item.get("project_url") or "").strip()
                                    project_provider = str(item.get("project_provider") or "").strip().lower()
                                    if not project_url or project_provider != "github":
                                        inferred_url = github_url_from_image_ref(tracking_image_ref)
                                        if inferred_url and github_repo_from_url(inferred_url):
                                            project_url = inferred_url
                                            project_provider = "github"
                                    if project_url and project_provider == "github":
                                        if project_url not in github_scan_cache:
                                            try:
                                                github_scan_cache[project_url] = github_project_tags(project_url)
                                            except Exception as exc:
                                                github_scan_cache[project_url] = ([], "github_error", str(exc))
                                        github_values, github_source, github_error = github_scan_cache[project_url]
                                        github_values = github_values or []
                                        if github_values:
                                            verified_newer = github_verified_newer_registry_tags(
                                                parsed["tag"],
                                                repo_key,
                                                tags or [],
                                                github_values,
                                                platform=platform,
                                                limit=30,
                                                verify_limit=8,
                                            )
                                            if verified_newer:
                                                item["newer_tags"] = verified_newer
                                                item["newer_tag"] = verified_newer[0]
                                                item["version_source"] = (
                                                    "registry+" + str(github_source or "github")
                                                )
                                        elif github_error:
                                            item["scan_warning"] = (
                                                "GitHub version supplement failed: "
                                                + str(github_error).splitlines()[0][:140]
                                            )

                                if item["newer_tag"]:
                                    item["status"] = "VERSION_UPDATE"
                                    item["detail"] = f"Newer version tag available: {parsed['tag']} -> {item['newer_tag']}"
                                elif same:
                                    item["status"] = "CURRENT"
                                    item["detail"] = "Configured fixed version is current"
                                else:
                                    item["status"] = "IMAGE_UPDATE"
                                    item["detail"] = "Configured tag points to a different remote image"
                            else:
                                item["update_policy"] = "follow_tag"
                                item["status"] = "CURRENT" if same else "IMAGE_UPDATE"
                                if remote_config_same is not None:
                                    item["detail"] = (
                                        "Remote config digest matches local image"
                                        if same
                                        else "Configured tag points to a different remote image"
                                    )
                                else:
                                    item["detail"] = "Remote digest matches local image" if same else "Configured tag points to a different remote image"

                results.append(item)

                checked_percent = 22 + int((deployment_index / max(1, deployment_total)) * 68)
                update_scan_progress(
                    "images",
                    checked_percent,
                    deployment_index,
                    deployment_total,
                )

        # v0.3.119: a targeted post-update scan must not turn into a hidden full
        # version-catalogue scan. Resolve versions only for the freshly scanned
        # app, then merge its verified records back into the previous snapshot.
        update_scan_progress("versions", 93)
        if stack_key:
            fresh_results = list(results)
            enrich_scan_installed_versions(fresh_results, platform)
            enrich_scan_available_versions(fresh_results, platform)
            enrich_follow_policy_targets(fresh_results, platform)

            # Replace the old app completely. Match both the historical
            # stack_key and Compose project so stale pre-update results cannot
            # survive when ZimaOS changed container metadata during recreation.
            results = [
                item
                for item in previous_results
                if (
                    item.get("stack_key") != stack_key
                    and (
                        not target_compose_project
                        or str(item.get("compose_project") or "").strip()
                        != target_compose_project
                    )
                )
            ] + fresh_results
        else:
            # A real full scan intentionally refreshes version information for
            # every app. This is also the one shared verification after an
            # automatic-update batch.
            enrich_scan_installed_versions(results, platform)
            enrich_scan_available_versions(results, platform)
            enrich_follow_policy_targets(results, platform)

        update_scan_progress("results", 94)
        counter = Counter(x["status"] for x in results)
        fixed = sum(1 for x in results if x.get("update_policy") == "fixed_version")
        summary = {
            "CURRENT": counter["CURRENT"],
            "IMAGE_UPDATE": counter["IMAGE_UPDATE"],
            "VERSION_UPDATE": counter["VERSION_UPDATE"],
            "PINNED": counter["PINNED"],
            "LOCAL": counter["LOCAL"],
            "ERROR": counter["ERROR"],
            "FIXED": fixed,
        }
        update_scan_progress("results", 97)
        apps, app_summary = build_apps(results)
        update_scan_progress("results", 99)
        finished_at = utc_now()

        try:
            local_cache_snapshot = collect_current_local_scan_cache_snapshot()
            local_cache_error = None
        except Exception as cache_exc:
            local_cache_snapshot = copy.deepcopy(
                scan_state.get("local_cache_snapshot")
                if isinstance(scan_state.get("local_cache_snapshot"), dict)
                else local_scan_cache_snapshot_from_results(results)
            )
            local_cache_error = str(cache_exc).splitlines()[0][:220]

        final = {
            "state": "idle",
            "scan_scope": "app" if stack_key else "all",
            "scan_stack_key": stack_key,
            "started_at": scan_state.get("started_at"),
            "finished_at": finished_at,
            # Targeted verification must NOT reset the configured 1/6/12/24 h
            # clock. Only a real full scan advances this timestamp.
            "last_full_scan_at": (
                scan_state.get("last_full_scan_at")
                if stack_key
                else finished_at
            ),
            "local_cache_snapshot": local_cache_snapshot,
            "local_cache_error": local_cache_error,
            "progress_step": "done",
            "progress_percent": 100,
            "progress_current": scan_state.get("progress_total", 0),
            "progress_total": scan_state.get("progress_total", 0),
            "platform": platform,
            "results": results,
            "apps": apps,
            "summary": summary,
            "app_summary": app_summary,
        }
    except Exception as exc:
        final = {
            "state": "idle" if stack_key else "error",
            "scan_scope": "app" if stack_key else "all",
            "scan_stack_key": stack_key,
            "started_at": scan_state.get("started_at"),
            "finished_at": utc_now(),
            "progress_step": "error",
            "progress_percent": scan_state.get("progress_percent", 0),
            "progress_current": scan_state.get("progress_current", 0),
            "progress_total": scan_state.get("progress_total", 0),
            "last_full_scan_at": scan_state.get("last_full_scan_at"),
            "local_cache_snapshot": copy.deepcopy(
                scan_state.get("local_cache_snapshot")
                if isinstance(scan_state.get("local_cache_snapshot"), dict)
                else {}
            ),
            "results": scan_state.get("results", []),
            "apps": scan_state.get("apps", []),
            "summary": scan_state.get("summary", {}),
            "app_summary": scan_state.get("app_summary", {}),
            "error": str(exc),
        }

    with scan_lock:
        scan_state = final
        save_json(SCAN_FILE, scan_state)


def active_mutating_action_keys():
    """Return unfinished Docker-changing actions.

    app_update_lock is the primary gate. This progress check is defense in
    depth so a full scan is also refused if a future code path accidentally
    exposes a small unlock window while an action still reports as active.
    """
    with action_progress_lock:
        return sorted(
            str(key)
            for key, record in action_progress.items()
            if isinstance(record, dict)
            and not bool(record.get("finished"))
            and str(record.get("kind") or "").strip().lower()
            in {"update", "uninstall", "restore"}
        )


def has_pending_targeted_app_scan(stack_key=None):
    """Only targeted post-action verifications, not queued full scans."""
    with post_scan_lock:
        if stack_key is None:
            return bool(post_scan_pending)
        return str(stack_key or "").strip() in post_scan_pending


def full_scan_block_reason():
    """Central rule for every normal/full update check.

    A full scan must never start while a Docker-changing action is running or
    while a targeted post-action verification is queued. This applies equally
    to manual scans, interval scans, startup scans and any future full-scan
    caller.
    """
    if app_update_lock.locked():
        return "app-operation-or-scan-active"

    if active_mutating_action_keys():
        return "app-operation-progress-active"

    if has_pending_targeted_app_scan():
        return "targeted-verification-pending"

    with scan_lock:
        if scan_state.get("state") == "scanning":
            return "scan-active"
        thread = scan_thread
        if thread and thread.is_alive():
            return "scan-thread-active"

    return None


def remove_uninstalled_app_from_scan_snapshot(stack_key, compose_project=None):
    """Remove an already-confirmed uninstall without launching a full scan.

    The uninstall endpoint waits until ZimaOS/Docker confirms removal. At that
    point a network/registry-wide update check is unnecessary: filter the old
    app records locally and rebuild counters/cards from the remaining snapshot.
    """
    stack_key = str(stack_key or "").strip()
    compose_project = str(compose_project or "").strip()

    with scan_lock:
        results = [
            item
            for item in (scan_state.get("results") or [])
            if not (
                str(item.get("stack_key") or "").strip() == stack_key
                or (
                    compose_project
                    and str(item.get("compose_project") or "").strip()
                    == compose_project
                )
            )
        ]

        counter = Counter(
            str(item.get("status") or "")
            for item in results
            if isinstance(item, dict)
        )
        fixed = sum(
            1
            for item in results
            if isinstance(item, dict)
            and item.get("update_policy") == "fixed_version"
        )
        summary = {
            "CURRENT": counter["CURRENT"],
            "IMAGE_UPDATE": counter["IMAGE_UPDATE"],
            "VERSION_UPDATE": counter["VERSION_UPDATE"],
            "PINNED": counter["PINNED"],
            "LOCAL": counter["LOCAL"],
            "ERROR": counter["ERROR"],
            "FIXED": fixed,
        }

        apps, app_summary = build_apps(results)

        scan_state["results"] = results
        scan_state["apps"] = apps
        scan_state["summary"] = summary
        scan_state["app_summary"] = app_summary

        cached = scan_state.get("local_cache_snapshot")
        if isinstance(cached, dict):
            cached_stacks = dict(cached.get("stacks") or {})
            cached_stacks.pop(stack_key, None)
            cached["stacks"] = cached_stacks
            cached["generated_at"] = utc_now()
            scan_state["local_cache_snapshot"] = cached

        # Do not touch finished_at / last_full_scan_at: an uninstall is not a
        # full update check and must not reset the configured 1/6/12/24-hour
        # interval.
        save_json(SCAN_FILE, scan_state)

    return True


def _scan_thread_entry(stack_key=None):
    """
    Run one scan while owning the same exclusive operation lock used by Docker
    updates, uninstall and runtime changes.

    The common operation gate prevents scans from overlapping Compose/image updates.
    That could capture a transitional old state and later overwrite the fresh
    result with "Update installieren" again.
    """
    try:
        scan_all(stack_key)
    finally:
        app_update_lock.release()


def _start_scan_with_operation_lock(stack_key=None, *, origin="unknown"):
    global scan_thread

    stack_key = str(stack_key or "").strip() or None

    # Full scans are subject to the strict central gate. A targeted verification
    # is intentionally exempt from the "pending targeted scan" check because it
    # is the operation that consumes that pending marker.
    if stack_key is None:
        if full_scan_block_reason():
            return False

    # Atomic common gate: if an update/uninstall/restore/source switch already
    # owns the operation lock, neither a full nor targeted scan can overlap it.
    if not app_update_lock.acquire(blocking=False):
        return False

    try:
        # Re-check the full-scan-only conditions after acquiring the lock to
        # close the race between the first check and lock acquisition.
        if stack_key is None:
            if active_mutating_action_keys() or has_pending_targeted_app_scan():
                app_update_lock.release()
                return False

        with scan_lock:
            if scan_thread and scan_thread.is_alive():
                app_update_lock.release()
                return False

            if scan_state.get("state") == "scanning":
                app_update_lock.release()
                return False

            if stack_key:
                exists = any(
                    app_item.get("stack_key") == stack_key
                    for app_item in (scan_state.get("apps") or [])
                )
                if not exists:
                    app_update_lock.release()
                    return False

            scan_thread = threading.Thread(
                target=_scan_thread_entry,
                args=(stack_key,),
                name=(
                    "update-monitor-scan-app"
                    if stack_key
                    else f"update-monitor-scan-all-{str(origin or 'unknown')}"
                ),
                daemon=True,
            )
            scan_thread.start()
            return True

    except Exception:
        if app_update_lock.locked():
            app_update_lock.release()
        raise

def start_scan(origin="manual"):
    """Start a full scan only when no Docker action/targeted verification exists."""
    return _start_scan_with_operation_lock(None, origin=origin)

def start_app_scan(stack_key):
    """Start exactly one targeted app verification after the action lock is free."""
    stack_key = str(stack_key or "").strip()
    if not stack_key:
        return False
    return _start_scan_with_operation_lock(
        stack_key,
        origin="targeted-verification",
    )

def pending_app_scan_keys():
    with post_scan_lock:
        keys = sorted(post_scan_pending)
        if post_full_scan_pending:
            keys.insert(0, "*")
        return keys


def has_pending_app_scan(stack_key=None):
    with post_scan_lock:
        # A pending full verification covers every app and therefore blocks all
        # new scan/version/update decisions until it has completed.
        if post_full_scan_pending:
            return True
        if stack_key is None:
            return bool(post_scan_pending)
        return str(stack_key or "").strip() in post_scan_pending


def _scan_result_is_newer_than_request(stack_key, requested_at):
    requested = _parse_utc_timestamp(requested_at)
    if requested is None:
        return False

    with scan_lock:
        finished = _parse_utc_timestamp(scan_state.get("finished_at"))
        if finished is None or finished < requested:
            return False

        scope = str(scan_state.get("scan_scope") or "")
        scanned_key = str(scan_state.get("scan_stack_key") or "").strip()
        if stack_key is None:
            return scope == "all"
        return scope == "all" or (scope == "app" and scanned_key == stack_key)


def _post_update_verified_targets(update_result):
    """Return a small immutable digest snapshot for the post-update app scan."""
    result = update_result if isinstance(update_result, dict) else {}
    raw = result.get("target_verification") or {}
    verified = {}

    if not isinstance(raw, dict):
        return verified

    # Image source switching stores docker_pull_verified() directly, while
    # ordinary app updates may store a mapping keyed by image ref. Normalize
    # both shapes so the targeted post-action scan gets the exact verified
    # digest and never falls back to the stale pre-switch registry source.
    if (
        str(raw.get("image_ref") or "").strip()
        and (
            raw.get("expected_digests")
            or raw.get("local_digests")
        )
    ):
        raw = {
            str(raw.get("image_ref") or "").strip(): dict(raw)
        }

    for image_ref, info in raw.items():
        image_ref = str(image_ref or "").strip()
        if not image_ref or not isinstance(info, dict):
            continue

        digests = {
            str(value or "").strip().lower()
            for value in (info.get("expected_digests") or info.get("local_digests") or [])
            if str(value or "").strip()
        }
        if not digests:
            continue

        parsed = parse_image_ref(image_ref)
        repo = str(parsed.get("normalized_repo") or info.get("repo") or "").strip()
        tag = str(parsed.get("tag") or "").strip()
        key = f"{repo}:{tag}" if repo and tag else image_ref
        version_hint = str(info.get("resolved_version_hint") or "").strip()
        verified[key] = {
            "image_ref": image_ref,
            "repo": repo,
            "tag": tag,
            "digests": sorted(digests),
            "method": str(info.get("method") or "verified-update"),
            "resolved_version_hint": (
                version_hint if version_hint and parse_version(version_hint) else None
            ),
        }

    return verified


def _post_update_target_for_image(verified_targets, image_ref):
    if not isinstance(verified_targets, dict):
        return None
    parsed = parse_image_ref(str(image_ref or "").strip())
    repo = str(parsed.get("normalized_repo") or "").strip()
    tag = str(parsed.get("tag") or "").strip()
    key = f"{repo}:{tag}" if repo and tag else str(image_ref or "").strip()
    value = verified_targets.get(key)
    return value if isinstance(value, dict) else None


def schedule_app_scan(
    stack_key,
    delay=8.0,
    max_wait_seconds=900,
    verification_result=None,
):
    """
    Queue exactly one authoritative post-action scan for an app.

    v0.3.117 could create multiple waiter threads for the same app. After the
    operation lock became free those waiters ran the same targeted scan one
    after another, which looked like an endless update check and could starve
    automatic installation.
    """
    stack_key = str(stack_key or "").strip()
    if not stack_key:
        return False
    if self_update_handoff_owned_by_current_process(stack_key):
        return False

    requested_at = utc_now()
    verified_targets = _post_update_verified_targets(verification_result)

    with post_scan_lock:
        if stack_key in post_scan_pending:
            # One pending authoritative scan is sufficient. If a successful
            # update supplied stronger verification evidence, preserve it.
            if verified_targets:
                current = post_scan_pending.get(stack_key) or {}
                merged = dict(current.get("verified_targets") or {})
                merged.update(verified_targets)
                current["verified_targets"] = merged
                post_scan_pending[stack_key] = current
            return False
        post_scan_pending[stack_key] = {
            "requested_at": requested_at,
            "created_at": requested_at,
            "verified_targets": verified_targets,
        }

    def waiter():
        try:
            time.sleep(max(0.0, float(delay)))
            deadline = time.time() + max(5.0, float(max_wait_seconds))

            while time.time() < deadline:
                # A newer full/targeted scan may already have refreshed this app
                # (for example because the user explicitly started a full scan).
                if _scan_result_is_newer_than_request(stack_key, requested_at):
                    return

                if start_app_scan(stack_key):
                    # Keep the pending marker until the scan thread really ends.
                    # This blocks stale-state automation decisions in between.
                    while True:
                        with scan_lock:
                            thread = scan_thread
                            scanning = scan_state.get("state") == "scanning"
                        if not scanning and (not thread or not thread.is_alive()):
                            break
                        time.sleep(0.25)
                    return

                # If the app disappeared and no scan/update is active, there is
                # nothing left to verify.
                if not app_update_lock.locked():
                    with scan_lock:
                        exists = any(
                            app_item.get("stack_key") == stack_key
                            for app_item in (scan_state.get("apps") or [])
                        )
                    if not exists:
                        return

                time.sleep(1.0)
        finally:
            with post_scan_lock:
                post_scan_pending.pop(stack_key, None)

    thread = threading.Thread(
        target=waiter,
        name="update-monitor-post-update-scan-waiter",
        daemon=True,
    )
    thread.start()
    return True




def _parse_utc_timestamp(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _auto_retry_ready(policy, now_utc, update_signature):
    """
    Successful identical update signatures stay suppressed.
    Failed or interrupted identical signatures are retried after a 15-minute cooldown.
    """
    if str(policy.get("last_auto_signature") or "") != str(update_signature or ""):
        return True

    result = str(policy.get("last_auto_result") or "").strip().lower()
    last_attempt = _parse_utc_timestamp(policy.get("last_auto_attempt_at"))

    if result == "success":
        # A true success should disappear from the next authoritative scan. If
        # the exact same update signature is still present afterwards, the old
        # success record is stale/false-positive (this happened in v0.3.115/116).
        # Do not block that update forever.
        if last_attempt is None:
            return True
        return (
            now_utc - last_attempt
        ).total_seconds() >= AUTO_UPDATE_SUCCESS_REVERIFY_SECONDS

    if result not in {"error", "running"}:
        return True

    if last_attempt is None:
        return True

    return (
        now_utc - last_attempt
    ).total_seconds() >= AUTO_UPDATE_RETRY_COOLDOWN_SECONDS



def _auto_update_signature(app_item):
    """Stable identity for the update currently detected for one app."""
    app_item = app_item or {}
    policy = get_monitor_policy(app_item.get("stack_key"))

    if app_item.get("can_version_update"):
        version_group = app_item.get("version_group") or {}
        if policy.get("mode") == "upgrade":
            target = str(policy.get("target_tag") or "").strip()
        elif policy.get("mode") == "follow":
            target = FOLLOW_POLICY_TAG
        else:
            target = ""

        if target:
            current = ",".join(
                str(value or "").strip()
                for value in (version_group.get("current_tags") or [])
            )
            return f"version:{policy.get('mode')}:{current}->{target}"

    image_parts = []
    for item in app_item.get("items") or []:
        if item.get("status") != "IMAGE_UPDATE":
            continue
        image_ref = str(item.get("image_ref") or "").strip()
        remote = str(item.get("remote_digest") or "").strip()
        if image_ref:
            image_parts.append(f"{image_ref}@{remote or 'remote'}")

    if image_parts:
        return "image:" + "|".join(sorted(image_parts))

    return None


def _auto_update_due(app_item, now_utc=None):
    policy = get_monitor_policy(app_item.get("stack_key"))
    if not policy.get("auto_enabled"):
        return False, None, None

    if policy.get("mode") not in {"upgrade", "follow"}:
        return False, None, None

    if not (app_item.get("can_image_update") or app_item.get("can_version_update")):
        return False, None, None

    try:
        tz = ZoneInfo(policy.get("auto_timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc

    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(tz)
    local_date = local_now.date().isoformat()
    update_signature = _auto_update_signature(app_item)

    if policy.get("auto_immediate"):
        # "Sofort" ignores the complete schedule: both weekday and clock time.
        # The update is started as soon as a scan has detected a new update.
        # The signature prevents the scheduler from reinstalling the exact
        # same detected update every 30 seconds.
        if not update_signature:
            return False, None, None
        if not _auto_retry_ready(policy, now_utc, update_signature):
            return False, None, None
        return True, local_date, update_signature

    if local_now.weekday() not in set(policy.get("auto_days") or []):
        return False, None, None

    hour, minute = [int(x) for x in _normalize_auto_time(policy.get("auto_time")).split(":")]
    scheduled_minutes = hour * 60 + minute
    current_minutes = local_now.hour * 60 + local_now.minute

    # Run only inside a one-hour window after the configured time.
    if current_minutes < scheduled_minutes:
        return False, None, None
    if current_minutes >= scheduled_minutes + 60:
        return False, None, None

    if policy.get("last_auto_local_date") == local_date:
        if not update_signature:
            return False, None, None
        if not _auto_retry_ready(policy, now_utc, update_signature):
            return False, None, None

    return True, local_date, update_signature



def _follow_normalization_signature(app_item):
    app_item = app_item or {}
    policy = get_monitor_policy(app_item.get("stack_key"))
    if str(policy.get("mode") or "") != "follow":
        return None
    if not app_item.get("follow_tag_switch_only"):
        return None

    group = select_primary_version_group(app_item)
    items = list((group or {}).get("items") or [])
    parts = []
    for item in items:
        current_tag = str(_version_item_current_tag(item) or "").strip().lower()
        if current_tag == FOLLOW_POLICY_TAG:
            continue
        image_ref = str(item.get("image_ref") or "").strip()
        digests = ",".join(sorted(
            str(value or "").strip().lower()
            for value in (item.get("local_digests") or [])
            if str(value or "").strip()
        ))
        if image_ref:
            parts.append(f"{image_ref}@{digests or 'local'}")

    if not parts:
        return None
    return "follow-normalize:" + "|".join(sorted(parts)) + "->latest"


def _follow_normalization_due(app_item, now_utc=None):
    signature = _follow_normalization_signature(app_item)
    if not signature:
        return False, None

    policy = get_monitor_policy(app_item.get("stack_key"))
    previous_signature = str(
        policy.get("last_follow_normalize_signature") or ""
    )
    if previous_signature != signature:
        return True, signature

    last_attempt = _parse_utc_timestamp(
        policy.get("last_follow_normalize_attempt_at")
    )
    if last_attempt is None:
        return True, signature

    now_utc = now_utc or datetime.now(timezone.utc)
    age = (now_utc - last_attempt).total_seconds()

    # Both a failure and a suspicious "success but scan still says mismatch"
    # are rate-limited. This prevents a broken Compose stack from generating a
    # normalize -> scan -> normalize -> scan loop every 30 seconds.
    return age >= FOLLOW_NORMALIZE_RETRY_COOLDOWN_SECONDS, signature


def run_follow_tag_normalization(now_utc=None):
    """Perform at most one due configuration-only transition to :latest."""
    if scan_state.get("state") == "scanning" or has_pending_app_scan():
        return False

    with scan_lock:
        apps_snapshot = list(scan_state.get("apps") or [])

    now_utc = now_utc or datetime.now(timezone.utc)

    for app_item in apps_snapshot:
        due, signature = _follow_normalization_due(app_item, now_utc=now_utc)
        if not due:
            continue

        if not app_update_lock.acquire(blocking=False):
            return False

        stack_key = str(app_item.get("stack_key") or "").strip()
        begin_action_progress(stack_key, "update", app_item, automatic=True)
        update_action_progress(stack_key, phase="normalizing")

        try:
            # Record the attempt before mutation so a process restart cannot
            # immediately hammer the same broken normalization every 30 seconds.
            record_follow_normalization_result(
                stack_key,
                signature,
                False,
                "Follow-tag normalization started",
            )

            update_result = perform_version_update(app_item)

            record_follow_normalization_result(
                stack_key,
                signature,
                True,
                None,
            )
            finish_action_progress(stack_key, True)
            schedule_app_scan(
                stack_key,
                verification_result=update_result,
            )
            return True

        except Exception as exc:
            record_follow_normalization_result(
                stack_key,
                signature,
                False,
                str(exc),
            )
            finish_action_progress(stack_key, False, str(exc))
            schedule_app_scan(stack_key)
            return True

        finally:
            app_update_lock.release()

    return False



def run_due_auto_updates(now_utc=None):
    """Install at most one due automatic update, then verify that app first.

    Automatic updates are intentionally serialized as:
      update one app -> targeted verification scan -> next due app.

    The pending targeted scan blocks the scheduler from starting another update
    until the just-updated app has been authoritatively rescanned. This keeps
    automatic updates controlled and prevents a later update from starting on
    stale scan state. Unrelated Docker apps are never rescanned here.
    """
    if scan_state.get("state") == "scanning" or has_pending_app_scan():
        return False

    with scan_lock:
        apps_snapshot = list(scan_state.get("apps") or [])

    now_utc = now_utc or datetime.now(timezone.utc)

    for app_item in apps_snapshot:
        due, local_date, update_signature = _auto_update_due(
            app_item,
            now_utc=now_utc,
        )
        if not due:
            continue

        if not app_update_lock.acquire(blocking=False):
            return False

        stack_key = str(app_item.get("stack_key") or "").strip()
        if not stack_key:
            app_update_lock.release()
            continue

        begin_action_progress(stack_key, "update", app_item, automatic=True)

        # Record the attempt before touching Docker/Compose so a process crash
        # cannot immediately retry the same update on restart. An active run is
        # not a failure; success/error is recorded only when the run finishes.
        with policies_lock:
            current = dict(policies.get(str(stack_key)) or {})
            current["last_auto_attempt_at"] = utc_now()
            current["last_auto_local_date"] = str(local_date)
            if update_signature:
                current["last_auto_signature"] = str(update_signature)
            current["last_auto_result"] = "running"
            current["last_auto_error"] = None
            policies[str(stack_key)] = current
            save_json(POLICY_FILE, policies)

        update_result = None
        backup_result = None
        try:
            auto_policy = get_monitor_policy(stack_key)
            auto_backup_mode = _normalize_backup_mode(auto_policy.get("auto_backup_mode"), "none")
            if auto_backup_mode != "none":
                update_action_progress(stack_key, determinate=False, phase="backup")
                backup_result = create_pre_update_backup(
                    app_item,
                    auto_backup_mode,
                    stack_key=stack_key,
                )
                update_action_progress(stack_key, determinate=False, phase="starting")

            if app_item.get("can_version_update"):
                update_result = perform_version_update(app_item)
            elif app_item.get("can_image_update"):
                update_result = perform_image_update(app_item)
            else:
                raise RuntimeError("No installable update is available")

            if isinstance(update_result, dict) and backup_result:
                update_result["backup"] = backup_result

            record_auto_update_result(
                stack_key,
                local_date,
                True,
                None,
                update_signature=update_signature,
            )
            finish_action_progress(stack_key, True)

        except Exception as exc:
            record_auto_update_result(
                stack_key,
                local_date,
                False,
                str(exc),
                update_signature=update_signature,
            )
            finish_action_progress(stack_key, False, str(exc))

        finally:
            # Queue the authoritative targeted verification BEFORE releasing the
            # common operation gate. The pending marker blocks the scheduler, so
            # no second automatic update can start until this scan has completed.
            schedule_app_scan(
                stack_key,
                verification_result=update_result,
            )
            app_update_lock.release()

        # Exactly one automatic update attempt per scheduler iteration.
        # The next due app is considered only after the targeted scan above ends.
        return True

    return False

def scheduler_iteration(now_utc=None):
    """Run one deterministic automation/scheduling decision."""
    now_utc = now_utc or datetime.now(timezone.utc)

    # Hard bidirectional exclusion:
    # Docker action -> no scheduler scan/update decision.
    # Targeted post-action verification -> no unrelated full scan/update.
    if scan_state.get("state") == "scanning":
        return "busy-scan"
    if app_update_lock.locked() or active_mutating_action_keys():
        return "busy-operation"
    if has_pending_targeted_app_scan():
        return "pending-targeted-verification"
    if has_pending_app_scan():
        return "pending-post-scan"

    # Real automatic installations have priority over a configuration-only
    # :latest normalization. v0.3.117 did the reverse, so one failing
    # normalization could starve every automatic update forever.
    if run_due_auto_updates(now_utc=now_utc):
        return "auto-update"

    if run_follow_tag_normalization(now_utc=now_utc):
        return "follow-normalize"

    interval = int(settings.get("scan_interval_seconds", 0))
    if interval <= 0:
        return "idle"

    last_full = scan_state.get("last_full_scan_at")
    if not last_full:
        return "full-scan" if start_scan(origin="interval") else "idle"

    then = _parse_utc_timestamp(last_full)
    if then is None:
        return "full-scan" if start_scan(origin="interval") else "idle"

    age = (now_utc - then).total_seconds()
    if age >= interval:
        return "full-scan" if start_scan(origin="interval") else "idle"

    return "idle"




def stale_cached_digest_pin_stack_keys():
    """Return cached digest-pin apps that must be reclassified.

    v0.3.161 fixed fresh scans, but its startup migration depended on reading
    literal Compose YAML before it would queue a targeted scan. If that read
    was temporarily unavailable (or old metadata lacked project/service
    fields), the persistent startup cache could keep the old PINNED result
    forever even though the new classifier was correct.

    A cached digest pin is therefore trusted only when it was produced by the
    current classifier AND explicitly proved from literal Compose YAML.
    Everything older/ambiguous is refreshed once, app-by-app.
    """
    with scan_lock:
        cached_results = [
            dict(item)
            for item in (scan_state.get("results") or [])
            if isinstance(item, dict)
            and (
                item.get("status") == "PINNED"
                or item.get("update_policy") == "digest_pinned"
            )
        ]

    affected = set()

    for item in cached_results:
        stack_key = str(item.get("stack_key") or "").strip()
        if not stack_key:
            continue

        schema_ok = (
            int(item.get("pin_classification_schema") or 0)
            >= PIN_CLASSIFICATION_SCHEMA
        )
        proof_ok = item.get("compose_digest_pin_proven") is True

        if not (schema_ok and proof_ok):
            affected.add(stack_key)

    return sorted(affected)


def false_cached_digest_pin_stack_keys():
    """Compatibility alias for older internal callers/tests."""
    return stale_cached_digest_pin_stack_keys()


def _startup_scan_if_still_needed(baseline_finished_at):
    """Compatibility wrapper around the v0.3.152 persistent startup cache."""
    return startup_cache_refresh_if_needed(baseline_finished_at)


def scheduler_loop():
    while True:
        time.sleep(30)
        try:
            scheduler_iteration()
        except Exception:
            pass


@app.on_event("startup")
def startup():
    global scan_state, settings, policies, PROJECT_LINK_CACHE, INSTALLED_VERSION_CACHE, VERSION_CONFIRMATIONS
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    loaded = load_json(SETTINGS_FILE, settings)
    if isinstance(loaded, dict):
        settings.update(loaded)
    settings["backup_retention"] = _normalize_backup_retention(
        settings.get("backup_retention")
    )
    settings["backup_max_per_app"] = _normalize_backup_max_per_app(
        settings.get("backup_max_per_app")
    )
    settings["backup_max_per_app_by_stack"] = _normalize_backup_max_per_app_map(
        settings.get("backup_max_per_app_by_stack")
    )

    try:
        load_backup_encryption_state()
    except Exception:
        pass
    loaded_project_links = load_json(PROJECT_LINK_CACHE_FILE, {})
    PROJECT_LINK_CACHE = loaded_project_links if isinstance(loaded_project_links, dict) else {}
    loaded_installed_versions = load_json(INSTALLED_VERSION_CACHE_FILE, {})
    INSTALLED_VERSION_CACHE = (
        loaded_installed_versions
        if isinstance(loaded_installed_versions, dict)
        else {}
    )
    scan_state = load_json(SCAN_FILE, scan_state)
    if not isinstance(scan_state, dict):
        scan_state = {
            "state": "idle",
            "started_at": None,
            "finished_at": None,
            "last_full_scan_at": None,
            "results": [],
            "apps": [],
            "summary": {},
            "app_summary": {},
        }
    if scan_state.get("state") == "scanning":
        scan_state["state"] = "idle"

    # One-time migration: old versions had only finished_at. Seed the normal
    # interval anchor once; targeted scans from v0.3.152 onward never advance it.
    if not scan_state.get("last_full_scan_at") and scan_state.get("finished_at"):
        scan_state["last_full_scan_at"] = scan_state.get("finished_at")
        save_json(SCAN_FILE, scan_state)
    _migrate_backup_max_per_app_settings()
    try:
        cleanup_expired_backups()
    except Exception:
        pass
    loaded_confirmations = load_json(VERSION_CONFIRMATIONS_FILE, {})
    VERSION_CONFIRMATIONS = loaded_confirmations if isinstance(loaded_confirmations, dict) else {}
    loaded_policies = load_json(POLICY_FILE, {})
    if isinstance(loaded_policies, dict):
        policies = loaded_policies
    refresh_scan_policy_fields()

    false_pin_stacks = stale_cached_digest_pin_stack_keys()

    threading.Thread(target=scheduler_loop, name="update-monitor-scheduler", daemon=True).start()
    threading.Thread(target=message_bus_listener_loop, name="update-monitor-message-bus", daemon=True).start()
    threading.Timer(1.0, reconcile_self_update_after_restart).start()

    for index, stack_key in enumerate(false_pin_stacks):
        schedule_app_scan(
            stack_key,
            delay=0.05 + (index * 0.10),
        )

    if env_bool("UPDATE_MONITOR_SCAN_ON_START", True):
        startup_scan_baseline = str(scan_state.get("finished_at") or "")
        threading.Timer(
            3.0,
            _startup_scan_if_still_needed,
            args=(startup_scan_baseline,),
        ).start()


# Public application branding assets, independent of login.
# Register only these exact filenames; never expose the entire static directory.
WEB_ICON_ASSETS = {'android-chrome-192x192.png': 'image/png', 'apple-touch-icon.png': 'image/png', 'favicon-128x128.png': 'image/png', 'favicon-16x16.png': 'image/png', 'favicon-32x32.png': 'image/png', 'favicon-48x48.png': 'image/png', 'favicon-64x64.png': 'image/png', 'favicon.ico': 'image/x-icon', 'icon-256x256.png': 'image/png', 'update-monitor-master-1024.png': 'image/png', 'update-monitor-source-highres.png': 'image/png', 'web-app-icon-512.png': 'image/png', 'site.webmanifest': 'application/manifest+json'}


def _make_web_icon_handler(filename, media_type):
    def serve_web_icon():
        path = STATIC_DIR / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Website icon not found")
        return FileResponse(path, media_type=media_type,
                            headers={"Cache-Control": "no-cache"})
    return serve_web_icon


for _icon_filename, _icon_media_type in WEB_ICON_ASSETS.items():
    app.add_api_route(
        "/" + _icon_filename,
        _make_web_icon_handler(_icon_filename, _icon_media_type),
        methods=["GET", "HEAD"], include_in_schema=False,
        name="web_icon_" + _icon_filename,
    )


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/icons-v3/{cache_key}", include_in_schema=False)
def app_icon_v3(cache_key: str, request: Request):
    require_auth(request)
    if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
        raise HTTPException(status_code=400, detail="Invalid icon cache key")

    # Fast path: the file already exists locally. This is the normal path once
    # the background warm-up has run. No remote URL is needed in the browser.
    cached = cached_icon_file(cache_key)
    if cached:
        path, media_type = cached
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=604800, immutable"},
        )

    # If the browser beats the background prefetch, fetch once using only the
    # server-side source map populated from ZimaOS/Docker metadata.
    source = icon_source_for_key(cache_key)
    if not source:
        raise HTTPException(status_code=404, detail="Icon source is not known")
    try:
        path, media_type = cached_icon(source, cache_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=604800, immutable"},
    )


@app.get("/api/icons-v2/{cache_key}", include_in_schema=False)
@app.get("/api/icons/{cache_key}", include_in_schema=False)
def app_icon_legacy(cache_key: str, src: str, request: Request):
    require_auth(request)
    if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
        raise HTTPException(status_code=400, detail="Invalid icon cache key")
    try:
        path, media_type = cached_icon(src, cache_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {"authenticated": is_authenticated(request), "username": request.session.get("username") if is_authenticated(request) else None}


@app.post("/api/login")
def login(data: LoginRequest, request: Request):
    key = client_key(request)
    if rate_limited(key):
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")
    if not (secrets.compare_digest(data.username, UPDATE_MONITOR_USERNAME) and secrets.compare_digest(data.password, UPDATE_MONITOR_PASSWORD)):
        record_failure(key)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    with LOGIN_RATE_LIMIT_LOCK:
        login_failures.pop(key, None)
    request.session["authenticated"] = True
    request.session["username"] = UPDATE_MONITOR_USERNAME
    return {"success": True, "username": UPDATE_MONITOR_USERNAME}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"success": True}



def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _cgroup_cpu_usage_seconds():
    # cgroup v2
    raw = _read_text("/sys/fs/cgroup/cpu.stat")
    if raw:
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "usage_usec":
                try:
                    return int(parts[1]) / 1_000_000.0
                except ValueError:
                    pass

    # cgroup v1 fallback
    raw = _read_text("/sys/fs/cgroup/cpuacct/cpuacct.usage")
    if raw:
        try:
            return int(raw) / 1_000_000_000.0
        except ValueError:
            pass
    return None


def _cgroup_memory_values():
    current = _read_text("/sys/fs/cgroup/memory.current")
    limit = _read_text("/sys/fs/cgroup/memory.max")

    if current is None:
        current = _read_text("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if limit is None:
        limit = _read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes")

    try:
        current_value = int(current) if current is not None else None
    except ValueError:
        current_value = None

    if limit in (None, "max"):
        limit_value = None
    else:
        try:
            limit_value = int(limit)
            # cgroup v1 often exposes an enormous sentinel when no real limit exists.
            if limit_value >= (1 << 60):
                limit_value = None
        except ValueError:
            limit_value = None

    return current_value, limit_value


def container_resource_usage(sample_seconds=0.12):
    first = _cgroup_cpu_usage_seconds()
    wall_start = time.perf_counter()
    if first is not None:
        time.sleep(sample_seconds)
    second = _cgroup_cpu_usage_seconds()
    wall_elapsed = time.perf_counter() - wall_start

    cpu_percent = None
    if first is not None and second is not None and wall_elapsed > 0:
        cpu_percent = max(0.0, ((second - first) / wall_elapsed) * 100.0)

    memory_bytes, memory_limit_bytes = _cgroup_memory_values()
    return {
        "cpu_percent": cpu_percent,
        "memory_bytes": memory_bytes,
        "memory_limit_bytes": memory_limit_bytes,
    }

def docker_version():
    rc, out, _ = run(["docker", "version", "--format", "{{.Server.Version}}"], 10)
    return out if rc == 0 else None


def casaos_detected():
    try:
        return CASAOS_URL_FILE.exists() and bool(CASAOS_URL_FILE.read_text().strip())
    except Exception:
        return False


def casaos_base_url():
    if not CASAOS_URL_FILE.exists():
        raise RuntimeError("ZimaOS App Management URL not found")
    value = CASAOS_URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
    if not value.startswith("http://127.0.0.1:") and not value.startswith("http://localhost:"):
        raise RuntimeError("Unexpected ZimaOS App Management address")
    return value


def casaos_compose_yaml(compose_project):
    app_id = urllib.parse.quote(str(compose_project), safe="")
    url = f"{casaos_base_url()}/v2/app_management/compose/{app_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/yaml",
            "User-Agent": f"Update-Monitor/{VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            if response.status != 200 or not body.strip():
                raise RuntimeError(f"ZimaOS compose read failed (HTTP {response.status})")
            return body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        detail = body.strip().replace("\n", " ")[:240]
        raise RuntimeError(f"ZimaOS compose read failed (HTTP {exc.code}){': ' + detail if detail else ''}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ZimaOS App Management not reachable: {exc.reason}") from exc


def find_scanned_app(stack_key):
    with scan_lock:
        apps = list(scan_state.get("apps") or [])
    for app_item in apps:
        if app_item.get("stack_key") == stack_key:
            return app_item
    return None



def casaos_request(path, method="GET", body=None, content_type=None, accept="application/json", timeout=30):
    url = casaos_base_url() + path
    headers = {
        "Accept": accept,
        "User-Agent": f"Update-Monitor/{VERSION}",
    }
    if content_type:
        headers["Content-Type"] = content_type

    data = body
    if isinstance(body, str):
        data = body.encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        detail = raw.strip().replace("\n", " ")[:500]
        raise RuntimeError(
            f"ZimaOS App Management HTTP {exc.code}"
            + (f": {detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"ZimaOS App Management not reachable: {exc.reason}"
        ) from exc


def casaos_apply_compose(compose_project, yaml_text, dry_run):
    app_id = urllib.parse.quote(str(compose_project), safe="")
    query = urllib.parse.urlencode({
        "dry_run": "true" if dry_run else "false",
        # Existing app ports are already in use by this same app. Do not reject
        # an update just because the current app owns them.
        "check_port_conflict": "false",
        # ZimaOS merges query parameters into MessageBus event properties.
        # Supplying app:name lets app:install-progress map back to this card.
        "app:name": str(compose_project),
    })
    status, raw = casaos_request(
        f"/v2/app_management/compose/{app_id}?{query}",
        method="PUT",
        body=yaml_text,
        content_type="application/yaml",
        accept="application/json",
        timeout=45 if dry_run else 90,
    )
    if status != 200:
        raise RuntimeError(f"ZimaOS compose apply failed (HTTP {status})")
    return raw


def casaos_uninstall_compose(compose_project, delete_config_folder=False):
    app_id = urllib.parse.quote(str(compose_project), safe="")
    query = urllib.parse.urlencode({
        "delete_config_folder": "true" if delete_config_folder else "false",
        "app:name": str(compose_project),
    })
    status, raw = casaos_request(
        f"/v2/app_management/compose/{app_id}?{query}",
        method="DELETE",
        accept="application/json",
        timeout=45,
    )
    if status != 200:
        raise RuntimeError(
            f"ZimaOS app uninstall failed (HTTP {status})"
        )
    return raw


def casaos_compose_project_exists(compose_project):
    """
    Return True/False when the ZimaOS Compose list can be read.
    Return None if the App Management list is temporarily unavailable.
    """
    try:
        status, raw = casaos_request(
            "/v2/app_management/compose",
            method="GET",
            accept="application/json",
            timeout=20,
        )
        if status != 200:
            return None
        payload = json.loads(raw or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return None
        return str(compose_project) in data
    except Exception:
        return None


def wait_for_uninstall_complete(compose_project, timeout=180, progress_stack_key=None):
    """
    ZimaOS uninstalls Compose apps asynchronously.

    Success is reported only after:
    - no current Docker container belongs to the Compose project, and
    - ZimaOS no longer reports the Compose app (or the list is temporarily
      unavailable while Docker already confirms removal).

    Two consecutive "gone" samples prevent a short Docker transition from
    being mistaken for a completed uninstall.
    """
    compose_project = str(compose_project or "").strip()
    if not compose_project:
        raise RuntimeError("ZimaOS Compose app ID is missing")

    deadline = time.time() + timeout
    gone_samples = 0
    last_zimaos_exists = None
    last_container_count = None

    while time.time() < deadline:
        zimaos_exists = casaos_compose_project_exists(compose_project)
        try:
            rows = [
                row
                for row in docker_runtime_snapshot()
                if str(row.get("compose_project") or "").strip()
                == compose_project
            ]
            container_count = len(rows)
        except Exception:
            container_count = None

        last_zimaos_exists = zimaos_exists
        last_container_count = container_count

        # ZimaOS does not publish a native uninstall percentage. Measure what
        # actually happened instead: how many project containers disappeared.
        if progress_stack_key and container_count is not None:
            progress_record = action_progress_snapshot(progress_stack_key)
            if progress_record:
                initial = max(
                    1,
                    int(progress_record.get("initial_container_count") or 1),
                )
                removed = max(0, initial - int(container_count))
                measured = min(90, int(round((removed / initial) * 90)))
                update_action_progress(
                    progress_stack_key,
                    measured,
                    determinate=True,
                    phase="removing",
                )

        docker_gone = container_count == 0
        zimaos_gone = zimaos_exists is not True

        if docker_gone and zimaos_gone:
            gone_samples += 1
            if gone_samples >= 2:
                return {
                    "zimaos_registered": False if zimaos_exists is False else None,
                    "remaining_containers": 0,
                }
        else:
            gone_samples = 0

        time.sleep(2)

    raise RuntimeError(
        "ZimaOS accepted the uninstall, but the app did not disappear "
        f"within {timeout} seconds "
        f"(zimaos_registered={last_zimaos_exists}, "
        f"remaining_containers={last_container_count})"
    )


def remove_uninstalled_app_policy(stack_key):
    stack_key = str(stack_key or "").strip()
    if not stack_key:
        return
    with policies_lock:
        if stack_key in policies:
            policies.pop(stack_key, None)
            save_json(POLICY_FILE, policies)



def casaos_set_app_status(compose_project, status_name):
    if status_name not in {"start", "restart", "stop"}:
        raise RuntimeError("Unsupported ZimaOS app status")
    app_id = urllib.parse.quote(str(compose_project), safe="")
    status, raw = casaos_request(
        f"/v2/app_management/compose/{app_id}/status",
        method="PUT",
        body=json.dumps(status_name),
        content_type="application/json",
        accept="application/json",
        timeout=30,
    )
    if status != 200:
        raise RuntimeError(
            f"ZimaOS app status change failed (HTTP {status})"
        )
    return raw


def casaos_recreate_container(container_id, app_name=None):
    container_id = urllib.parse.quote(str(container_id), safe="")
    query_values = {
        "pull": "true",
        "force": "true",
    }
    if app_name:
        query_values["app:name"] = str(app_name)
    query = urllib.parse.urlencode(query_values)
    status, raw = casaos_request(
        f"/v2/app_management/container/{container_id}?{query}",
        method="PATCH",
        accept="application/json",
        timeout=900,
    )
    if status != 200:
        raise RuntimeError(
            f"ZimaOS container recreate failed (HTTP {status})"
        )
    return raw


def docker_container_image_id(container_name):
    rc, out, err = run(
        ["docker", "inspect", "--format", "{{.Image}}", container_name],
        timeout=15,
    )
    if rc != 0:
        return None
    value = out.strip()
    return value or None


def docker_image_repo_digests(image_ref_or_id, normalized_repo=None):
    value = str(image_ref_or_id or "").strip()
    if not value:
        return set()

    rc, out, _ = run(
        ["docker", "image", "inspect", value, "--format", "{{json .RepoDigests}}"],
        timeout=20,
    )
    if rc != 0 or not out:
        return set()

    try:
        repo_digests = json.loads(out)
    except Exception:
        return set()

    wanted_repo = str(normalized_repo or "").strip()
    result = set()

    for repo_digest in repo_digests or []:
        repo, digest = parse_repo_digest(str(repo_digest or "").strip())
        if not digest:
            continue
        if wanted_repo and repo != wanted_repo:
            continue
        result.add(str(digest).strip().lower())

    return result


def docker_container_repo_digests(container_name, normalized_repo=None):
    image_id = docker_container_image_id(container_name)
    if not image_id:
        return set()
    return docker_image_repo_digests(image_id, normalized_repo)




def docker_pull_verified(image_ref, platform=None, expected_digests=None):
    """Pull the exact target and verify the image actually fetched by Docker.

    The successful Docker pull is the authoritative install-time snapshot of a
    mutable tag.  ``expected_digests`` is only prior scan evidence: a tag may
    legitimately move between scan and installation, so a mismatch is recorded
    for diagnostics but must not turn a successfully pulled image into a false
    update failure.  The freshly pulled local RepoDigest becomes the exact
    runtime-verification target for the rest of this operation.
    """
    parsed = parse_image_ref(str(image_ref or "").strip())
    repo_key = str(parsed.get("normalized_repo") or "").strip()
    if not repo_key:
        raise RuntimeError(f"Could not parse registry repository for {image_ref}")

    expected = {
        str(value or "").strip().lower()
        for value in (expected_digests or [])
        if str(value or "").strip()
    }

    last_message = None
    for attempt in range(2):
        rc, out, err = run(["docker", "pull", str(image_ref)], timeout=900)
        if rc == 0:
            break
        last_message = err or out or f"Exit {rc}"
        if attempt == 0 and _remote_digest_error_is_transient(last_message):
            time.sleep(2.0)
            continue
        break
    else:
        rc = 1

    if rc != 0:
        message = str(last_message or err or out or f"Exit {rc}")
        first = message.splitlines()[0][:300]
        if _remote_digest_error_is_transient(message):
            raise RuntimeError(
                f"Registry temporarily unavailable or rate limited while pulling "
                f"{image_ref}: {first}"
            )
        raise RuntimeError(f"Docker pull failed for {image_ref}: {first}")

    local = {
        str(value or "").strip().lower()
        for value in docker_image_repo_digests(image_ref, repo_key)
        if str(value or "").strip()
    }
    if not local:
        raise RuntimeError(
            f"Docker pulled {image_ref}, but no local RepoDigest was available "
            "for verification"
        )

    scan_digest_changed = bool(expected and not local.intersection(expected))

    # The bytes Docker actually pulled are now the only valid target for this
    # installation.  Keeping an older scan digest as the expected runtime target
    # caused false failures when a tag moved between scan and update.
    verified = local
    return {
        "image_ref": str(image_ref),
        "repo": repo_key,
        "expected_digests": sorted(verified),
        "local_digests": sorted(local),
        "scan_expected_digests": sorted(expected),
        "scan_digest_changed": scan_digest_changed,
    }


def docker_tag_same_image(old_ref, new_ref):
    rc, out, err = run(
        ["docker", "tag", str(old_ref), str(new_ref)],
        timeout=30,
    )
    if rc != 0:
        message = err or out or f"Exit {rc}"
        raise RuntimeError(
            f"Could not tag existing image {old_ref} as {new_ref}: "
            f"{str(message).splitlines()[0][:300]}"
        )
    return True


def container_matches_target_digest(container_name, image_ref, expected_digests):
    parsed = parse_image_ref(str(image_ref or "").strip())
    repo_key = str(parsed.get("normalized_repo") or "").strip()

    expected = {
        str(value or "").strip().lower()
        for value in (expected_digests or [])
        if str(value or "").strip()
    }

    local = docker_container_repo_digests(container_name, repo_key)
    return bool(expected and local.intersection(expected)), local


def wait_for_container_target_digest(
    container_name,
    image_ref,
    expected_digests,
    timeout=600,
):
    """
    Update success requires both the target Config.Image string and a real
    RepoDigest match against the registry target.
    """
    deadline = time.time() + timeout
    last_ref = None
    last_local = set()

    while time.time() < deadline:
        last_ref = docker_container_config_image(container_name)
        matched, last_local = container_matches_target_digest(
            container_name,
            image_ref,
            expected_digests,
        )

        if last_ref == image_ref and matched:
            return {
                "container": container_name,
                "image_ref": last_ref,
                "image_id": docker_container_image_id(container_name),
                "local_digests": sorted(last_local),
            }

        time.sleep(2)

    raise RuntimeError(
        f"Container {container_name} did not reach the verified target image "
        f"{image_ref} within {timeout} seconds "
        f"(config_image={last_ref}, local_digests={sorted(last_local)}, "
        f"expected_digests={sorted(expected_digests)})"
    )


def docker_container_name(container_id):
    rc, out, _ = run([
        "docker",
        "inspect",
        "-f",
        "{{.Name}}",
        str(container_id),
    ])
    if rc != 0 or not out.strip():
        return None
    return out.strip().lstrip("/")


def docker_container_id(container_name):
    rc, out, err = run(
        ["docker", "inspect", "--format", "{{.Id}}", container_name],
        timeout=15,
    )
    if rc != 0:
        return None
    value = out.strip()
    return value or None


def docker_stop_container(container_name):
    rc, out, err = run(
        ["docker", "stop", container_name],
        timeout=60,
    )
    if rc != 0:
        detail = (err or out or f"exit {rc}")[-800:]
        raise RuntimeError(
            f"Could not restore stopped state for {container_name}: {detail}"
        )



def docker_start_container(container_name):
    rc, out, err = run(
        ["docker", "start", container_name],
        timeout=60,
    )
    if rc != 0:
        detail = (err or out or f"exit {rc}")[-800:]
        raise RuntimeError(
            f"Could not start container {container_name}: {detail}"
        )


def docker_runtime_snapshot():
    """
    Return the CURRENT Docker runtime inventory in one lightweight call.

    Compose project/service labels are included so live runtime state is not
    tied to container names cached by the last update scan.
    """
    rc, out, err = run(
        [
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--format",
            '{{.Names}}\t{{.State}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}\t{{.Label "com.docker.compose.container-number"}}\t{{.Label "com.docker.compose.depends_on"}}\t{{.CreatedAt}}',
        ],
        timeout=15,
    )
    if rc != 0:
        raise RuntimeError(err or out or "docker ps failed")

    rows = []
    for raw_line in out.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        while len(parts) < 7:
            parts.append("")

        name = str(parts[0] or "").strip()
        state = str(parts[1] or "").strip().lower() or "unknown"
        compose_project = str(parts[2] or "").strip()
        compose_service = str(parts[3] or "").strip()
        compose_container_number = str(parts[4] or "").strip()
        compose_depends_on = str(parts[5] or "").strip()
        created_at = str(parts[6] or "").strip()

        if name:
            rows.append({
                "name": name,
                "state": state,
                "compose_project": compose_project,
                "compose_service": compose_service,
                "compose_container_number": compose_container_number,
                "compose_depends_on": compose_depends_on,
                "created_at": created_at,
            })

    return select_current_runtime_rows(rows)


def resolve_app_runtime_rows(app_item, snapshot=None):
    """
    Resolve the CURRENT Docker containers for an app.

    Compose apps are matched by the live com.docker.compose.project label.
    This survives container recreation/replacement. Local apps fall back to
    the last known container names.
    """
    snapshot = snapshot if snapshot is not None else docker_runtime_snapshot()

    compose_project = str(app_item.get("compose_project") or "").strip()
    if compose_project:
        rows = select_current_runtime_rows([
            row
            for row in snapshot
            if str(row.get("compose_project") or "").strip() == compose_project
        ])
        if rows:
            return rows

    cached_names = {
        str(container.get("name") or "").strip()
        for container in (app_item.get("containers") or [])
        if str(container.get("name") or "").strip()
    }

    return [
        row
        for row in snapshot
        if str(row.get("name") or "").strip() in cached_names
    ]


def _runtime_states_from_rows(rows):
    return {
        str(row.get("name") or ""): str(row.get("state") or "unknown")
        for row in rows or []
        if str(row.get("name") or "")
    }


def _runtime_action_reached(action, states, allow_empty_stop=False):
    values = [str(value or "missing").lower() for value in states.values()]

    if action == "stop" and not values and allow_empty_stop:
        return True

    if not values:
        return False

    if action == "stop":
        return all(
            value in {"exited", "dead", "created", "missing"}
            for value in values
        )

    return (
        any(value == "running" for value in values)
        and not any(value in {"dead", "restarting"} for value in values)
    )


def current_app_runtime_states(app_item):
    rows = resolve_app_runtime_rows(app_item)
    return rows, _runtime_states_from_rows(rows)


def wait_for_runtime_action(app_item, action, timeout=18):
    deadline = time.time() + timeout
    last_rows, last_states = current_app_runtime_states(app_item)
    stable_hits = 0

    while time.time() < deadline:
        reached = _runtime_action_reached(
            action,
            last_states,
            allow_empty_stop=bool(app_item.get("compose_project")),
        )

        if reached:
            stable_hits += 1
            # Require three consecutive real Docker samples. This prevents
            # transient ZimaOS start/stop phases from being reported as final.
            if stable_hits >= 3:
                return True, last_rows, last_states
        else:
            stable_hits = 0

        time.sleep(1)
        last_rows, last_states = current_app_runtime_states(app_item)

    reached = _runtime_action_reached(
        action,
        last_states,
        allow_empty_stop=bool(app_item.get("compose_project")),
    )
    return reached and stable_hits >= 2, last_rows, last_states


def _parse_compose_dependencies(raw_value):
    dependencies = []
    for entry in str(raw_value or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        service = entry.split(":", 1)[0].strip()
        if service and service not in dependencies:
            dependencies.append(service)
    return dependencies


def _compose_service_order(rows):
    """Return dependency-first service order from Docker Compose labels."""
    services = []
    dependencies = {}

    for row in rows or []:
        service = str(row.get("compose_service") or "").strip()
        if not service:
            continue

        if service not in services:
            services.append(service)

        dependencies.setdefault(service, [])
        for dep in _parse_compose_dependencies(row.get("compose_depends_on")):
            if dep not in dependencies[service]:
                dependencies[service].append(dep)

    ordered = []
    visiting = set()
    visited = set()

    def visit(service):
        if service in visited:
            return
        if service in visiting:
            return

        visiting.add(service)
        for dep in dependencies.get(service, []):
            if dep in dependencies:
                visit(dep)
        visiting.remove(service)

        visited.add(service)
        ordered.append(service)

    for service in services:
        visit(service)

    return ordered


def direct_docker_runtime_action(rows, action):
    """
    Start/stop current containers directly through Docker.

    No ZimaOS App Management status endpoint is called here, so no global
    ZimaOS app-progress event is emitted by this manual runtime action.
    """
    action = str(action or "").strip().lower()
    if action not in {"start", "stop"}:
        raise RuntimeError("Unsupported container action")

    rows = list(rows or [])
    if not rows:
        if action == "stop":
            return []
        raise RuntimeError("No current Docker containers found for this app")

    service_order = _compose_service_order(rows)
    if action == "stop":
        service_order = list(reversed(service_order))

    rows_by_service = {}
    ungrouped = []

    for row in rows:
        service = str(row.get("compose_service") or "").strip()
        if service:
            rows_by_service.setdefault(service, []).append(row)
        else:
            ungrouped.append(row)

    ordered_rows = []
    for service in service_order:
        ordered_rows.extend(rows_by_service.get(service, []))
    ordered_rows.extend(ungrouped)

    changed = []
    errors = []

    for row in ordered_rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue

        try:
            current = docker_container_state(name)

            if action == "start":
                if current != "running":
                    docker_start_container(name)
                    changed.append(name)
            else:
                if current not in {"exited", "dead", "created", None}:
                    docker_stop_container(name)
                    changed.append(name)

        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors))

    return changed


def perform_runtime_action(app_item, action):
    action = str(action or "").strip().lower()
    if action not in {"start", "stop"}:
        raise RuntimeError("Unsupported container action")

    compose_project = str(app_item.get("compose_project") or "").strip()
    initial_rows, initial_states = current_app_runtime_states(app_item)

    if compose_project:
        # ZimaOS-managed app:
        # Use the official app status API so ZimaOS itself publishes the
        # AppStart/AppStop events and stays synchronized with Docker.
        casaos_set_app_status(compose_project, action)

        reached, final_rows, final_states = wait_for_runtime_action(
            app_item,
            action,
            timeout=30,
        )
        if not reached:
            raise RuntimeError(
                f"ZimaOS accepted {action}, but Docker did not reach the "
                f"requested state. Current states: {final_states}"
            )

        return {
            "engine": "zimaos-app-status-synchronized",
            "action": action,
            "compose_project": compose_project,
            "initial_states": initial_states,
            "final_states": final_states,
        }

    # Local/non-ZimaOS app: direct Docker remains the only applicable path.
    changed = direct_docker_runtime_action(initial_rows, action)

    reached, final_rows, final_states = wait_for_runtime_action(
        app_item,
        action,
        timeout=18,
    )
    if not reached:
        raise RuntimeError(
            f"{action.capitalize()} did not reach the requested Docker state. "
            f"Current states: {final_states}"
        )

    return {
        "engine": "docker-runtime-direct-local",
        "action": action,
        "compose_project": None,
        "containers": changed,
        "initial_states": initial_states,
        "final_states": final_states,
    }



def aggregate_runtime_state(states):
    normalized = [str(state or "unknown").strip().lower() for state in states]
    total = len(normalized)
    running_count = sum(1 for state in normalized if state == "running")

    if any(state == "restarting" for state in normalized):
        runtime_state = "restarting"
    elif any(state in {"dead", "removing"} for state in normalized):
        runtime_state = "error"
    elif total == 0:
        runtime_state = "unknown"
    elif running_count == total:
        runtime_state = "running"
    elif running_count == 0 and all(
        state in {"exited", "created", "paused", "unknown"} for state in normalized
    ):
        runtime_state = "stopped"
    elif running_count == 0:
        runtime_state = "stopped"
    else:
        runtime_state = "mixed"

    return runtime_state, running_count


_runtime_missing_lock = threading.Lock()
_runtime_missing_since = {}
_RUNTIME_MISSING_PRUNE_SECONDS = 9.0


def _runtime_missing_prune_blocked(stack_key):
    """Do not remove a card while Update Monitor itself is changing/scanning it."""
    key = str(stack_key or "").strip()
    if app_update_lock.locked():
        return True

    with action_progress_lock:
        record = action_progress.get(key)
        if isinstance(record, dict) and not bool(record.get("finished")):
            return True

    with post_scan_lock:
        if key and key in post_scan_pending:
            return True

    with scan_lock:
        if scan_state.get("state") == "scanning":
            return True

    return False


def _runtime_missing_is_confirmed(stack_key, missing):
    """Require a short continuous Docker absence before treating an app as removed."""
    key = str(stack_key or "").strip()
    if not key:
        return False

    now = time.monotonic()
    with _runtime_missing_lock:
        if not missing:
            _runtime_missing_since.pop(key, None)
            return False

        first_seen = _runtime_missing_since.setdefault(key, now)
        return (now - first_seen) >= _RUNTIME_MISSING_PRUNE_SECONDS


def live_app_runtime_status():
    """
    Lightweight live runtime refresh.

    One local Docker call is used for every app. No registry checks and no
    update scan are performed. If a previously scanned app has no Docker
    containers at all anymore, keep it briefly as stopped to survive normal
    recreate windows. After a short continuous absence, remove the stale card
    from the local scan snapshot. A genuinely stopped container remains in
    ``docker ps -a`` and is therefore never mistaken for a removed app.
    """
    snapshot = docker_runtime_snapshot()

    with scan_lock:
        apps_snapshot = list(scan_state.get("apps") or [])

    # One no-stream Docker stats call feeds CPU/RAM for every running app card.
    resource_names = [
        str(row.get("name") or "").strip()
        for row in snapshot
        if str(row.get("state") or "").strip().lower() == "running"
        and str(row.get("name") or "").strip()
    ]
    resource_stats = _docker_stats_snapshot(resource_names)

    result = []
    removed_stack_keys = []
    cached_stack_keys = set()

    for app_item in apps_snapshot:
        stack_key = str(app_item.get("stack_key") or "").strip()
        if stack_key:
            cached_stack_keys.add(stack_key)

        rows = resolve_app_runtime_rows(app_item, snapshot=snapshot)
        cached_by_name = {
            str(container.get("name") or "").strip(): container
            for container in (app_item.get("containers") or [])
            if isinstance(container, dict) and str(container.get("name") or "").strip()
        }
        live_containers = []
        for row in rows:
            live = dict(row)
            cached = cached_by_name.get(str(row.get("name") or "").strip()) or {}
            # A cached successful one-shot remains completed only while it is still exited.
            if str(row.get("state") or "").strip().lower() == "exited" and _container_completed_one_shot(cached):
                live["exit_code"] = 0
                live["restart_policy"] = "on-failure"
            live_containers.append(live)
        runtime_state, running_count, completed_count, active_container_count = (
            _aggregate_app_runtime(live_containers)
        )

        cached_count = len(app_item.get("containers") or [])
        current_count = len(rows)
        missing = bool(cached_count > 0 and current_count == 0)

        if missing and not _runtime_missing_prune_blocked(stack_key):
            if _runtime_missing_is_confirmed(stack_key, True):
                removed_stack_keys.append(stack_key)
                continue
        else:
            _runtime_missing_is_confirmed(stack_key, False)

        if missing:
            runtime_state = "stopped"
            running_count = 0
            completed_count = 0
            active_container_count = cached_count
            display_count = cached_count
        else:
            display_count = current_count if current_count > 0 else cached_count

        resource_cpu = 0.0
        resource_memory = 0.0
        resource_seen = False
        resource_usage_parts = []
        for row in rows:
            container_name = str(row.get("name") or "").strip()
            stat = resource_stats.get(container_name) or {}
            if not stat:
                continue
            cpu_match = re.search(r"-?\d+(?:\.\d+)?", str(stat.get("cpu_percent") or ""))
            memory_match = re.search(r"-?\d+(?:\.\d+)?", str(stat.get("memory_percent") or ""))
            if cpu_match:
                resource_cpu += float(cpu_match.group(0))
                resource_seen = True
            if memory_match:
                resource_memory += float(memory_match.group(0))
                resource_seen = True
            memory_usage = str(stat.get("memory_usage") or "").strip()
            if memory_usage:
                resource_seen = True
                resource_usage_parts.append(
                    f"{container_name}: {memory_usage}" if len(rows) > 1 else memory_usage
                )

        result.append({
            "stack_key": app_item.get("stack_key"),
            "runtime_state": runtime_state,
            "running_count": running_count,
            "completed_count": completed_count,
            "active_container_count": active_container_count,
            "container_count": display_count,
            "cpu_percent": round(resource_cpu, 2) if resource_seen else None,
            "memory_percent": round(resource_memory, 2) if resource_seen else None,
            "memory_usage": " · ".join(resource_usage_parts) if resource_usage_parts else None,
            "self_protected": is_update_monitor_self_app(app_item),
        })

    if removed_stack_keys:
        _prune_removed_stacks_from_scan_snapshot(removed_stack_keys)
        with _runtime_missing_lock:
            for key in removed_stack_keys:
                _runtime_missing_since.pop(key, None)

    with _runtime_missing_lock:
        for key in list(_runtime_missing_since):
            if key not in cached_stack_keys:
                _runtime_missing_since.pop(key, None)

    return result, removed_stack_keys

def docker_container_state(container_name):
    rc, out, err = run(
        ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
        timeout=15,
    )
    if rc != 0:
        return None
    value = out.strip()
    return value or None


def docker_container_runtime_snapshot(container_name):
    """Read the runtime facts needed to reject a boot loop after source switch."""
    rc, out, err = run(
        ["docker", "inspect", str(container_name)],
        timeout=15,
    )
    if rc != 0:
        return None

    try:
        rows = json.loads(out or "[]")
    except Exception as exc:
        raise RuntimeError(
            f"Could not parse Docker runtime state for {container_name}"
        ) from exc

    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None

    row = rows[0]
    state = row.get("State") or {}
    health = state.get("Health") or {}

    try:
        restart_count = int(row.get("RestartCount") or 0)
    except Exception:
        restart_count = 0

    config = row.get("Config") or {}
    healthcheck = config.get("Healthcheck") or {}
    health_log = health.get("Log") or []

    compact_health_log = []
    for entry in health_log[-3:]:
        if not isinstance(entry, dict):
            continue
        output = str(entry.get("Output") or "").strip()
        if len(output) > 500:
            output = output[:497] + "..."
        compact_health_log.append({
            "start": entry.get("Start"),
            "end": entry.get("End"),
            "exit_code": entry.get("ExitCode"),
            "output": output,
        })

    return {
        "status": str(state.get("Status") or "").strip().lower(),
        "running": bool(state.get("Running")),
        "restarting": bool(state.get("Restarting")),
        "exit_code": state.get("ExitCode"),
        "error": str(state.get("Error") or "").strip(),
        "health": str(health.get("Status") or "").strip().lower() or None,
        "health_failing_streak": int(health.get("FailingStreak") or 0),
        "health_log": compact_health_log,
        "healthcheck": {
            "test": healthcheck.get("Test"),
            "interval": healthcheck.get("Interval"),
            "timeout": healthcheck.get("Timeout"),
            "start_period": healthcheck.get("StartPeriod"),
            "retries": healthcheck.get("Retries"),
        } if healthcheck else None,
        "restart_count": max(0, restart_count),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "container_id": str(row.get("Id") or "").strip() or None,
        "container_name": str(row.get("Name") or "").lstrip("/") or None,
        "config_image": str(config.get("Image") or "").strip() or None,
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "restart_policy": (
            ((row.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
            or None
        ),
        "labels": {
            str(key): str(value)
            for key, value in (config.get("Labels") or {}).items()
            if str(key).startswith("com.centurylinklabs.watchtower")
        },
    }



def _image_source_healthcheck_text(snapshot):
    healthcheck = (snapshot or {}).get("healthcheck") or {}
    test = healthcheck.get("test")
    if isinstance(test, list):
        text = " ".join(str(value or "").strip() for value in test if str(value or "").strip())
    else:
        text = str(test or "").strip()
    return text[:320] or "-"


def _image_source_last_health_log(snapshot):
    logs = (snapshot or {}).get("health_log") or []
    if not logs:
        return {"exit_code": None, "output": "-"}
    last = logs[-1] if isinstance(logs[-1], dict) else {}
    output = str(last.get("output") or "").strip().replace("\n", " | ")
    if len(output) > 380:
        output = output[:377] + "..."
    return {
        "exit_code": last.get("exit_code"),
        "output": output or "-",
    }



def _image_source_health_failure_evidence(snapshot):
    """Return whether Docker has actual failed-healthcheck evidence."""
    snapshot = snapshot or {}

    try:
        failing_streak = int(snapshot.get("health_failing_streak") or 0)
    except Exception:
        failing_streak = 0

    failed_logs = []
    for entry in snapshot.get("health_log") or []:
        if not isinstance(entry, dict):
            continue
        try:
            exit_code = int(entry.get("exit_code"))
        except Exception:
            exit_code = None
        if exit_code not in {None, 0}:
            failed_logs.append(entry)

    return {
        "has_failure": bool(failing_streak > 0 or failed_logs),
        "failing_streak": max(0, failing_streak),
        "failed_logs": failed_logs,
    }


def _image_source_health_timeout_seconds(snapshot, default=10):
    healthcheck = (snapshot or {}).get("healthcheck") or {}
    raw = healthcheck.get("timeout")
    try:
        # Docker inspect exposes health timings in nanoseconds.
        seconds = float(raw) / 1_000_000_000.0
    except Exception:
        seconds = float(default)

    if seconds <= 0:
        seconds = float(default)

    return max(2, min(30, int(round(seconds)) + 2))


def _image_source_run_healthcheck_probe(container_name, snapshot):
    """Run the container's configured Docker HEALTHCHECK once.

    This is used only when Docker reports `unhealthy` but provides no failing
    streak and no failed health log. In that contradictory state, the literal
    status word alone is not sufficient evidence for a destructive rollback.
    """
    healthcheck = (snapshot or {}).get("healthcheck") or {}
    test = healthcheck.get("test")

    if not isinstance(test, list) or not test:
        return {
            "available": False,
            "reason": "no-healthcheck-command",
        }

    kind = str(test[0] or "").strip().upper()
    args = [str(value) for value in test[1:]]

    if kind == "NONE":
        return {
            "available": False,
            "reason": "healthcheck-disabled",
        }

    if kind == "CMD":
        if not args:
            return {
                "available": False,
                "reason": "empty-healthcheck-command",
            }
        command = ["docker", "exec", str(container_name)] + args
    elif kind == "CMD-SHELL":
        shell_command = args[0] if args else ""
        if not shell_command.strip():
            return {
                "available": False,
                "reason": "empty-healthcheck-shell-command",
            }
        command = [
            "docker",
            "exec",
            str(container_name),
            "/bin/sh",
            "-c",
            shell_command,
        ]
    else:
        return {
            "available": False,
            "reason": f"unsupported-healthcheck-kind:{kind or '-'}",
        }

    timeout = _image_source_health_timeout_seconds(snapshot)
    rc, out, err = run(command, timeout=timeout)

    output = "\n".join(
        value.strip()
        for value in [str(out or ""), str(err or "")]
        if value.strip()
    )
    if len(output) > 500:
        output = output[:497] + "..."

    return {
        "available": True,
        "ok": rc == 0,
        "exit_code": rc,
        "output": output or "-",
        "command": _image_source_healthcheck_text(snapshot),
        "timeout": timeout,
    }



def _image_source_health_failure_message(
    container_name,
    original_snapshot,
    new_snapshot,
    manual_probe=None,
):
    original_snapshot = original_snapshot or {}
    new_snapshot = new_snapshot or {}
    last_log = _image_source_last_health_log(new_snapshot)
    manual_probe = manual_probe or {}

    probe_text = ""
    if manual_probe.get("available"):
        probe_text = (
            f", manual_health_exit={manual_probe.get('exit_code')}, "
            f"manual_health_output={manual_probe.get('output') or '-'}"
        )

    return (
        f"New image source failed its Docker health check: {container_name} "
        f"(original_health={original_snapshot.get('health') or 'none'}, "
        f"new_health={new_snapshot.get('health') or 'none'}, "
        f"failing_streak={int(new_snapshot.get('health_failing_streak') or 0)}, "
        f"healthcheck={_image_source_healthcheck_text(new_snapshot)}, "
        f"last_health_exit={last_log.get('exit_code')}, "
        f"last_health_output={last_log.get('output')}"
        f"{probe_text})"
    )




def docker_container_recent_logs(container_name, tail=40):
    """Read a bounded log tail before rollback removes/recreates the container."""
    rc, out, err = run(
        [
            "docker",
            "logs",
            "--tail",
            str(max(1, min(100, int(tail or 40)))),
            str(container_name),
        ],
        timeout=15,
    )

    combined = "\n".join(
        value.strip()
        for value in [str(out or ""), str(err or "")]
        if value.strip()
    )
    if len(combined) > 1400:
        combined = combined[-1400:]

    return {
        "ok": rc == 0,
        "output": combined or "-",
    }


def _image_source_compact_command(snapshot):
    snapshot = snapshot or {}
    entrypoint = snapshot.get("entrypoint")
    cmd = snapshot.get("cmd")

    def normalize(value):
        if isinstance(value, list):
            return " ".join(
                str(item or "").strip()
                for item in value
                if str(item or "").strip()
            )
        return str(value or "").strip()

    combined = " ".join(
        value
        for value in [normalize(entrypoint), normalize(cmd)]
        if value
    )
    return combined[:360] or "-"


def _image_source_watchtower_chain(snapshot):
    labels = (snapshot or {}).get("labels") or {}
    return str(
        labels.get("com.centurylinklabs.watchtower.container-chain")
        or ""
    ).strip()


def _image_source_exit_failure_message(
    container_name,
    expected_runtime_snapshot,
    new_snapshot,
):
    expected_runtime_snapshot = expected_runtime_snapshot or {}
    new_snapshot = new_snapshot or {}
    logs = docker_container_recent_logs(container_name, tail=40)
    log_text = str(logs.get("output") or "-").replace("\n", " | ")

    # Watchtower intentionally exits predecessor/parent instances during its
    # own self-update lineage handling. Detect the exact upstream log wording
    # so the next error explains the real reason instead of only "exited".
    lowered = log_text.lower()
    watchtower_self_update_guard = (
        "detected invalid restart of old watchtower container" in lowered
        or "recovered orphaned watchtower container" in lowered
        or "watchtower-old-" in str(new_snapshot.get("container_name") or "").lower()
    )

    chain = _image_source_watchtower_chain(new_snapshot)
    old_chain = _image_source_watchtower_chain(expected_runtime_snapshot)

    if len(log_text) > 900:
        log_text = log_text[-900:]

    return (
        f"New image source is not stable: {container_name} entered exited state "
        f"(original_state=running, "
        f"exit_code={new_snapshot.get('exit_code')}, "
        f"docker_error={new_snapshot.get('error') or '-'}, "
        f"restart_policy={new_snapshot.get('restart_policy') or '-'}, "
        f"command={_image_source_compact_command(new_snapshot)}, "
        f"watchtower_self_update_guard="
        f"{'true' if watchtower_self_update_guard else 'false'}, "
        f"container_chain={chain or '-'}, "
        f"original_container_chain={old_chain or '-'}, "
        f"logs={log_text or '-'})"
    )



def wait_for_image_source_runtime_stable(
    container_name,
    timeout=120,
    stable_seconds=20,
    baseline_restart_count=0,
    expected_state="running",
    expected_exit_code=None,
    expected_runtime_snapshot=None,
    unhealthy_grace_seconds=30,
):
    """Verify post-switch runtime relative to the container's original state.

    Long-running services that were running before the switch must remain
    running. Containers that were intentionally stopped/exited before the
    switch are allowed to return to the same non-running state. This avoids
    falsely rejecting one-shot/job containers while still detecting a real
    crash of a previously running service.
    """
    deadline = time.time() + max(5, int(timeout))
    stable_since = None
    unhealthy_since = None
    last_health_probe_at = None
    last_health_probe = None
    last = None

    expected_runtime_snapshot = dict(expected_runtime_snapshot or {})
    original_health = str(
        expected_runtime_snapshot.get("health") or ""
    ).strip().lower() or None
    unhealthy_grace_seconds = max(5, int(unhealthy_grace_seconds or 30))

    expected_state = str(expected_state or "running").strip().lower()
    baseline_restart_count = max(0, int(baseline_restart_count or 0))

    try:
        expected_exit_code_normalized = (
            None
            if expected_exit_code is None
            else int(expected_exit_code)
        )
    except Exception:
        expected_exit_code_normalized = None

    while time.time() < deadline:
        last = docker_container_runtime_snapshot(container_name)

        if not last:
            stable_since = None
            time.sleep(2)
            continue

        status = str(last.get("status") or "").strip().lower()
        restart_count = int(last.get("restart_count") or 0)
        health = last.get("health")

        if bool(last.get("restarting")) or status == "restarting":
            raise RuntimeError(
                f"New image source is restarting: {container_name} "
                f"(expected_state={expected_state})"
            )

        # Reject only NEW restarts caused by the source switch. Historical
        # RestartCount values that already existed before the switch are fine.
        if restart_count > baseline_restart_count:
            raise RuntimeError(
                f"New image source is restarting: {container_name} has "
                f"RestartCount={restart_count} "
                f"(baseline={baseline_restart_count}, "
                f"expected_state={expected_state})"
            )

        if health == "unhealthy":
            # Do not blame a source switch for a health state that already
            # existed before the switch. Runtime/restart/exit checks below
            # still remain active.
            if original_health == "unhealthy":
                unhealthy_since = None
            else:
                if unhealthy_since is None:
                    unhealthy_since = time.time()

                evidence = _image_source_health_failure_evidence(last)

                # Docker can briefly expose the contradictory combination
                #   Status=unhealthy + FailingStreak=0 + empty Health.Log
                # during/recently after container recreation.  That is not a
                # proven failed healthcheck. Run the configured HEALTHCHECK
                # ourselves once (and periodically if needed) before deciding
                # to roll back a correctly pulled/recreated container.
                if not evidence.get("has_failure") and bool(last.get("running")):
                    probe_due = (
                        last_health_probe_at is None
                        or time.time() - last_health_probe_at >= 10
                    )
                    if probe_due:
                        last_health_probe = _image_source_run_healthcheck_probe(
                            container_name,
                            last,
                        )
                        last_health_probe_at = time.time()

                    if (
                        isinstance(last_health_probe, dict)
                        and last_health_probe.get("available")
                        and last_health_probe.get("ok")
                    ):
                        # Manual execution of the container's own configured
                        # HEALTHCHECK passed. Treat the native status word as
                        # stale/inconclusive for this verification cycle while
                        # all restart/exit/runtime checks remain enforced.
                        health = "healthy-manual"
                        unhealthy_since = None
                    else:
                        if (
                            time.time() - unhealthy_since
                            >= unhealthy_grace_seconds
                        ):
                            # If the manual probe actually failed, that is now
                            # real evidence. If it was unavailable, keep the
                            # error explicit rather than claiming a Docker log
                            # failure that never existed.
                            if (
                                isinstance(last_health_probe, dict)
                                and last_health_probe.get("available")
                                and not last_health_probe.get("ok")
                            ):
                                raise RuntimeError(
                                    _image_source_health_failure_message(
                                        container_name,
                                        expected_runtime_snapshot,
                                        last,
                                        manual_probe=last_health_probe,
                                    )
                                )
                            raise RuntimeError(
                                "New image source health state could not be "
                                f"verified: {container_name} reports unhealthy "
                                "without Docker failure evidence and its "
                                "configured healthcheck could not be executed "
                                f"reliably (healthcheck="
                                f"{_image_source_healthcheck_text(last)})"
                            )

                        stable_since = None
                        time.sleep(2)
                        continue
                elif evidence.get("has_failure"):
                    if (
                        time.time() - unhealthy_since
                        >= unhealthy_grace_seconds
                    ):
                        raise RuntimeError(
                            _image_source_health_failure_message(
                                container_name,
                                expected_runtime_snapshot,
                                last,
                                manual_probe=last_health_probe,
                            )
                        )
                    stable_since = None
                    time.sleep(2)
                    continue
        else:
            unhealthy_since = None
            last_health_probe = None
            last_health_probe_at = None

        # A container that was running before the switch is a long-running
        # service for this operation. Exited/dead is therefore a real failure.
        if expected_state == "running":
            if status == "exited":
                raise RuntimeError(
                    _image_source_exit_failure_message(
                        container_name,
                        expected_runtime_snapshot,
                        last,
                    )
                )

            if status in {"dead", "removing"}:
                raise RuntimeError(
                    f"New image source is not stable: {container_name} entered "
                    f"{status} state (original_state=running, "
                    f"exit_code={last.get('exit_code')}, "
                    f"docker_error={last.get('error') or '-'})"
                )

            if not bool(last.get("running")) or status != "running":
                stable_since = None
                time.sleep(2)
                continue

            if stable_since is None:
                stable_since = time.time()

            if time.time() - stable_since >= max(5, int(stable_seconds)):
                result = dict(last)
                result["expected_state"] = expected_state
                return result

            time.sleep(2)
            continue

        # Intentionally non-running containers/jobs:
        # - if they are briefly running after recreation, observe them rather
        #   than treating that as a problem;
        # - if they return to their original stopped/exited state, accept that
        #   once the state remains stable for a short period.
        if status in {"dead", "removing"} and expected_state != status:
            raise RuntimeError(
                f"New image source entered unexpected {status} state: "
                f"{container_name} (original_state={expected_state})"
            )

        if status == "exited":
            try:
                current_exit_code = int(last.get("exit_code"))
            except Exception:
                current_exit_code = None

            # Do not turn a previously clean one-shot container into a failing
            # one and silently call the source switch successful.
            if (
                expected_state == "exited"
                and expected_exit_code_normalized == 0
                and current_exit_code not in {None, 0}
            ):
                raise RuntimeError(
                    f"New image source exited with code {current_exit_code}: "
                    f"{container_name} (previous_exit_code=0)"
                )

        acceptable_non_running = {
            "exited": {"exited"},
            "created": {"created", "exited"},
            "dead": {"dead"},
        }.get(expected_state, {expected_state})

        if status in acceptable_non_running:
            if stable_since is None:
                stable_since = time.time()

            # Non-running job state only needs a short observation window to
            # prove Docker is not immediately restarting it.
            stopped_stable_seconds = min(
                max(5, int(stable_seconds)),
                8,
            )
            if time.time() - stable_since >= stopped_stable_seconds:
                result = dict(last)
                result["expected_state"] = expected_state
                return result

            time.sleep(2)
            continue

        # A previously stopped job may be running briefly because Compose
        # recreated it. Let the existing final-state restoration code stop it
        # if it remains running successfully.
        if status == "running" and bool(last.get("running")):
            if stable_since is None:
                stable_since = time.time()

            if time.time() - stable_since >= max(5, int(stable_seconds)):
                result = dict(last)
                result["expected_state"] = expected_state
                result["temporarily_running"] = True
                return result

            time.sleep(2)
            continue

        stable_since = None
        time.sleep(2)

    raise RuntimeError(
        f"New image source did not reach a stable runtime state within "
        f"{timeout} seconds: {container_name} "
        f"(expected_state={expected_state}, last_state={last or {}})"
    )




def image_ref_matches_target(current_ref, target_ref):
    current = parse_image_ref(str(current_ref or "").strip())
    target = parse_image_ref(str(target_ref or "").strip())
    if not current or not target:
        return False
    return (
        str(current.get("normalized_repo") or "").strip().lower()
        == str(target.get("normalized_repo") or "").strip().lower()
        and str(current.get("tag") or "latest").strip()
        == str(target.get("tag") or "latest").strip()
    )


def wait_for_image_source_target_runtime(
    container_name,
    image_ref,
    expected_digests,
    timeout=90,
):
    """Confirm real image bits for a source switch without requiring ref text.

    The authoritative source is already verified in ZimaOS Compose. Here we
    verify only that the running container uses the exact registry target digest.
    """
    expected = {
        str(value or "").strip().lower()
        for value in (expected_digests or [])
        if str(value or "").strip()
    }
    if not expected:
        raise RuntimeError(f"No verified target digest supplied for {image_ref}")

    deadline = time.time() + max(10, int(timeout))
    last = {}

    while time.time() < deadline:
        container_id = docker_container_id(container_name)
        config_image = docker_container_config_image(container_name)
        image_id = docker_container_image_id(container_name)
        matched, local_digests = container_matches_target_digest(
            container_name,
            image_ref,
            expected,
        )

        last = {
            "container_id": container_id,
            "image_id": image_id,
            "config_image": config_image,
            "local_digests": sorted(local_digests),
            "config_ref_matches": image_ref_matches_target(config_image, image_ref),
        }

        if container_id and matched:
            return last

        time.sleep(2)

    raise RuntimeError(
        f"Container {container_name} did not reach the verified target digest "
        f"for {image_ref} within {timeout} seconds "
        f"(last={last}, expected_digests={sorted(expected)})"
    )


def wait_for_container_recreate_target_digest(
    container_name,
    old_container_id,
    image_ref,
    expected_digests,
    timeout=600,
):
    """
    Wait until ZimaOS has really recreated the container AND the image used by
    that container matches the registry target digest.

    Container-ID/image-ID changes alone are not accepted as update success.
    """
    expected = {
        str(value or "").strip().lower()
        for value in (expected_digests or [])
        if str(value or "").strip()
    }
    if not expected:
        raise RuntimeError(f"No verified target digest supplied for {image_ref}")

    deadline = time.time() + timeout
    last_container_id = old_container_id
    last_image_id = None
    last_config_image = None
    last_digests = set()

    while time.time() < deadline:
        current_container_id = docker_container_id(container_name)
        current_image_id = docker_container_image_id(container_name)
        current_config_image = docker_container_config_image(container_name)

        if current_container_id:
            last_container_id = current_container_id
        if current_image_id:
            last_image_id = current_image_id
        if current_config_image:
            last_config_image = current_config_image

        matched, local_digests = container_matches_target_digest(
            container_name,
            image_ref,
            expected,
        )
        if local_digests:
            last_digests = set(local_digests)

        if (
            current_container_id
            and current_container_id != old_container_id
            and image_ref_matches_target(current_config_image, image_ref)
            and matched
        ):
            return {
                "container_id": current_container_id,
                "image_id": current_image_id,
                "config_image": current_config_image,
                "local_digests": sorted(local_digests),
            }

        time.sleep(2)

    raise RuntimeError(
        f"ZimaOS recreated {container_name}, but it did not reach the verified "
        f"target digest for {image_ref} within {timeout} seconds "
        f"(container_changed={last_container_id != old_container_id}, "
        f"image_id={last_image_id}, config_image={last_config_image}, "
        f"local_digests={sorted(last_digests)}, "
        f"expected_digests={sorted(expected)})"
    )





def replace_compose_image_ref(yaml_text, old_ref, new_ref):
    """
    Backward-compatible exact image replacement.
    Used only as a fallback when no Compose service can be resolved.
    """
    pattern = re.compile(
        r'^(?P<prefix>\s*image\s*:\s*)(?P<quote>["\']?)'
        + re.escape(str(old_ref))
        + r'(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        re.MULTILINE,
    )

    def repl(match):
        quote = match.group("quote") or ""
        return (
            match.group("prefix")
            + quote
            + str(new_ref)
            + quote
            + match.group("suffix")
        )

    updated, count = pattern.subn(repl, yaml_text)
    return updated, count


def _yaml_key_matches(line, key):
    """
    Match a simple YAML mapping key while allowing quoted service names.
    Returns the indentation width or None.
    """
    match = re.match(
        r'^(?P<indent>[ \t]*)(?P<quote>["\']?)'
        + re.escape(str(key))
        + r'(?P=quote)\s*:\s*(?:#.*)?$',
        line.rstrip("\r\n"),
    )
    if not match:
        return None
    return len(match.group("indent").expandtabs(4))


def replace_compose_service_images(yaml_text, service_names, new_ref):
    """
    Replace the `image:` value only inside the requested Compose service blocks.

    This deliberately does not depend on the current image text. It therefore
    also works when ZimaOS stores the service image via an environment
    interpolation such as `${IMAGE:-repo/image:tag}`.

    Only the service's `image:` scalar is rewritten; environment values and
    unrelated Compose content are left untouched.
    """
    services = []
    for value in service_names or []:
        value = str(value or "").strip()
        if value and value not in services:
            services.append(value)

    if not services:
        return yaml_text, 0, []

    lines = yaml_text.splitlines(keepends=True)
    replacements = 0
    changed_services = []

    # Locate top-level `services:`.
    services_line = None
    services_indent = None
    for i, line in enumerate(lines):
        indent = _yaml_key_matches(line, "services")
        if indent is not None:
            services_line = i
            services_indent = indent
            break

    if services_line is None:
        return yaml_text, 0, []

    for service_name in services:
        service_start = None
        service_indent = None

        for i in range(services_line + 1, len(lines)):
            raw = lines[i]
            stripped = raw.strip()

            if not stripped or stripped.startswith("#"):
                continue

            leading = len(raw) - len(raw.lstrip(" \t"))
            indent_width = len(raw[:leading].expandtabs(4))

            # Leaving the services mapping.
            if indent_width <= services_indent:
                break

            matched_indent = _yaml_key_matches(raw, service_name)
            if matched_indent is not None and matched_indent > services_indent:
                service_start = i
                service_indent = matched_indent
                break

        if service_start is None:
            continue

        service_end = len(lines)
        for j in range(service_start + 1, len(lines)):
            raw = lines[j]
            stripped = raw.strip()

            if not stripped or stripped.startswith("#"):
                continue

            leading = len(raw) - len(raw.lstrip(" \t"))
            indent_width = len(raw[:leading].expandtabs(4))

            if indent_width <= service_indent:
                service_end = j
                break

        image_line = None
        for j in range(service_start + 1, service_end):
            raw = lines[j]
            match = re.match(
                r'^(?P<prefix>[ \t]*image[ \t]*:[ \t]*)'
                r'(?P<value>.*?)'
                r'(?P<newline>\r?\n?)$',
                raw,
            )
            if not match:
                continue

            leading = len(raw) - len(raw.lstrip(" \t"))
            indent_width = len(raw[:leading].expandtabs(4))
            if indent_width <= service_indent:
                continue

            image_line = j

            # Preserve a plain trailing YAML comment when present.
            value = match.group("value")
            comment = ""
            comment_match = re.match(r'^(.*?)([ \t]+#.*)$', value)
            if comment_match:
                comment = comment_match.group(2)

            lines[j] = (
                match.group("prefix")
                + str(new_ref)
                + comment
                + match.group("newline")
            )
            replacements += 1
            changed_services.append(service_name)
            break

        if image_line is None:
            continue

    return "".join(lines), replacements, changed_services


def docker_container_config_image(container_name):
    rc, out, err = run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container_name],
        timeout=15,
    )
    if rc != 0:
        return None
    return out.strip() or None



def wait_for_image_source_compose_activation(
    compose_project,
    target_container_names,
    old_container_ids,
    new_ref,
    expected_digests,
    timeout=180,
    recreate_after=20,
    stack_key=None,
):
    """Verify a source switch by Compose source + real target digest.

    Config.Image is NOT authoritative for a cross-registry switch. Docker may
    keep the old textual repository reference when Docker Hub and GHCR resolve
    to the same image bits. If ZimaOS persisted the requested Compose source and
    the running container already uses the verified target digest, the switch is
    successful without a pointless recreation.
    """
    deadline = time.time() + max(20, int(timeout))
    recreate_deadline = time.time() + max(4, int(recreate_after))
    recreate_sent = False
    last_images = {}
    last_digest_state = {}
    compose_saved = False

    target_names = [
        str(value or "").strip()
        for value in (target_container_names or [])
        if str(value or "").strip()
    ]
    original_ids = {
        str(name): str(value or "").strip()
        for name, value in (old_container_ids or {}).items()
        if str(name or "").strip() and str(value or "").strip()
    }

    expected = {
        str(value or "").strip().lower()
        for value in (expected_digests or [])
        if str(value or "").strip()
    }
    if not expected:
        raise RuntimeError("No verified target digest was supplied for source activation")

    while time.time() < deadline:
        try:
            compose_text = casaos_compose_yaml(compose_project)
            compose_saved = str(new_ref) in compose_text
        except Exception:
            compose_saved = False

        last_images = {}
        last_digest_state = {}
        all_target_digests = bool(target_names)

        for name in target_names:
            current_ref = docker_container_config_image(name)
            last_images[name] = current_ref

            matched, local_digests = container_matches_target_digest(
                name,
                new_ref,
                expected,
            )
            last_digest_state[name] = {
                "matched": bool(matched),
                "local_digests": sorted(local_digests),
                "container_id": docker_container_id(name),
            }
            if not matched:
                all_target_digests = False

        # This is the important cross-registry case:
        # Compose says GHCR and the actual running image bits equal the verified
        # GHCR digest. The old Config.Image text is only a harmless alias lag.
        if compose_saved and all_target_digests:
            config_refs_match = all(
                image_ref_matches_target(last_images.get(name), new_ref)
                for name in target_names
            )
            return {
                "images": dict(last_images),
                "digests": copy.deepcopy(last_digest_state),
                "forced_recreate": recreate_sent,
                "config_ref_lag": not config_refs_match,
                "activation_mode": (
                    "compose-ref-digest"
                    if config_refs_match
                    else "compose-digest-equivalent"
                ),
            }

        # Compose has the new source but actual bits are still old. Only then is
        # a recreation useful.
        if (
            compose_saved
            and not all_target_digests
            and not recreate_sent
            and time.time() >= recreate_deadline
        ):
            if stack_key:
                update_action_progress(
                    stack_key,
                    65,
                    determinate=True,
                    phase="force_recreate",
                )

            for name in target_names:
                container_id = original_ids.get(name) or docker_container_id(name)
                if not container_id:
                    raise RuntimeError(
                        f"Could not resolve container ID for forced source recreation: {name}"
                    )
                casaos_recreate_container(container_id, compose_project)

            recreate_sent = True

        if (
            not compose_saved
            and not recreate_sent
            and time.time() >= recreate_deadline + 10
        ):
            raise RuntimeError(
                "ZimaOS accepted the image source request, but did not persist "
                f"the new Compose image within {int(recreate_after) + 10} seconds. "
                f"(expected {new_ref}, observed {last_images})"
            )

        time.sleep(2)

    raise RuntimeError(
        "ZimaOS stored the image source change, but the selected container "
        f"did not reach the verified target digest within {timeout} seconds. "
        f"(expected {new_ref}, observed {last_images}, "
        f"digests={last_digest_state}, forced_recreate={recreate_sent}, "
        f"compose_saved={compose_saved})"
    )


def wait_for_version_compose_update(
    compose_project,
    target_container_names,
    new_ref,
    timeout=300,
):
    deadline = time.time() + timeout
    last_images = {}

    while time.time() < deadline:
        try:
            compose_text = casaos_compose_yaml(compose_project)
            compose_saved = new_ref in compose_text
        except Exception:
            compose_saved = False

        # Resolve CURRENT project containers after Compose recreation.
        rc, ids_raw, _ = run([
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
        ])
        current_ids = [
            value for value in ids_raw.splitlines() if value.strip()
        ] if rc == 0 else []

        current_refs = {}
        for container_id in current_ids:
            name = docker_container_name(container_id) or container_id[:12]
            current_ref = docker_container_config_image(container_id)
            current_refs[name] = current_ref

        # Fallback during a very short recreation gap.
        if not current_refs:
            for name in target_container_names:
                current_refs[name] = docker_container_config_image(name)

        last_images = current_refs
        target_ready = bool(current_refs) and new_ref in set(current_refs.values())

        if compose_saved and target_ready:
            return last_images

        time.sleep(2)

    raise RuntimeError(
        "ZimaOS accepted the version change, but the new Compose image "
        f"did not become active within {timeout} seconds. "
        "No destructive recovery action was executed. "
        f"(expected {new_ref}, observed {last_images})"
    )


def wait_for_version_group_compose_update(
    compose_project,
    expected_by_container,
    timeout=300,
):
    """
    Wait until every service in one version family reached its own expected
    image reference. This is the multi-image counterpart of
    wait_for_version_compose_update().
    """
    expected_by_container = {
        str(name): str(ref)
        for name, ref in (expected_by_container or {}).items()
        if str(name).strip() and str(ref).strip()
    }
    if not expected_by_container:
        raise RuntimeError("No target containers were supplied for the version group")

    expected_refs = set(expected_by_container.values())
    deadline = time.time() + timeout
    last_images = {}

    while time.time() < deadline:
        try:
            compose_text = casaos_compose_yaml(compose_project)
            compose_saved = all(ref in compose_text for ref in expected_refs)
        except Exception:
            compose_saved = False

        current_refs = {}
        for container_name in expected_by_container:
            current_ref = docker_container_config_image(container_name)
            if current_ref:
                current_refs[container_name] = current_ref

        last_images = current_refs
        target_ready = (
            len(current_refs) == len(expected_by_container)
            and all(
                current_refs.get(name) == expected_ref
                for name, expected_ref in expected_by_container.items()
            )
        )

        if compose_saved and target_ready:
            return last_images

        time.sleep(2)

    raise RuntimeError(
        "ZimaOS accepted the version-group change, but not every Compose image "
        f"became active within {timeout} seconds. "
        "No destructive recovery action was executed. "
        f"(expected={expected_by_container}, observed={last_images})"
    )


def _backup_stack_dir_name(stack_key, compose_project=None, app_name=None):
    """Return a human-readable backup folder name.

    ZimaOS Compose project names are unique and are preferred so the host-side
    backup tree stays readable (for example: backups/plex/...).
    Legacy/non-Compose entries keep a short stack-key hash to avoid collisions.
    """
    project = str(compose_project or "").strip()
    if project:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", project).strip("-._")[:64]
        if safe:
            return safe

    display = str(app_name or "").strip()
    if display:
        safe_display = re.sub(r"[^A-Za-z0-9._-]+", "-", display).strip("-._")[:48]
        if safe_display:
            digest = hashlib.sha256(
                str(stack_key or display).encode("utf-8", "replace")
            ).hexdigest()[:8]
            return f"{safe_display}-{digest}"

    raw = str(stack_key or "app").strip() or "app"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")[:48] or "app"
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:10]
    return f"{safe}-{digest}"



def _b64e(value):
    return base64.b64encode(bytes(value)).decode("ascii")


def _b64d(value):
    return base64.b64decode(str(value or "").encode("ascii"), validate=True)


def _backup_encryption_config():
    value = load_json(BACKUP_ENCRYPTION_CONFIG_FILE, {})
    return value if isinstance(value, dict) else {}


def _backup_password_key(password, salt, *, n=32768, r=8, p=1):
    password = str(password or "")
    if len(password) < 8:
        raise RuntimeError("Backup encryption password must contain at least 8 characters")
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes(salt),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=32,
        maxmem=128 * 1024 * 1024,
    )


def _backup_auto_unlock_key():
    return hashlib.sha256(
        BACKUP_AUTO_UNLOCK_AAD + b"\0" + SESSION_SECRET.encode("utf-8")
    ).digest()


def _write_backup_encryption_config(config):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _write_backup_json(BACKUP_ENCRYPTION_CONFIG_FILE, config)


def _write_backup_auto_unlock(master_key):
    nonce = os.urandom(12)
    encrypted = AESGCM(_backup_auto_unlock_key()).encrypt(
        nonce,
        bytes(master_key),
        BACKUP_AUTO_UNLOCK_AAD,
    )
    _write_backup_json(
        BACKUP_AUTO_UNLOCK_FILE,
        {
            "schema": "update-monitor-backup-autounlock-v1",
            "nonce": _b64e(nonce),
            "wrapped_key": _b64e(encrypted),
        },
    )


def _try_auto_unlock_backup_key():
    global BACKUP_MASTER_KEY
    config = _backup_encryption_config()
    if not config.get("configured"):
        BACKUP_MASTER_KEY = None
        return False

    record = load_json(BACKUP_AUTO_UNLOCK_FILE, {})
    if not isinstance(record, dict):
        BACKUP_MASTER_KEY = None
        return False

    try:
        master = AESGCM(_backup_auto_unlock_key()).decrypt(
            _b64d(record.get("nonce")),
            _b64d(record.get("wrapped_key")),
            BACKUP_AUTO_UNLOCK_AAD,
        )
        if len(master) != 32:
            raise ValueError("Invalid key length")
    except Exception:
        BACKUP_MASTER_KEY = None
        return False

    BACKUP_MASTER_KEY = bytes(master)
    return True


def load_backup_encryption_state():
    """Load encryption configuration and, when possible, unlock it automatically.

    The password itself is never stored.  A cache-local auto-unlock envelope is
    tied to UPDATE_MONITOR_SESSION_SECRET so unattended automatic backups keep
    working after a normal container restart.  If Update Monitor is reinstalled
    and that cache is gone, the password can unlock the master key again from
    the metadata stored beside the external backup directory.
    """
    global BACKUP_MASTER_KEY
    with BACKUP_ENCRYPTION_LOCK:
        BACKUP_MASTER_KEY = None
        _try_auto_unlock_backup_key()


def backup_encryption_public_status():
    config = _backup_encryption_config()
    configured = bool(config.get("configured"))
    enabled = bool(config.get("enabled")) and configured
    with BACKUP_ENCRYPTION_LOCK:
        unlocked = BACKUP_MASTER_KEY is not None

    return {
        "configured": configured,
        "enabled": enabled,
        "locked": bool(enabled and not unlocked),
        "unlocked": bool(unlocked),
        "algorithm": "AES-256-GCM" if configured else None,
        "password_stored": False,
        "auto_unlock_available": bool(BACKUP_AUTO_UNLOCK_FILE.is_file()),
    }


def _unwrap_backup_master_key_with_password(password):
    config = _backup_encryption_config()
    if not config.get("configured"):
        raise RuntimeError("Backup encryption is not configured")

    kdf = config.get("kdf") if isinstance(config.get("kdf"), dict) else {}
    try:
        salt = _b64d(kdf.get("salt"))
        password_key = _backup_password_key(
            password,
            salt,
            n=int(kdf.get("n") or 32768),
            r=int(kdf.get("r") or 8),
            p=int(kdf.get("p") or 1),
        )
        master = AESGCM(password_key).decrypt(
            _b64d(config.get("nonce")),
            _b64d(config.get("wrapped_key")),
            BACKUP_ENCRYPTION_AAD,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Backup encryption password is incorrect") from exc

    if len(master) != 32:
        raise RuntimeError("Backup encryption key is invalid")
    return bytes(master)


def configure_backup_encryption(enabled, password=None):
    global BACKUP_MASTER_KEY
    enabled = bool(enabled)
    password = str(password or "")

    with BACKUP_ENCRYPTION_LOCK:
        config = _backup_encryption_config()

        if not enabled:
            if config.get("configured"):
                config["enabled"] = False
                config["updated_at"] = utc_now()
                _write_backup_encryption_config(config)
            return backup_encryption_public_status()

        if not config.get("configured"):
            if len(password) < 8:
                raise RuntimeError("Enter a backup password with at least 8 characters")
            master = os.urandom(32)
        else:
            master = BACKUP_MASTER_KEY
            if master is None:
                if not password:
                    raise RuntimeError("Enter the backup password to unlock encryption")
                master = _unwrap_backup_master_key_with_password(password)

        # A supplied password sets or changes the password wrapper. When the
        # existing key is already unlocked, no old password has to be stored.
        if password:
            if len(password) < 8:
                raise RuntimeError("Enter a backup password with at least 8 characters")
            salt = os.urandom(16)
            password_key = _backup_password_key(password, salt)
            nonce = os.urandom(12)
            wrapped = AESGCM(password_key).encrypt(
                nonce,
                bytes(master),
                BACKUP_ENCRYPTION_AAD,
            )
            config = {
                "schema": "update-monitor-backup-encryption-v1",
                "configured": True,
                "enabled": True,
                "algorithm": "AES-256-GCM",
                "kdf": {
                    "name": "scrypt",
                    "salt": _b64e(salt),
                    "n": 32768,
                    "r": 8,
                    "p": 1,
                },
                "nonce": _b64e(nonce),
                "wrapped_key": _b64e(wrapped),
                "updated_at": utc_now(),
            }
        else:
            config["enabled"] = True
            config["updated_at"] = utc_now()

        BACKUP_MASTER_KEY = bytes(master)
        _write_backup_encryption_config(config)
        _write_backup_auto_unlock(BACKUP_MASTER_KEY)

    return backup_encryption_public_status()


def unlock_backup_encryption(password):
    global BACKUP_MASTER_KEY
    with BACKUP_ENCRYPTION_LOCK:
        master = _unwrap_backup_master_key_with_password(password)
        BACKUP_MASTER_KEY = bytes(master)
        _write_backup_auto_unlock(BACKUP_MASTER_KEY)
    return backup_encryption_public_status()


def _backup_encryption_key_for_write():
    status = backup_encryption_public_status()
    if not status.get("enabled"):
        return None
    with BACKUP_ENCRYPTION_LOCK:
        key = BACKUP_MASTER_KEY
    if key is None:
        raise RuntimeError(
            "Backup encryption is enabled but locked. Unlock it in Settings before creating a backup."
        )
    return bytes(key)


def _backup_encryption_key_for_restore(meta):
    encrypted = bool(
        isinstance(meta, dict)
        and isinstance(meta.get("encryption"), dict)
        and meta.get("encryption", {}).get("encrypted")
    )
    if not encrypted:
        return None
    with BACKUP_ENCRYPTION_LOCK:
        key = BACKUP_MASTER_KEY
    if key is None:
        raise RuntimeError(
            "This backup is encrypted. Unlock backup encryption in Settings before restoring it."
        )
    return bytes(key)


def _backup_payload_aad(backup_id):
    return ("update-monitor-backup-payload-v1:" + str(backup_id or "")).encode("utf-8")


def _encrypt_file_gcm(source_path, target_path, key, aad):
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(bytes(key)), modes.GCM(nonce))
    encryptor = cipher.encryptor()
    encryptor.authenticate_additional_data(bytes(aad))

    with open(source_path, "rb") as source, open(target_path, "wb") as target:
        while True:
            chunk = source.read(BACKUP_PAYLOAD_CHUNK_SIZE)
            if not chunk:
                break
            target.write(encryptor.update(chunk))
        target.write(encryptor.finalize())

    return nonce, bytes(encryptor.tag)


def _decrypt_file_gcm(source_path, target_path, key, nonce, tag, aad):
    cipher = Cipher(
        algorithms.AES(bytes(key)),
        modes.GCM(bytes(nonce), bytes(tag)),
    )
    decryptor = cipher.decryptor()
    decryptor.authenticate_additional_data(bytes(aad))

    with open(source_path, "rb") as source, open(target_path, "wb") as target:
        while True:
            chunk = source.read(BACKUP_PAYLOAD_CHUNK_SIZE)
            if not chunk:
                break
            target.write(decryptor.update(chunk))
        target.write(decryptor.finalize())


def _encrypt_backup_payload(work_dir, metadata, key):
    """Pack compose/data into one encrypted payload while keeping a small index."""
    backup_id = str(metadata.get("backup_id") or "")
    archive_path = work_dir / ".payload.tar.gz"
    encrypted_path = work_dir / "payload.umbackup.enc"

    with tarfile.open(archive_path, "w:gz") as archive:
        compose_path = work_dir / "compose.yaml"
        if compose_path.is_file():
            archive.add(compose_path, arcname="compose.yaml", recursive=False)
        data_root = work_dir / "data"
        if data_root.is_dir():
            archive.add(data_root, arcname="data", recursive=True)

    try:
        nonce, tag = _encrypt_file_gcm(
            archive_path,
            encrypted_path,
            key,
            _backup_payload_aad(backup_id),
        )
    finally:
        try:
            archive_path.unlink()
        except OSError:
            pass

    # Remove plaintext only after authenticated encryption completed.
    try:
        (work_dir / "compose.yaml").unlink()
    except OSError:
        pass
    shutil.rmtree(work_dir / "data", ignore_errors=True)

    metadata["encryption"] = {
        "encrypted": True,
        "algorithm": "AES-256-GCM",
        "payload": "payload.umbackup.enc",
        "nonce": _b64e(nonce),
        "tag": _b64e(tag),
    }


def _safe_extract_backup_tar(archive_path, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if target != base and base not in target.parents:
                raise RuntimeError("Encrypted backup contains an unsafe archive path")
        # Python 3.12 data filter also rejects dangerous device nodes / links.
        archive.extractall(destination, filter="data")


def _materialize_backup_payload(backup_dir, meta):
    """Return (payload_root, temporary_root_or_none)."""
    backup_dir = Path(backup_dir)
    encryption = meta.get("encryption") if isinstance(meta.get("encryption"), dict) else {}
    if not encryption.get("encrypted"):
        return backup_dir, None

    key = _backup_encryption_key_for_restore(meta)
    payload_name = str(encryption.get("payload") or "payload.umbackup.enc")
    encrypted_path = backup_dir / payload_name
    if not encrypted_path.is_file():
        raise RuntimeError("Encrypted backup payload is missing")

    staging_root = CACHE_DIR / "restore-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="restore-", dir=str(staging_root)))
    archive_path = temp_root / "payload.tar.gz"
    payload_root = temp_root / "payload"

    try:
        _decrypt_file_gcm(
            encrypted_path,
            archive_path,
            key,
            _b64d(encryption.get("nonce")),
            _b64d(encryption.get("tag")),
            _backup_payload_aad(meta.get("backup_id")),
        )
        _safe_extract_backup_tar(archive_path, payload_root)
        archive_path.unlink(missing_ok=True)
        return payload_root, temp_root
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _version_confirmation_identity(stack_key, item):
    image_ref = str((item or {}).get("image_ref") or "").strip()
    parsed = parse_image_ref(image_ref) if image_ref else {}
    displayed = str(
        (item or {}).get("resolved_installed_tag")
        or (item or {}).get("display_installed_version")
        or (item or {}).get("tag")
        or ""
    ).strip()
    local_digests = sorted({
        str(value).strip()
        for value in ((item or {}).get("local_digests") or [])
        if str(value).strip()
    })
    return {
        "stack_key": str(stack_key or "").strip(),
        "image_ref": image_ref,
        "normalized_repo": str(parsed.get("normalized_repo") or "").strip(),
        "tag": str(parsed.get("tag") or (item or {}).get("tag") or "").strip(),
        "displayed_version": displayed,
        "local_digests": local_digests,
    }


def _version_confirmation_signature(identity):
    payload = json.dumps(identity or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def version_confirmation_for_item(stack_key, item):
    identity = _version_confirmation_identity(stack_key, item)
    signature = _version_confirmation_signature(identity)
    with VERSION_CONFIRMATIONS_LOCK:
        record = VERSION_CONFIRMATIONS.get(signature)
        record = dict(record) if isinstance(record, dict) else None
    if not record:
        return {"confirmed": False, "signature": signature}
    return {
        "confirmed": True,
        "signature": signature,
        "confirmed_at": record.get("confirmed_at"),
        "displayed_version": record.get("displayed_version"),
    }


def annotate_version_confirmations(scan_payload):
    for app_item in (scan_payload or {}).get("apps") or []:
        stack_key = str((app_item or {}).get("stack_key") or "").strip()
        for item in (app_item or {}).get("items") or []:
            resolution = (item or {}).get("installed_version_resolution") or {}
            status = str(resolution.get("status") or "").strip().lower()
            if status in {"hint", "unresolved"}:
                item["version_confirmation"] = version_confirmation_for_item(stack_key, item)
            else:
                item["version_confirmation"] = {"confirmed": False}
    return scan_payload


def confirm_installed_version(stack_key, image_ref):
    app_item = find_scanned_app(stack_key)
    if not app_item:
        raise RuntimeError("App not found in current scan")

    wanted = str(image_ref or "").strip()
    item = next(
        (row for row in (app_item.get("items") or []) if str((row or {}).get("image_ref") or "").strip() == wanted),
        None,
    )
    if not item:
        raise RuntimeError("Docker image not found in current scan")

    resolution = (item or {}).get("installed_version_resolution") or {}
    status = str(resolution.get("status") or "").strip().lower()
    if status not in {"hint", "unresolved"}:
        raise RuntimeError("This Docker version no longer requires manual confirmation")

    identity = _version_confirmation_identity(stack_key, item)
    if not identity.get("displayed_version"):
        raise RuntimeError("No displayed Docker version is available to confirm")
    signature = _version_confirmation_signature(identity)
    record = {
        **identity,
        "signature": signature,
        "confirmed_at": utc_now(),
    }

    with VERSION_CONFIRMATIONS_LOCK:
        VERSION_CONFIRMATIONS[signature] = record
        # Keep the cache bounded. Older exact confirmations are harmless, but
        # there is no reason to let this small preference file grow forever.
        if len(VERSION_CONFIRMATIONS) > 500:
            ordered = sorted(
                VERSION_CONFIRMATIONS.items(),
                key=lambda pair: str((pair[1] or {}).get("confirmed_at") or ""),
                reverse=True,
            )
            VERSION_CONFIRMATIONS.clear()
            VERSION_CONFIRMATIONS.update(dict(ordered[:500]))
        save_json(VERSION_CONFIRMATIONS_FILE, VERSION_CONFIRMATIONS)

    return {
        "confirmed": True,
        "signature": signature,
        "confirmed_at": record["confirmed_at"],
        "displayed_version": identity["displayed_version"],
    }


def _parse_backup_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_backup_retention(value):
    raw = str(value or "").strip().lower()
    aliases = {
        "2d": "2",
        "5d": "5",
        "10d": "10",
        "permanent": "forever",
        "infinite": "forever",
        "infinity": "forever",
        "never": "forever",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in BACKUP_RETENTION_OPTIONS else BACKUP_RETENTION_DEFAULT


def current_backup_retention():
    return _normalize_backup_retention(settings.get("backup_retention"))


def current_backup_retention_days():
    return BACKUP_RETENTION_OPTIONS[current_backup_retention()]


def _normalize_backup_max_per_app(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return BACKUP_MAX_PER_APP_DEFAULT
    return parsed if parsed in BACKUP_MAX_PER_APP_OPTIONS else BACKUP_MAX_PER_APP_DEFAULT


def _normalize_backup_max_per_app_map(value):
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        normalized[key] = _normalize_backup_max_per_app(raw_value)
    return normalized


def current_backup_max_per_app(stack_key=None):
    key = str(stack_key or "").strip()
    if not key:
        return BACKUP_MAX_PER_APP_DEFAULT
    per_app = _normalize_backup_max_per_app_map(
        settings.get("backup_max_per_app_by_stack")
    )
    return _normalize_backup_max_per_app(
        per_app.get(key, BACKUP_MAX_PER_APP_DEFAULT)
    )


def _migrate_backup_max_per_app_settings():
    # One-time migration from the former global count to currently known apps.
    # Existing apps keep the previous value; apps first seen later start at 3.
    if bool(settings.get("backup_max_per_app_migrated_v1")):
        return

    legacy_value = _normalize_backup_max_per_app(
        settings.get("backup_max_per_app")
    )
    per_app = _normalize_backup_max_per_app_map(
        settings.get("backup_max_per_app_by_stack")
    )
    for app_item in (scan_state.get("apps") or []):
        key = str((app_item or {}).get("stack_key") or "").strip()
        if key and key not in per_app:
            per_app[key] = legacy_value

    settings["backup_max_per_app_by_stack"] = per_app
    settings["backup_max_per_app_migrated_v1"] = True
    settings["backup_max_per_app"] = BACKUP_MAX_PER_APP_DEFAULT
    save_json(SETTINGS_FILE, settings)


def backup_expiry_for_created(created_at):
    if created_at is None:
        return None
    days = current_backup_retention_days()
    if days is None:
        return None
    return created_at + timedelta(days=days)


def backup_retention_public_status(stack_key=None):
    value = current_backup_retention()
    days = BACKUP_RETENTION_OPTIONS[value]
    return {
        "value": value,
        "days": days,
        "forever": days is None,
        "max_per_app": current_backup_max_per_app(stack_key),
        "stack_key": str(stack_key or "").strip() or None,
    }


def _rewrite_backup_retention_metadata():
    """Keep stored metadata aligned with the currently selected global policy."""
    if not BACKUP_DIR.exists():
        return

    days = current_backup_retention_days()
    with BACKUP_LOCK:
        try:
            stack_dirs = list(BACKUP_DIR.iterdir())
        except OSError:
            return

        for stack_dir in stack_dirs:
            if not stack_dir.is_dir():
                continue
            try:
                candidates = list(stack_dir.iterdir())
            except OSError:
                continue

            for candidate in candidates:
                if not candidate.is_dir() or candidate.name.endswith(".incomplete"):
                    continue
                metadata_path = candidate / "metadata.json"
                if not metadata_path.is_file():
                    continue
                meta = load_json(metadata_path, {})
                if not isinstance(meta, dict):
                    continue
                created = _parse_backup_timestamp(meta.get("created_at"))
                if created is None:
                    continue
                expires = None if days is None else created + timedelta(days=days)
                meta["retention_days"] = days
                meta["expires_at"] = expires.isoformat() if expires is not None else None
                try:
                    _write_backup_json(metadata_path, meta)
                except Exception:
                    continue


def cleanup_expired_backups(now_utc=None):
    """Remove completed backups according to age and per-app count limits.

    Incomplete staging directories are still removed after one day even when
    completed backups are configured to remain forever. Completed backups are
    also capped to the configured 1/2/3 newest backups in each app folder.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    retention_days = current_backup_retention_days()
    incomplete_cutoff = now_utc - timedelta(days=1)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    with BACKUP_LOCK:
        for stack_dir in list(BACKUP_DIR.iterdir()):
            if not stack_dir.is_dir():
                continue

            completed = []
            stack_key_for_limit = None
            for candidate in list(stack_dir.iterdir()):
                if not candidate.is_dir():
                    continue

                created = None
                metadata_path = candidate / "metadata.json"
                if metadata_path.is_file():
                    meta = load_json(metadata_path, {})
                    if isinstance(meta, dict):
                        meta_stack_key = str(meta.get("stack_key") or "").strip()
                        if meta_stack_key and not stack_key_for_limit:
                            stack_key_for_limit = meta_stack_key
                        created = _parse_backup_timestamp(meta.get("created_at"))
                if created is None:
                    try:
                        created = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
                    except OSError:
                        continue

                is_incomplete = candidate.name.endswith(".incomplete")
                if is_incomplete:
                    if created < incomplete_cutoff:
                        shutil.rmtree(candidate, ignore_errors=True)
                    continue

                if retention_days is not None and created < (now_utc - timedelta(days=retention_days)):
                    shutil.rmtree(candidate, ignore_errors=True)
                    continue

                completed.append((created, candidate))

            # Keep only this app's own configured number of newest backups.
            max_per_app = current_backup_max_per_app(stack_key_for_limit)
            completed.sort(key=lambda row: row[0], reverse=True)
            for _, candidate in completed[max_per_app:]:
                shutil.rmtree(candidate, ignore_errors=True)

            try:
                if not any(stack_dir.iterdir()):
                    stack_dir.rmdir()
            except OSError:
                pass


def _backup_container_count_from_metadata(meta):
    """Read container count from current or older backup metadata."""
    try:
        explicit = int((meta or {}).get("container_count"))
        if explicit >= 0:
            return explicit
    except (TypeError, ValueError):
        pass

    names = set()
    for image in (meta or {}).get("images") or []:
        if not isinstance(image, dict):
            continue
        for container in image.get("containers") or []:
            if not isinstance(container, dict):
                continue
            name = str(container.get("name") or "").strip()
            if name:
                names.add(name)
    if names:
        return len(names)

    for name in (meta or {}).get("running_containers_before_backup") or []:
        value = str(name or "").strip()
        if value:
            names.add(value)
    return len(names)


def backup_summary_index():
    """Build one lightweight index for all still-valid completed backups."""
    try:
        cleanup_expired_backups()
    except Exception:
        pass

    result = {"by_stack": {}, "by_project": {}}
    if not BACKUP_DIR.exists():
        return result

    records = []
    with BACKUP_LOCK:
        try:
            stack_dirs = list(BACKUP_DIR.iterdir())
        except OSError:
            return result

        for stack_dir in stack_dirs:
            if not stack_dir.is_dir():
                continue
            try:
                candidates = list(stack_dir.iterdir())
            except OSError:
                continue

            for candidate in candidates:
                if not candidate.is_dir() or candidate.name.endswith(".incomplete"):
                    continue
                metadata_path = candidate / "metadata.json"
                if not metadata_path.is_file():
                    continue

                meta = load_json(metadata_path, {})
                if not isinstance(meta, dict):
                    continue

                created_at = _parse_backup_timestamp(meta.get("created_at"))
                if created_at is None:
                    try:
                        created_at = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
                    except OSError:
                        continue

                expires_at = backup_expiry_for_created(created_at)
                if expires_at is not None and expires_at <= datetime.now(timezone.utc):
                    continue

                records.append({
                    "backup_id": str(meta.get("backup_id") or candidate.name),
                    "mode": _normalize_backup_mode(meta.get("mode"), "quick"),
                    "created_at": created_at.isoformat(),
                    "expires_at": expires_at.isoformat() if expires_at is not None else None,
                    "container_count": _backup_container_count_from_metadata(meta),
                    "data_mount_count": len(meta.get("data_mounts") or []),
                    "encrypted": bool(
                        isinstance(meta.get("encryption"), dict)
                        and meta.get("encryption", {}).get("encrypted")
                    ),
                    "compose_project": str(meta.get("compose_project") or "").strip() or None,
                    "stack_key": str(meta.get("stack_key") or "").strip() or None,
                })

    def add_record(bucket_map, key, record):
        key = str(key or "").strip()
        if not key:
            return
        bucket = bucket_map.setdefault(key, {
            "exists": True,
            "backup_count": 0,
            "latest": None,
        })
        bucket["backup_count"] += 1
        latest = bucket.get("latest")
        if latest is None or str(record.get("created_at") or "") > str(latest.get("created_at") or ""):
            bucket["latest"] = dict(record)

    for record in records:
        add_record(result["by_stack"], record.get("stack_key"), record)
        add_record(result["by_project"], record.get("compose_project"), record)

    return result


def backup_summary_for_app(index, stack_key, compose_project):
    """Return backup state for one currently installed app."""
    index = index if isinstance(index, dict) else {}
    by_stack = index.get("by_stack") if isinstance(index.get("by_stack"), dict) else {}
    by_project = index.get("by_project") if isinstance(index.get("by_project"), dict) else {}

    stack_key = str(stack_key or "").strip()
    compose_project = str(compose_project or "").strip()

    summary = by_stack.get(stack_key) if stack_key else None
    if summary is None and compose_project:
        summary = by_project.get(compose_project)

    if not isinstance(summary, dict):
        return {"exists": False, "backup_count": 0, "latest": None}

    return {
        "exists": bool(summary.get("exists")),
        "backup_count": max(0, int(summary.get("backup_count") or 0)),
        "latest": dict(summary.get("latest") or {}) or None,
    }


def _backup_container_inspects(app_item):
    names = []
    for container in (app_item or {}).get("containers") or []:
        name = str(container.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        for item in (app_item or {}).get("items") or []:
            for container in item.get("containers") or []:
                name = str(container.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
    if not names:
        raise RuntimeError("No Docker containers found for backup")

    rc, raw, err = run(["docker", "inspect"] + names, timeout=60)
    if rc != 0:
        raise RuntimeError(err or "docker inspect failed while preparing backup")
    try:
        rows = json.loads(raw)
    except Exception as exc:
        raise RuntimeError("Could not parse Docker inspect data for backup") from exc
    return [row for row in rows if isinstance(row, dict)]


def _backup_mount_identity(mount):
    mount_type = str((mount or {}).get("Type") or "").lower()
    if mount_type == "volume":
        name = str((mount or {}).get("Name") or "").strip()
        if name:
            return "volume:" + name
    source = str((mount or {}).get("Source") or "").strip()
    return f"{mount_type}:{source}" if source else ""


def _backup_mount_is_media_or_system(mount):
    source = str((mount or {}).get("Source") or "").strip().lower().rstrip("/")
    destination = str((mount or {}).get("Destination") or "").strip().lower().rstrip("/")
    if not destination:
        return True

    system_destinations = {
        "/var/run/docker.sock", "/run/docker.sock", "/etc/localtime", "/etc/timezone",
        "/dev", "/proc", "/sys",
    }
    if destination in system_destinations or destination.endswith(".sock"):
        return True

    media_roots = {
        "/media", "/movies", "/movie", "/tv", "/music", "/photos", "/photo",
        "/downloads", "/download", "/library", "/libraries", "/mnt", "/storage",
        "/share", "/shares", "/videos", "/video",
    }
    if destination in media_roots or any(destination.startswith(root + "/") for root in media_roots):
        return True

    source_media_markers = (
        "/media/", "/movies/", "/movie/", "/tv/", "/music/", "/photos/",
        "/downloads/", "/download/", "/library/", "/libraries/", "/videos/",
    )
    if any(marker in source + "/" for marker in source_media_markers):
        return True
    return False


def _backup_mount_is_app_data(mount):
    if _backup_mount_is_media_or_system(mount):
        return False
    mount_type = str((mount or {}).get("Type") or "").lower()
    source = str((mount or {}).get("Source") or "").strip().lower()
    destination = str((mount or {}).get("Destination") or "").strip().lower().rstrip("/")

    # ZimaOS / CasaOS application bind mounts normally live below AppData.
    if re.search(r"(?:^|/)appdata(?:/|$)", source):
        return True

    # Docker named volumes are application-owned unless mounted into an obvious
    # media/system destination (filtered above).
    if mount_type == "volume":
        return True

    # Conservative fallback for database/config destinations. This deliberately
    # avoids arbitrary bind mounts so a Plex media library or NAS share is never
    # copied merely because it is attached to the container.
    exact_or_prefix = (
        "/config", "/app/config", "/app/data", "/data/db",
        "/var/lib/postgresql", "/var/lib/mysql", "/var/lib/mariadb",
        "/var/lib/mongodb", "/var/lib/redis", "/var/lib/grafana",
        "/var/lib/influxdb", "/var/lib/prometheus",
    )
    return any(destination == root or destination.startswith(root + "/") for root in exact_or_prefix)


def _backup_candidate_mounts(inspects):
    selected = []
    seen = set()
    for row in inspects:
        container_name = str(row.get("Name") or "").lstrip("/").strip()
        for mount in row.get("Mounts") or []:
            if not isinstance(mount, dict) or not _backup_mount_is_app_data(mount):
                continue
            identity = _backup_mount_identity(mount)
            destination = str(mount.get("Destination") or "").strip()
            if not identity or not destination or identity in seen:
                continue
            seen.add(identity)
            selected.append({
                "identity": identity,
                "container": container_name,
                "destination": destination,
                "type": str(mount.get("Type") or ""),
                "source": str(mount.get("Source") or ""),
                "name": str(mount.get("Name") or "") or None,
                "rw": bool(mount.get("RW", True)),
            })
    return selected


def _write_backup_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _backup_directory_size_bytes(root):
    """Return backup directory size without following symlinks."""
    root = Path(root)
    total = 0
    try:
        for base, dirs, files in os.walk(root, followlinks=False):
            # Never descend into symlinked directories.
            dirs[:] = [
                name for name in dirs
                if not (Path(base) / name).is_symlink()
            ]
            for name in files:
                path = Path(base) / name
                try:
                    if path.is_symlink():
                        continue
                    total += int(path.stat().st_size)
                except OSError:
                    continue
    except OSError:
        return None
    return max(0, total)


def _backup_image_snapshot(app_item, inspects):
    inspect_by_name = {
        str(row.get("Name") or "").lstrip("/"): row
        for row in inspects
    }
    images = []
    for item in (app_item or {}).get("items") or []:
        image_ref = str(item.get("image_ref") or "").strip()
        containers = []
        for container in item.get("containers") or []:
            name = str(container.get("name") or "").strip()
            row = inspect_by_name.get(name) or {}
            image_id = str(row.get("Image") or "").strip() or None
            repo_digests = sorted(docker_image_repo_digests(image_id)) if image_id else []
            state = row.get("State") or {}
            health = state.get("Health") or {}
            restart_policy = ((row.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
            try:
                restart_count = int(row.get("RestartCount") or 0)
            except Exception:
                restart_count = 0
            containers.append({
                "name": name,
                "compose_service": str(container.get("compose_service") or "").strip() or None,
                "state": str(state.get("Status") or container.get("state") or "unknown"),
                "exit_code": state.get("ExitCode"),
                "health": str(health.get("Status") or "").strip().lower() or None,
                "restart_count": max(0, restart_count),
                "restart_policy": str(restart_policy or "").strip() or None,
                "configured_image": str((row.get("Config") or {}).get("Image") or image_ref),
                "image_id": image_id,
                "repo_digests": repo_digests,
            })
        images.append({
            "image_ref": image_ref,
            "tag": item.get("tag"),
            "resolved_installed_tag": item.get("resolved_installed_tag"),
            "containers": containers,
        })
    return images

def create_pre_update_backup(app_item, mode, stack_key=None):
    mode = _normalize_backup_mode(mode, "none")
    if mode == "full" and is_update_monitor_self_app(app_item):
        mode = "quick"
    if mode == "none":
        return None

    stack_key = str(stack_key or (app_item or {}).get("stack_key") or "").strip()
    project = str((app_item or {}).get("compose_project") or "").strip()
    if not stack_key or not project:
        raise RuntimeError("Backup requires a ZimaOS Compose app")

    # Backup progress is milestone based. docker cp does not expose a reliable
    # byte progress stream, so the percentages represent completed backup stages.
    update_action_progress(stack_key, 2, determinate=True, phase="backup_prepare")
    encryption_key = _backup_encryption_key_for_write()

    cleanup_expired_backups()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stack_dir = BACKUP_DIR / _backup_stack_dir_name(
        stack_key,
        compose_project=project,
        app_name=(app_item or {}).get("name"),
    )
    stack_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc)
    backup_id = created_at.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    final_dir = stack_dir / backup_id
    work_dir = stack_dir / (backup_id + ".incomplete")
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=False)

    inspects = _backup_container_inspects(app_item)
    compose_yaml = casaos_compose_yaml(project)
    (work_dir / "compose.yaml").write_text(compose_yaml, encoding="utf-8")

    mounts = _backup_candidate_mounts(inspects)
    running_before = [
        str(row.get("Name") or "").lstrip("/")
        for row in inspects
        if str((row.get("State") or {}).get("Status") or "") == "running"
    ]

    retention_days = current_backup_retention_days()
    expires_at = (
        created_at + timedelta(days=retention_days)
        if retention_days is not None
        else None
    )

    metadata = {
        "schema": "update-monitor-backup-v2",
        "backup_id": backup_id,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "retention_days": retention_days,
        "mode": mode,
        "stack_key": stack_key,
        "app_name": str((app_item or {}).get("name") or ""),
        "compose_project": project,
        "update_monitor_version": VERSION,
        "monitor_policy": get_monitor_policy(stack_key),
        "images": _backup_image_snapshot(app_item, inspects),
        "persistent_data_detected": bool(mounts),
        "persistent_mount_count": len(mounts),
        "persistent_mounts": [
            {
                "identity": mount.get("identity"),
                "container": mount.get("container"),
                "destination": mount.get("destination"),
                "type": mount.get("type"),
                "name": mount.get("name"),
                "rw": bool(mount.get("rw", True)),
            }
            for mount in mounts
        ],
        "data_mounts": [],
        "container_count": len(inspects),
        "running_containers_before_backup": running_before,
    }
    _write_backup_json(work_dir / "metadata.json", metadata)
    update_action_progress(stack_key, 4, determinate=True, phase="backup_prepare")

    stopped = []
    restart_errors = []
    try:
        if mode == "full" and mounts:
            # Stop only containers that were running. A consistent offline copy is
            # safer for SQLite/PostgreSQL/etc. than copying live database files.
            update_action_progress(stack_key, 5, determinate=True, phase="backup_stop")
            total_running = max(1, len(running_before))
            for stop_index, name in enumerate(running_before, start=1):
                rc, _, err = run(["docker", "stop", "-t", "30", name], timeout=60)
                if rc != 0:
                    raise RuntimeError(f"Could not stop {name} for full backup: {err or 'docker stop failed'}")
                stopped.append(name)
                update_action_progress(
                    stack_key,
                    5 + round((stop_index / total_running) * 3),
                    determinate=True,
                    phase="backup_stop",
                )

            data_root = work_dir / "data"
            data_root.mkdir(parents=True, exist_ok=True)
            total_mounts = max(1, len(mounts))
            for index, mount in enumerate(mounts, start=1):
                container_name = mount["container"]
                destination = mount["destination"]
                target_name = f"mount-{index:03d}"
                target_path = data_root / target_name
                update_action_progress(
                    stack_key,
                    8 + round(((index - 1) / total_mounts) * 5),
                    determinate=True,
                    phase="backup_data",
                )
                rc, _, err = run(
                    ["docker", "cp", "-a", f"{container_name}:{destination}", str(target_path)],
                    timeout=60 * 60,
                )
                if rc != 0:
                    raise RuntimeError(
                        f"Full backup failed while copying {container_name}:{destination}: {err or 'docker cp failed'}"
                    )
                metadata["data_mounts"].append({
                    **mount,
                    "backup_path": f"data/{target_name}",
                })
                _write_backup_json(work_dir / "metadata.json", metadata)
                update_action_progress(
                    stack_key,
                    8 + round((index / total_mounts) * 5),
                    determinate=True,
                    phase="backup_data",
                )
    except Exception:
        # Keep no apparently-complete backup after a copy/stop failure.
        raise
    finally:
        if stopped:
            update_action_progress(stack_key, 14, determinate=True, phase="backup_restart")
        for name in stopped:
            rc, _, err = run(["docker", "start", name], timeout=60)
            if rc != 0:
                restart_errors.append(f"{name}: {err or 'docker start failed'}")

    if restart_errors:
        metadata["restart_errors"] = restart_errors
        _write_backup_json(work_dir / "metadata.json", metadata)
        raise RuntimeError(
            "Backup was created, but one or more containers could not be restarted: "
            + "; ".join(restart_errors)
        )

    metadata["completed_at"] = utc_now()
    if encryption_key is not None:
        _encrypt_backup_payload(work_dir, metadata, encryption_key)
    _write_backup_json(work_dir / "metadata.json", metadata)
    metadata["size_bytes"] = _backup_directory_size_bytes(work_dir)
    _write_backup_json(work_dir / "metadata.json", metadata)
    work_dir.replace(final_dir)
    cleanup_expired_backups()
    update_action_progress(stack_key, 15, determinate=True, phase="backup_done")

    return {
        "backup_id": backup_id,
        "mode": mode,
        "created_at": metadata["created_at"],
        "expires_at": metadata["expires_at"],
        "data_mount_count": len(metadata["data_mounts"]),
        "container_count": int(metadata.get("container_count") or 0),
        "encrypted": bool(
            isinstance(metadata.get("encryption"), dict)
            and metadata.get("encryption", {}).get("encrypted")
        ),
    }



def _backup_records_for_app(app_item):
    stack_key = str((app_item or {}).get("stack_key") or "").strip()
    project = str((app_item or {}).get("compose_project") or "").strip()
    records = []

    try:
        cleanup_expired_backups()
    except Exception:
        pass

    if not BACKUP_DIR.exists():
        return records

    with BACKUP_LOCK:
        try:
            stack_dirs = list(BACKUP_DIR.iterdir())
        except OSError:
            return records

        for stack_dir in stack_dirs:
            if not stack_dir.is_dir():
                continue
            try:
                candidates = list(stack_dir.iterdir())
            except OSError:
                continue

            for candidate in candidates:
                if not candidate.is_dir() or candidate.name.endswith(".incomplete"):
                    continue
                metadata_path = candidate / "metadata.json"
                if not metadata_path.is_file():
                    continue
                meta = load_json(metadata_path, {})
                if not isinstance(meta, dict):
                    continue

                meta_stack = str(meta.get("stack_key") or "").strip()
                meta_project = str(meta.get("compose_project") or "").strip()
                if not (
                    (stack_key and meta_stack == stack_key)
                    or (project and meta_project == project)
                ):
                    continue

                created = _parse_backup_timestamp(meta.get("created_at"))
                if created is None:
                    continue
                expires = backup_expiry_for_created(created)
                if expires is not None and expires <= datetime.now(timezone.utc):
                    continue

                records.append({
                    "path": candidate,
                    "metadata": meta,
                    "backup_id": str(meta.get("backup_id") or candidate.name),
                    "created_at": created,
                    "expires_at": expires,
                })

    records.sort(key=lambda item: item["created_at"], reverse=True)
    return records


def app_backup_list(app_item):
    rows = []
    for record in _backup_records_for_app(app_item):
        meta = record["metadata"]
        size_bytes = meta.get("size_bytes")
        try:
            size_bytes = int(size_bytes) if size_bytes is not None else None
        except (TypeError, ValueError):
            size_bytes = None

        if size_bytes is None or size_bytes < 0:
            size_bytes = _backup_directory_size_bytes(record["path"])
            if size_bytes is not None:
                meta["size_bytes"] = size_bytes
                try:
                    _write_backup_json(record["path"] / "metadata.json", meta)
                except Exception:
                    pass

        expires_at = record.get("expires_at")
        mode = _normalize_backup_mode(meta.get("mode"), "quick")
        restore_safe, restore_warning = _quick_restore_safety(app_item, meta)
        rows.append({
            "backup_id": record["backup_id"],
            "created_at": record["created_at"].isoformat(),
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
            "retention": current_backup_retention(),
            "mode": mode,
            "container_count": _backup_container_count_from_metadata(meta),
            "data_mount_count": len(meta.get("data_mounts") or []),
            "persistent_mount_count": _backup_persistent_mount_count(meta, app_item),
            "restore_safe": bool(restore_safe),
            "restore_warning": restore_warning,
            "size_bytes": size_bytes,
            "encrypted": bool(
                isinstance(meta.get("encryption"), dict)
                and meta.get("encryption", {}).get("encrypted")
            ),
        })
    return rows


def delete_app_backup(app_item, backup_id):
    requested = str(backup_id or "").strip()
    if not requested:
        raise RuntimeError("No backup selected")

    record = _select_backup_record(app_item, requested)
    target = Path(record["path"])
    backup_root = BACKUP_DIR.resolve()
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise RuntimeError("The selected backup path is unavailable") from exc

    if resolved == backup_root or backup_root not in resolved.parents:
        raise RuntimeError("Refusing to delete a path outside the backup directory")

    with BACKUP_LOCK:
        if not target.is_dir():
            raise RuntimeError("The selected backup no longer exists")
        shutil.rmtree(target)
        parent = target.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    return requested


def _select_backup_record(app_item, backup_id=None):
    records = _backup_records_for_app(app_item)
    if not records:
        raise RuntimeError("No restorable backup exists for this app")

    requested = str(backup_id or "").strip()
    if not requested:
        return records[0]

    for record in records:
        if record["backup_id"] == requested:
            return record
    raise RuntimeError("The selected backup no longer exists")



def _current_app_container_names(app_item):
    names = []
    for container in (app_item or {}).get("containers") or []:
        name = str((container or {}).get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    for item in (app_item or {}).get("items") or []:
        for container in (item or {}).get("containers") or []:
            name = str((container or {}).get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _backup_persistent_mount_count(meta, app_item=None):
    try:
        stored = int((meta or {}).get("persistent_mount_count"))
        if stored >= 0:
            return stored
    except (TypeError, ValueError):
        pass

    stored_mounts = (meta or {}).get("persistent_mounts") or []
    if isinstance(stored_mounts, list) and stored_mounts:
        return len([item for item in stored_mounts if isinstance(item, dict)])

    # Legacy backup metadata did not record mount discovery for quick backups.
    # Inspect the current app conservatively so an old quick backup cannot be
    # used for a destructive database downgrade merely because the metadata is old.
    if app_item:
        try:
            return len(_backup_candidate_mounts(_backup_container_inspects(app_item)))
        except Exception:
            # Unknown must be treated as potentially persistent for quick rollback.
            return 1
    return 0


def _backup_current_image_differs(meta):
    compared = 0
    for image in (meta or {}).get("images") or []:
        if not isinstance(image, dict):
            continue
        for container in image.get("containers") or []:
            if not isinstance(container, dict):
                continue
            name = str(container.get("name") or "").strip()
            expected_id = str(container.get("image_id") or "").strip()
            if not name:
                continue
            current_id = str(docker_container_image_id(name) or "").strip()
            if not current_id:
                return True
            if expected_id:
                compared += 1
                if current_id != expected_id:
                    return True
                continue

            expected_digests = {
                str(parse_repo_digest(value)[1] or "").strip().lower()
                for value in container.get("repo_digests") or []
                if str(parse_repo_digest(value)[1] or "").strip()
            }
            if expected_digests:
                compared += 1
                current_digests = {
                    str(value or "").strip().lower()
                    for value in docker_image_repo_digests(current_id)
                    if str(value or "").strip()
                }
                if not current_digests.intersection(expected_digests):
                    return True

    # If no image identity can be proven, be conservative for quick restore.
    return compared == 0


def _quick_restore_safety(app_item, meta):
    if _normalize_backup_mode((meta or {}).get("mode"), "quick") != "quick":
        return True, None
    persistent_count = _backup_persistent_mount_count(meta, app_item)
    if persistent_count <= 0:
        return True, None
    if not _backup_current_image_differs(meta):
        return True, None
    return False, (
        "Quick backup cannot safely roll this app back after a different image has "
        "already started because persistent AppData/volume data was not backed up. "
        "Use a full backup for version rollback."
    )


def _restore_helper_image_id():
    identity = update_monitor_self_identity()
    candidate = str(identity.get("container_id") or identity.get("container_name") or "").strip()
    if not candidate:
        raise RuntimeError("Could not resolve Update Monitor helper container for full restore")
    rc, out, err = run(
        ["docker", "inspect", "--format", "{{.Image}}", candidate],
        timeout=15,
    )
    if rc != 0 or not str(out or "").strip():
        raise RuntimeError(
            "Could not resolve the local Update Monitor image needed for exact AppData restore: "
            + str(err or "docker inspect failed")
        )
    return str(out).strip()


def _restore_container_mount_destinations(container_name):
    rc, out, err = run(["docker", "inspect", str(container_name)], timeout=20)
    if rc != 0:
        raise RuntimeError(
            f"Could not inspect {container_name} before full data restore: {err or 'docker inspect failed'}"
        )
    try:
        rows = json.loads(out or "[]")
    except Exception as exc:
        raise RuntimeError(f"Could not parse Docker mounts for {container_name}") from exc
    if not rows or not isinstance(rows[0], dict):
        raise RuntimeError(f"Container {container_name} is unavailable for full data restore")
    return {
        str(mount.get("Destination") or "").strip()
        for mount in rows[0].get("Mounts") or []
        if isinstance(mount, dict) and str(mount.get("Destination") or "").strip()
    }


def _clear_restore_mount_contents(container_name, destination, helper_image_id):
    destination = str(destination or "").strip()
    if not destination.startswith("/") or destination in {"/", "/proc", "/sys", "/dev", "/etc"}:
        raise RuntimeError(f"Refusing unsafe restore destination: {destination or '-'}")

    mount_destinations = _restore_container_mount_destinations(container_name)
    if destination not in mount_destinations:
        raise RuntimeError(
            f"Full restore destination is no longer mounted in {container_name}: {destination}"
        )

    script = (
        'target="$1"; '
        'case "$target" in ""|"/"|"/proc"|"/sys"|"/dev"|"/etc") exit 97;; esac; '
        'find "$target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
    )
    rc, out, err = run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--entrypoint", "/bin/sh",
            "--volumes-from", str(container_name),
            str(helper_image_id),
            "-c", script, "restore-helper", destination,
        ],
        timeout=60 * 30,
    )
    if rc != 0:
        raise RuntimeError(
            f"Could not clear {container_name}:{destination} for exact AppData restore: "
            f"{err or out or 'restore helper failed'}"
        )


def _stop_restore_containers(container_names):
    for name in container_names:
        state = docker_container_state(name)
        if state in {"running", "restarting", "paused"}:
            rc, _, err = run(["docker", "stop", "-t", "30", name], timeout=60)
            if rc != 0:
                raise RuntimeError(
                    f"Could not stop {name} before restore: {err or 'docker stop failed'}"
                )


def _restore_full_backup_data(payload_root, meta, progress_stack_key=None):
    mounts = [
        mount for mount in (meta or {}).get("data_mounts") or []
        if isinstance(mount, dict)
    ]
    if _backup_persistent_mount_count(meta) > 0 and not mounts:
        raise RuntimeError(
            "Full backup metadata reports persistent AppData, but no data payload is available. "
            "Restore was stopped before the old image could start."
        )
    if not mounts:
        return 0

    helper_image_id = _restore_helper_image_id()
    restored = 0
    total_mounts = max(1, len(mounts))
    for mount_index, mount in enumerate(mounts, start=1):
        if progress_stack_key:
            update_action_progress(
                progress_stack_key,
                15 + round(((mount_index - 1) / total_mounts) * 30),
                determinate=True,
                phase="restore_data",
            )
        container_name = str(mount.get("container") or "").strip()
        destination = str(mount.get("destination") or "").strip()
        backup_path = str(mount.get("backup_path") or "").strip()
        source_path = Path(payload_root) / backup_path
        if not container_name or not destination or not backup_path or not source_path.exists():
            raise RuntimeError("Full backup contains an incomplete data-mount entry")
        if docker_container_state(container_name) is None:
            raise RuntimeError(
                f"Container {container_name} no longer exists, so its AppData cannot be restored safely"
            )

        # Exact restore: remove files created by the newer app version first.
        # Overlay-copying an old SQLite/PostgreSQL directory is not sufficient,
        # because new WAL/schema/helper files may survive and corrupt a downgrade.
        _clear_restore_mount_contents(container_name, destination, helper_image_id)
        rc, _, err = run(
            ["docker", "cp", "-a", str(source_path) + "/.", f"{container_name}:{destination}"],
            timeout=60 * 60,
        )
        if rc != 0:
            raise RuntimeError(
                f"Could not restore {container_name}:{destination}: {err or 'docker cp failed'}"
            )
        restored += 1
        if progress_stack_key:
            update_action_progress(
                progress_stack_key,
                15 + round((mount_index / total_mounts) * 30),
                determinate=True,
                phase="restore_data",
            )
    return restored


def _remove_restore_containers_for_recreate(container_names):
    """Remove stopped pre-restore containers without deleting their volumes.

    ZimaOS compose apply is asynchronous. If the old container remains present,
    an existence-only wait can mistake that stopped old container for the newly
    restored one and start the wrong image again. Removing it here forces the
    restored Compose application to create a fresh container from the backup image.
    """
    for name in dict.fromkeys(str(value or "").strip() for value in container_names or []):
        if not name:
            continue
        state = docker_container_state(name)
        if state is None:
            continue
        if state in {"running", "restarting", "paused"}:
            rc, _, err = run(["docker", "stop", "-t", "30", name], timeout=60)
            if rc != 0:
                raise RuntimeError(
                    f"Could not stop {name} before exact restore recreation: {err or 'docker stop failed'}"
                )
        # Deliberately do NOT pass -v: named/anonymous volumes must survive.
        rc, _, err = run(["docker", "rm", "-f", name], timeout=60)
        if rc != 0 and docker_container_state(name) is not None:
            raise RuntimeError(
                f"Could not remove old container {name} before restore recreation: {err or 'docker rm failed'}"
            )


def _backup_container_runtime_map(meta):
    result = {}
    for image in (meta or {}).get("images") or []:
        if not isinstance(image, dict):
            continue
        for container in image.get("containers") or []:
            if not isinstance(container, dict):
                continue
            name = str(container.get("name") or "").strip()
            if name:
                result[name] = dict(container)
    return result


def _wait_for_completed_restore_jobs(meta, timeout=180):
    snapshots = _backup_container_runtime_map(meta)
    for name, expected in snapshots.items():
        expected_state = str(expected.get("state") or "").strip().lower()
        try:
            expected_exit = int(expected.get("exit_code"))
        except Exception:
            expected_exit = None
        if expected_state != "exited" or expected_exit != 0:
            continue

        deadline = time.time() + max(10, int(timeout))
        last = None
        while time.time() < deadline:
            last = docker_container_runtime_snapshot(name)
            if not last:
                time.sleep(2)
                continue
            status = str(last.get("status") or "").strip().lower()
            if status == "exited":
                try:
                    exit_code = int(last.get("exit_code"))
                except Exception:
                    exit_code = None
                if exit_code == 0:
                    break
                logs = docker_container_recent_logs(name, tail=40).get("output") or "-"
                raise RuntimeError(
                    f"Restored one-shot container {name} exited with code {exit_code}: {logs}"
                )
            if status in {"dead", "removing"} or bool(last.get("restarting")):
                raise RuntimeError(
                    f"Restored one-shot container {name} entered {status or 'restarting'} state"
                )
            time.sleep(2)
        else:
            raise RuntimeError(
                f"Restored one-shot container {name} did not complete successfully within {timeout} seconds"
            )


def _restore_container_uses_backup_image(name, expected):
    current_id = str(docker_container_image_id(name) or "").strip()
    if not current_id:
        return False
    expected_id = str((expected or {}).get("image_id") or "").strip()
    if expected_id and current_id == expected_id:
        return True
    expected_digests = {
        str(parse_repo_digest(value)[1] or "").strip().lower()
        for value in (expected or {}).get("repo_digests") or []
        if str(parse_repo_digest(value)[1] or "").strip()
    }
    if not expected_digests:
        return False
    current_digests = {
        str(value or "").strip().lower()
        for value in docker_image_repo_digests(current_id)
        if str(value or "").strip()
    }
    return bool(current_digests.intersection(expected_digests))


def _wait_for_restore_image_activation(meta, container_names, timeout=180):
    """Wait for ZimaOS to recreate every container with the backed-up image.

    Merely seeing the old container name is not sufficient because compose apply
    may still be running asynchronously.
    """
    snapshots = _backup_container_runtime_map(meta)
    names = [str(name or "").strip() for name in container_names or [] if str(name or "").strip()]
    if not names:
        return
    deadline = time.time() + max(10, int(timeout))
    last_wrong = []
    while time.time() < deadline:
        missing = []
        wrong = []
        for name in names:
            if docker_container_state(name) is None:
                missing.append(name)
                continue
            expected = snapshots.get(name) or {}
            if not _restore_container_uses_backup_image(name, expected):
                wrong.append(name)
        if not missing and not wrong:
            return
        last_wrong = wrong
        time.sleep(2)
    if last_wrong:
        raise RuntimeError(
            "Restore image activation timed out: these containers still do not use the backed-up image: "
            + ", ".join(last_wrong)
        )
    missing = [name for name in names if docker_container_state(name) is None]
    raise RuntimeError(
        "Restore applied the Compose file, but containers did not reappear: "
        + ", ".join(missing)
    )


def _verify_restored_images(meta):
    verified = 0
    for name, expected in _backup_container_runtime_map(meta).items():
        current_id = str(docker_container_image_id(name) or "").strip()
        if not current_id:
            raise RuntimeError(f"Restored container is missing: {name}")
        expected_id = str(expected.get("image_id") or "").strip()
        if expected_id and current_id == expected_id:
            verified += 1
            continue

        expected_digests = {
            str(parse_repo_digest(value)[1] or "").strip().lower()
            for value in expected.get("repo_digests") or []
            if str(parse_repo_digest(value)[1] or "").strip()
        }
        current_digests = {
            str(value or "").strip().lower()
            for value in docker_image_repo_digests(current_id)
            if str(value or "").strip()
        }
        if expected_digests and current_digests.intersection(expected_digests):
            verified += 1
            continue
        raise RuntimeError(
            f"Restore image verification failed for {name}: the running container does not use the backed-up image"
        )
    return verified


def _verify_restore_runtime(meta, container_names):
    snapshots = _backup_container_runtime_map(meta)
    verified = 0
    for name in container_names:
        expected = snapshots.get(name) or {}
        expected_state = str(expected.get("state") or "").strip().lower()
        if not expected_state:
            expected_state = (
                "running"
                if name in set(meta.get("running_containers_before_backup") or [])
                else "exited"
            )
        try:
            baseline_restarts = int(expected.get("restart_count") or 0)
        except Exception:
            baseline_restarts = 0
        try:
            expected_exit = expected.get("exit_code")
            expected_exit = None if expected_exit is None else int(expected_exit)
        except Exception:
            expected_exit = None

        try:
            wait_for_image_source_runtime_stable(
                name,
                timeout=120,
                stable_seconds=12,
                baseline_restart_count=baseline_restarts,
                expected_state=expected_state,
                expected_exit_code=expected_exit,
                expected_runtime_snapshot={
                    "health": expected.get("health"),
                    "restart_count": baseline_restarts,
                    "restart_policy": expected.get("restart_policy"),
                },
            )
        except RuntimeError as exc:
            message = str(exc).replace("New image source", "Restored app").replace(
                "source switch", "restore"
            )
            raise RuntimeError(message) from exc
        verified += 1
    return verified


def _preferred_backup_digest(image):
    for container in (image or {}).get("containers") or []:
        for digest in container.get("repo_digests") or []:
            digest = str(digest or "").strip()
            if "@sha256:" in digest:
                return digest
    return None


def _restore_compose_yaml(payload_root, meta):
    compose_path = Path(payload_root) / "compose.yaml"
    if not compose_path.is_file():
        raise RuntimeError("Backup Compose file is missing")
    yaml_text = compose_path.read_text(encoding="utf-8")

    pinned = 0
    restore_pin_mappings = []
    for image in meta.get("images") or []:
        if not isinstance(image, dict):
            continue
        target = _preferred_backup_digest(image)
        if not target:
            continue

        services = []
        configured_refs = []
        old_ref = str(image.get("image_ref") or "").strip()
        if old_ref:
            configured_refs.append(old_ref)

        for container in image.get("containers") or []:
            if not isinstance(container, dict):
                continue
            service = str(container.get("compose_service") or "").strip()
            if service and service not in services:
                services.append(service)
            configured = str(container.get("configured_image") or "").strip()
            if configured and configured not in configured_refs:
                configured_refs.append(configured)

        changed = 0
        if services:
            updated, count, _ = replace_compose_service_images(
                yaml_text,
                services,
                target,
            )
            if count:
                yaml_text = updated
                changed += count

        if not changed:
            for configured in configured_refs:
                updated, count = replace_compose_image_ref(
                    yaml_text,
                    configured,
                    target,
                )
                if count:
                    yaml_text = updated
                    changed += count

        if changed:
            pinned += 1
            if old_ref and not parse_image_ref(old_ref).get("pinned_digest"):
                restore_pin_mappings.append({
                    "tracking_ref": old_ref,
                    "pinned_ref": target,
                    "services": list(services),
                })

    return yaml_text, pinned, restore_pin_mappings


def _backup_container_names(meta):
    names = []
    for image in meta.get("images") or []:
        if not isinstance(image, dict):
            continue
        for container in image.get("containers") or []:
            if not isinstance(container, dict):
                continue
            name = str(container.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _wait_for_restore_containers(container_names, timeout=180):
    container_names = [str(name) for name in container_names if str(name).strip()]
    if not container_names:
        return
    deadline = time.time() + max(5, int(timeout))
    while time.time() < deadline:
        states = {name: docker_container_state(name) for name in container_names}
        if all(state is not None for state in states.values()):
            return states
        time.sleep(2)
    missing = [name for name in container_names if docker_container_state(name) is None]
    raise RuntimeError(
        "Restore applied the Compose file, but containers did not reappear: "
        + ", ".join(missing)
    )


def _restore_container_runtime_states(meta, container_names):
    running_before = {
        str(name or "").strip()
        for name in meta.get("running_containers_before_backup") or []
        if str(name or "").strip()
    }

    for name in container_names:
        current = docker_container_state(name)
        should_run = name in running_before
        if should_run and current != "running":
            rc, _, err = run(["docker", "start", name], timeout=60)
            if rc != 0:
                raise RuntimeError(f"Could not restore running state for {name}: {err or 'docker start failed'}")
        elif not should_run and current == "running":
            rc, _, err = run(["docker", "stop", "-t", "30", name], timeout=60)
            if rc != 0:
                raise RuntimeError(f"Could not restore stopped state for {name}: {err or 'docker stop failed'}")


def restore_app_backup(app_item, backup_id=None, stack_key=None):
    stack_key = str(stack_key or (app_item or {}).get("stack_key") or "").strip()
    project = str((app_item or {}).get("compose_project") or "").strip()
    if not stack_key or not project:
        raise RuntimeError("Backup restore requires a ZimaOS Compose app")

    record = _select_backup_record(app_item, backup_id)
    meta = record["metadata"]
    mode = _normalize_backup_mode(meta.get("mode"), "quick")
    payload_root = None
    temporary_root = None
    data_restored = False

    try:
        restore_safe, restore_warning = _quick_restore_safety(app_item, meta)
        if not restore_safe:
            raise RuntimeError(restore_warning or "Quick backup is not safe for this rollback")

        payload_root, temporary_root = _materialize_backup_payload(
            record["path"],
            meta,
        )
        restore_yaml, pinned_images, restore_pin_mappings = _restore_compose_yaml(
            payload_root,
            meta,
        )

        # Validate the saved Compose through ZimaOS before touching application data.
        update_action_progress(stack_key, 5, determinate=True, phase="restore_prepare")
        casaos_apply_compose(project, restore_yaml, dry_run=True)

        names = _backup_container_names(meta)
        current_names = _current_app_container_names(app_item)
        all_names = list(dict.fromkeys(current_names + names))
        restored_mounts = 0

        if mode == "full":
            # Critical order for downgrade safety:
            #   stop NEW app -> restore OLD AppData exactly -> remove OLD container
            #   -> apply/recreate OLD image.
            # The old image must never get a chance to open a database that was
            # already migrated by the newer image.
            update_action_progress(stack_key, 10, determinate=True, phase="restore_stop")
            _stop_restore_containers(all_names)
            update_action_progress(stack_key, 15, determinate=True, phase="restore_data")
            restored_mounts = _restore_full_backup_data(
                payload_root,
                meta,
                progress_stack_key=stack_key,
            )
            data_restored = restored_mounts > 0

            # The previous implementation left the stopped pre-restore container
            # in place. Because ZimaOS applies Compose asynchronously, our wait could
            # see that old container immediately and start it again before ZimaOS had
            # recreated the backup image. Force exact recreation after AppData is safe.
            update_action_progress(stack_key, 50, determinate=True, phase="restore_recreate")
            _remove_restore_containers_for_recreate(all_names)

        update_action_progress(stack_key, 58, determinate=True, phase="restore_compose")
        casaos_apply_compose(project, restore_yaml, dry_run=False)

        update_action_progress(stack_key, 68, determinate=True, phase="restore_activate")
        if mode == "full":
            _wait_for_restore_image_activation(meta, names, timeout=180)
        else:
            _wait_for_restore_containers(names, timeout=180)

        # Let backed-up one-shot migration/job containers finish naturally before
        # restoring the exact running/stopped state. Stopping them immediately can
        # turn a successful code-0 job into a false code-137 failure.
        update_action_progress(stack_key, 78, determinate=True, phase="restore_jobs")
        _wait_for_completed_restore_jobs(meta, timeout=180)

        update_action_progress(stack_key, 84, determinate=True, phase="restore_runtime")
        _restore_container_runtime_states(meta, names)

        update_action_progress(stack_key, 92, determinate=True, phase="restore_verify")
        verified_images = _verify_restored_images(meta)
        verified_runtime = _verify_restore_runtime(meta, names)
        update_action_progress(stack_key, 99, determinate=True, phase="restore_verify")

        remember_restore_pin_assignments(
            project,
            stack_key,
            restore_pin_mappings,
            backup_id=record.get("backup_id"),
            source="backup-restore",
        )

        return {
            "backup_id": record["backup_id"],
            "mode": mode,
            "encrypted": bool(
                isinstance(meta.get("encryption"), dict)
                and meta.get("encryption", {}).get("encrypted")
            ),
            "restored_containers": len(names),
            "restored_data_mounts": restored_mounts,
            "verified_images": verified_images,
            "verified_runtime": verified_runtime,
            "digest_pinned_images": pinned_images,
            "project": project,
        }
    except Exception as exc:
        # Once old AppData has been put back, never restart the newer application
        # automatically on top of it. Keep the app stopped if a later Compose or
        # verification stage fails; the selected full backup remains available for retry.
        if data_restored:
            try:
                _stop_restore_containers(_current_app_container_names(app_item) + _backup_container_names(meta))
            except Exception:
                pass
            raise RuntimeError(
                f"{exc} | Restored AppData was protected by keeping the app stopped."
            ) from exc
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(str(exc)) from exc
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)

def perform_version_update(app_item):
    project = app_item.get("compose_project")
    if not project:
        raise RuntimeError("ZimaOS Compose app ID is missing")

    policy = get_monitor_policy(app_item.get("stack_key"))
    mode = policy.get("mode")
    if mode not in {"upgrade", "follow"}:
        raise RuntimeError("This version is not released for installation")

    group = select_primary_version_group(app_item)
    if not group:
        if len(build_version_groups(app_item)) > 1:
            raise RuntimeError(
                "Multiple independent version groups were detected; "
                "no safe primary version group could be selected"
            )
        raise RuntimeError("No selectable version group is available")

    group_items = list(group.get("items") or [])
    if not group_items:
        raise RuntimeError("The selected version group contains no images")

    if mode == "upgrade":
        target_tag = str(policy.get("target_tag") or "").strip()
        available_tags = list((group.get("available") or {}).get("display_tags") or [])
        if not target_tag or target_tag not in available_tags:
            raise RuntimeError("The selected version is no longer available")
        target_map = _version_group_target_map(group, target_tag, "available")
    else:
        target_tag = FOLLOW_POLICY_TAG
        target_map = {
            str(item.get("image_ref") or "").strip(): FOLLOW_POLICY_TAG
            for item in group_items
            if str(item.get("image_ref") or "").strip()
        }

    if len(target_map) != len(group_items):
        raise RuntimeError(
            "The selected version is not available for every image in the version group"
        )

    current_aliases = {
        _version_alias(_version_item_current_tag(item))
        for item in group_items
        if _version_alias(_version_item_current_tag(item))
    }
    if _version_alias(target_tag) in current_aliases and len(current_aliases) == 1:
        raise RuntimeError("The selected version is already configured")

    yaml_text = casaos_compose_yaml(project)
    updated_yaml = yaml_text

    plans = []
    changed_services = []
    expected_by_container = {}
    old_refs = []
    new_refs = []

    for item in group_items:
        old_ref = str(item.get("image_ref") or "").strip()
        parsed = parse_image_ref(old_ref)
        if parsed.get("pinned_digest"):
            raise RuntimeError("Digest-pinned images cannot be changed by this path")
        if not old_ref or not parsed.get("repo"):
            raise RuntimeError("Could not determine the configured image reference")

        exact_target_tag = str(target_map.get(old_ref) or "").strip()
        if not exact_target_tag:
            raise RuntimeError(
                f"No installable target tag was found for version-group image {old_ref}"
            )

        new_ref = f"{parsed['repo']}:{exact_target_tag}"

        # Do not probe the registry a second time here.  docker_pull_verified()
        # is the authoritative installability check and avoids an extra Docker
        # Hub HEAD/manifest request immediately after the scan.
        services = []
        container_names = []

        for container in item.get("containers") or []:
            service_name = str(container.get("compose_service") or "").strip()
            if service_name and service_name not in services:
                services.append(service_name)

            container_name = str(container.get("name") or "").strip()
            if container_name and container_name not in container_names:
                container_names.append(container_name)

        before_yaml = updated_yaml
        replacements = 0
        item_changed_services = []

        if services:
            updated_yaml, replacements, item_changed_services = replace_compose_service_images(
                updated_yaml,
                services,
                new_ref,
            )
            expected_services = set(services)
            changed_service_set = set(item_changed_services)
            if replacements != len(expected_services) or changed_service_set != expected_services:
                missing_services = sorted(expected_services - changed_service_set)
                raise RuntimeError(
                    "Safe version change aborted: not every Compose service in the "
                    f"version group could be rewritten (image={old_ref}, "
                    f"changed={sorted(changed_service_set)}, missing={missing_services}). "
                    "The configuration was left unchanged."
                )
        else:
            # Fallback only for older/non-standard stacks that expose no Compose
            # service labels at all. Never use a global text replacement after a
            # partial service rewrite, because that can leave one stack mixed.
            updated_yaml, replacements = replace_compose_image_ref(
                updated_yaml,
                old_ref,
                new_ref,
            )
            item_changed_services = []

        if replacements < 1 or updated_yaml == before_yaml:
            service_text = ", ".join(services) if services else "unknown"
            raise RuntimeError(
                "The image entry for a version-group service could not be found "
                f"(image: {old_ref}, service: {service_text}). "
                "The configuration was left unchanged."
            )

        for service in item_changed_services:
            if service not in changed_services:
                changed_services.append(service)

        for container_name in container_names:
            expected_by_container[container_name] = new_ref

        old_refs.append(old_ref)
        new_refs.append(new_ref)
        # Preserve a concrete human-readable version only when it is already
        # authoritative for the currently installed image. This is especially
        # useful for fixed-version -> :latest transitions where the scan already
        # proved that both tags point to the exact same digest. In that case the
        # post-update verification can keep the known version without crawling
        # hundreds of registry tags again.
        source_version_hint = str(item.get("resolved_installed_tag") or "").strip()
        if not parse_version(source_version_hint):
            current_tag_hint = str(_version_item_current_tag(item) or "").strip()
            source_version_hint = current_tag_hint if parse_version(current_tag_hint) else ""

        plans.append({
            "old_image_ref": old_ref,
            "new_image_ref": new_ref,
            "target_tag": exact_target_tag,
            "services": services,
            "containers": container_names,
            "follow_target_digests": list(item.get("follow_target_digests") or []),
            "source_version_hint": source_version_hint or None,
        })

    if not expected_by_container:
        raise RuntimeError("No Docker containers found for this version-group update")

    # Validate the complete multi-service Compose change before touching the app.
    casaos_apply_compose(project, updated_yaml, dry_run=True)

    with scan_lock:
        update_platform = str(scan_state.get("platform") or "").strip()

    if not update_platform:
        rc, platform_raw, platform_error = run(
            ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
            timeout=20,
        )
        if rc != 0:
            raise RuntimeError(platform_error or "Could not determine Docker platform")

        platform_map = {
            "linux/x86_64": "linux/amd64",
            "linux/amd64": "linux/amd64",
            "linux/aarch64": "linux/arm64",
            "linux/arm64": "linux/arm64",
            "linux/armv7l": "linux/arm/v7",
        }
        update_platform = platform_map.get(
            platform_raw.strip(),
            platform_raw.strip(),
        )

    # Establish and verify the exact target image before changing the app.
    target_verification = {}
    tag_only_switch = bool(app_item.get("follow_tag_switch_only"))

    for plan in plans:
        old_ref = str(plan.get("old_image_ref") or "").strip()
        new_ref = str(plan.get("new_image_ref") or "").strip()

        if not new_ref or new_ref in target_verification:
            continue

        if tag_only_switch:
            parsed_new = parse_image_ref(new_ref)
            repo_key = str(parsed_new.get("normalized_repo") or "").strip()
            expected = {
                str(value or "").strip().lower()
                for value in (plan.get("follow_target_digests") or [])
                if str(value or "").strip()
            }
            if not repo_key or not expected:
                raise RuntimeError(
                    f"The scan did not preserve a verified :latest digest for {new_ref}"
                )

            # Same digest was already proven by the scan, so only add the
            # rolling tag locally; no duplicate registry request/download.
            docker_tag_same_image(old_ref, new_ref)

            local = {
                str(value or "").strip().lower()
                for value in docker_image_repo_digests(new_ref, repo_key)
                if str(value or "").strip()
            }
            if not local.intersection(expected):
                raise RuntimeError(
                    f"Tag-only normalization verification failed for {new_ref}: "
                    f"local={sorted(local)}, scan={sorted(expected)}"
                )

            version_hint = str(plan.get("source_version_hint") or "").strip()
            if version_hint and parse_version(version_hint):
                # The old concrete tag and :latest were already proven to be the
                # same image. Persist that exact digest -> version mapping now so
                # the targeted post-update scan can display the version instantly.
                installed_version_cache_put(repo_key, sorted(local), version_hint)

            target_verification[new_ref] = {
                "image_ref": new_ref,
                "repo": repo_key,
                "expected_digests": sorted(expected),
                "local_digests": sorted(local),
                "method": "local-tag-only-scan-digest",
                "resolved_version_hint": version_hint if parse_version(version_hint) else None,
            }
        else:
            scan_expected = (
                plan.get("follow_target_digests") or None
                if mode == "follow"
                else None
            )
            pulled = docker_pull_verified(
                new_ref,
                update_platform,
                expected_digests=scan_expected,
            )
            pulled["method"] = "verified-docker-pull"
            target_verification[new_ref] = pulled

    original_states = {}
    original_one_shots = set()
    for container in app_item.get("containers") or []:
        name = str(container.get("name") or "").strip()
        state = str(container.get("state") or "unknown").strip()
        if not name:
            continue
        if state not in {"running", "exited", "created", "dead"}:
            raise RuntimeError(
                f"Unsupported container state for safe version update: {name} ({state})"
            )
        original_states[name] = state
        snapshot = docker_container_runtime_snapshot(name) or {}
        try:
            exit_code = int(snapshot.get("exit_code"))
        except Exception:
            exit_code = None
        if (
            state == "exited"
            and exit_code == 0
            and str(snapshot.get("restart_policy") or "").strip().lower() == "on-failure"
        ):
            original_one_shots.add(name)

    # Apply all services in the version family through ZimaOS. From this point on,
    # any failure must restore the complete original Compose YAML; partial stacks
    # are never accepted as a valid result.
    apply_attempted = False
    try:
        apply_attempted = True
        casaos_apply_compose(project, updated_yaml, dry_run=False)

        # Verify the persisted Compose source itself before waiting for containers.
        persisted_yaml = casaos_compose_yaml(project)
        persisted_images = compose_service_image_map_from_yaml(persisted_yaml)
        for plan in plans:
            target_ref = str(plan.get("new_image_ref") or "").strip()
            for service in plan.get("services") or []:
                actual_ref = str(persisted_images.get(str(service)) or "").strip()
                if actual_ref != target_ref:
                    raise RuntimeError(
                        "ZimaOS persisted an incomplete version-group change: "
                        f"service={service}, expected={target_ref}, actual={actual_ref or '-'}"
                    )

        # Wait until ZimaOS activates the requested image reference.
        active_images = wait_for_version_group_compose_update(
            project,
            expected_by_container,
            timeout=600,
        )

        verified_runtime = {}

        for container_name, expected_ref in expected_by_container.items():
            verification = target_verification.get(expected_ref) or {}
            expected_digests = set(verification.get("expected_digests") or [])

            if not expected_digests:
                raise RuntimeError(
                    f"No verified target digest is available for {expected_ref}"
                )

            matched, _ = container_matches_target_digest(
                container_name,
                expected_ref,
                expected_digests,
            )

            if not matched:
                current_container_id = docker_container_id(container_name)
                if not current_container_id:
                    raise RuntimeError(
                        f"Could not resolve current container ID for {container_name}"
                    )
                casaos_recreate_container(current_container_id, project)

            verified_runtime[container_name] = wait_for_container_target_digest(
                container_name,
                expected_ref,
                expected_digests,
                timeout=600,
            )

        # Restore ordinary stopped containers, but never stop a successful
        # on-failure one-shot job. Those jobs must be allowed to finish naturally.
        restored_states = {}
        for name, previous_state in original_states.items():
            if name in original_one_shots:
                deadline = time.time() + 600
                while time.time() < deadline:
                    snapshot = docker_container_runtime_snapshot(name) or {}
                    status = str(snapshot.get("status") or "").strip().lower()
                    if status == "exited":
                        try:
                            exit_code = int(snapshot.get("exit_code"))
                        except Exception:
                            exit_code = None
                        if exit_code != 0:
                            raise RuntimeError(
                                f"One-shot container {name} failed after the update "
                                f"(exit_code={exit_code})"
                            )
                        restored_states[name] = "completed"
                        break
                    if status in {"dead", "removing"} or bool(snapshot.get("restarting")):
                        raise RuntimeError(
                            f"One-shot container {name} did not complete cleanly "
                            f"(state={status or 'unknown'})"
                        )
                    time.sleep(2)
                else:
                    raise RuntimeError(
                        f"One-shot container {name} did not finish within 600 seconds"
                    )
                continue

            current_state = docker_container_state(name)
            if previous_state != "running" and current_state == "running":
                docker_stop_container(name)
                current_state = docker_container_state(name)
            restored_states[name] = current_state

    except Exception as update_exc:
        rollback_error = None
        if apply_attempted:
            try:
                casaos_apply_compose(project, yaml_text, dry_run=False)
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
        if rollback_error:
            raise RuntimeError(
                f"Version update failed: {update_exc}. Automatic Compose rollback also failed: {rollback_error}"
            ) from update_exc
        raise RuntimeError(
            f"Version update failed and the original Compose configuration was restored: {update_exc}"
        ) from update_exc

    # Manual "switch version" remains a one-shot action. Once the complete
    # family is updated, pin the new state again.
    if mode == "upgrade":
        save_monitor_policy(app_item.get("stack_key"), "fixed", None, auto_enabled=False)

    return {
        "project": project,
        # Compatibility fields for callers that previously expected one image.
        "old_image_ref": old_refs[0] if old_refs else None,
        "new_image_ref": new_refs[0] if new_refs else None,
        "old_image_refs": old_refs,
        "new_image_refs": new_refs,
        "target_tag": target_tag,
        "version_group": _public_version_group(group),
        "version_group_plans": plans,
        "compose_services": changed_services,
        "containers": list(expected_by_container),
        "active_images": active_images,
        "verified_runtime": verified_runtime,
        "target_verification": target_verification,
        "final_states": restored_states,
        "policy_after_update": get_monitor_policy(app_item.get("stack_key")),
        "engine": "verified-pull+zimaos-compose-version-group-update",
    }


def perform_image_update(app_item):
    """
    Install same-tag updates such as :latest -> newer :latest.

    The old path accepted a changed container/image ID as success. This path
    instead verifies the actual remote digest before and after recreation.
    """
    if is_update_monitor_self_app(app_item):
        return perform_self_image_update_handoff(app_item)

    project = app_item.get("compose_project")
    if not project:
        raise RuntimeError("ZimaOS Compose app ID is missing")

    targets = [
        item for item in (app_item.get("items") or [])
        if item.get("status") == "IMAGE_UPDATE"
        and item.get("update_policy") == "follow_tag"
    ]
    if not targets:
        raise RuntimeError("No eligible same-tag image update found")

    with scan_lock:
        update_platform = str(scan_state.get("platform") or "").strip()

    if not update_platform:
        rc, platform_raw, platform_error = run(
            ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
            timeout=20,
        )
        if rc != 0:
            raise RuntimeError(
                platform_error or "Could not determine Docker platform"
            )
        platform_map = {
            "linux/x86_64": "linux/amd64",
            "linux/amd64": "linux/amd64",
            "linux/aarch64": "linux/arm64",
            "linux/arm64": "linux/arm64",
            "linux/armv7l": "linux/arm/v7",
        }
        update_platform = platform_map.get(
            platform_raw.strip(),
            platform_raw.strip(),
        )

    stack_key = str(app_item.get("stack_key") or "").strip()
    update_action_progress(stack_key, phase="pulling")

    # Pull and verify every unique target before asking ZimaOS to recreate.
    target_verification = {}
    for item in targets:
        image_ref = str(
            item.get("tracking_image_ref")
            or item.get("image_ref")
            or ""
        ).strip()
        if not image_ref:
            raise RuntimeError("Could not determine same-tag target image")
        if image_ref in target_verification:
            continue
        pulled = docker_pull_verified(image_ref, update_platform)
        pulled["method"] = "verified-docker-pull"
        target_verification[image_ref] = pulled

    target_containers = []
    seen_names = set()
    restore_pin_targets = []

    for item in targets:
        image_ref = str(
            item.get("tracking_image_ref")
            or item.get("image_ref")
            or ""
        ).strip()
        verification = target_verification.get(image_ref) or {}
        expected_digests = list(verification.get("expected_digests") or [])
        if not expected_digests:
            raise RuntimeError(
                f"No verified remote digest available for {image_ref}"
            )

        item_services = [
            str(container.get("compose_service") or "").strip()
            for container in (item.get("containers") or [])
            if str(container.get("compose_service") or "").strip()
        ]
        if item.get("restore_generated_digest_pin"):
            restore_pin_targets.append({
                "image_ref": image_ref,
                "services": sorted(set(item_services)),
            })

        for container in item.get("containers") or []:
            name = str(container.get("name") or "").strip()
            state = str(container.get("state") or "unknown").strip()
            if not name or name in seen_names:
                continue
            if state not in {"running", "exited", "created", "dead"}:
                raise RuntimeError(
                    f"Unsupported container state for safe update: {name} ({state})"
                )
            seen_names.add(name)
            target_containers.append({
                "name": name,
                "state": state,
                "image_ref": image_ref,
                "expected_digests": expected_digests,
            })

    if not target_containers:
        raise RuntimeError("No Docker containers found for this update")

    before_image_ids = {}
    after_image_ids = {}
    verified_runtime = {}
    restored_states = {}

    restore_pin_applied = False
    if restore_pin_targets:
        yaml_text = casaos_compose_yaml(project)
        updated_yaml = yaml_text
        changed = 0

        for restore_target in restore_pin_targets:
            updated_yaml, count, _ = replace_compose_service_images(
                updated_yaml,
                restore_target.get("services") or [],
                restore_target.get("image_ref"),
            )
            changed += int(count or 0)

        if changed < 1 or updated_yaml == yaml_text:
            raise RuntimeError(
                "Backup restore source could not be returned to its original "
                "tracking tag before the update"
            )

        # This mutation happens only because the user explicitly requested the
        # available update. The target image was pulled and digest-verified above.
        casaos_apply_compose(project, updated_yaml, dry_run=True)
        casaos_apply_compose(project, updated_yaml, dry_run=False)
        restore_pin_applied = True

    for target in target_containers:
        name = target["name"]
        previous_state = target["state"]
        image_ref = target["image_ref"]
        expected_digests = target["expected_digests"]

        old_container_id = docker_container_id(name)
        old_image_id = docker_container_image_id(name)
        if not old_container_id or not old_image_id:
            raise RuntimeError(
                f"Could not read current Docker container/image ID for {name}"
            )
        before_image_ids[name] = old_image_id

        update_action_progress(stack_key, phase="verifying")

        if restore_pin_applied:
            # ZimaOS already recreated the stack when the Compose source was
            # returned from our internal backup digest to the user's tracking
            # tag. Verify the running container directly against the pulled
            # target digest instead of forcing a second recreation.
            deadline = time.time() + 180
            verified = None
            while time.time() < deadline:
                runtime = docker_container_runtime_snapshot(name) or {}
                current_id = docker_container_id(name)
                image_id = docker_container_image_id(name)
                local_digests = set(
                    docker_image_repo_digests(
                        image_id,
                        parse_image_ref(image_ref).get("normalized_repo"),
                    )
                ) if image_id else set()
                expected = {
                    str(value or "").strip().lower()
                    for value in expected_digests
                    if str(value or "").strip()
                }
                if (
                    current_id
                    and image_id
                    and local_digests.intersection(expected)
                    and str(runtime.get("status") or "") == "running"
                    and not runtime.get("restarting")
                    and runtime.get("health") != "unhealthy"
                ):
                    verified = {
                        "container_id": current_id,
                        "image_id": image_id,
                        "local_digests": sorted(local_digests),
                        "config_image": str(
                            (runtime or {}).get("config_image") or image_ref
                        ),
                    }
                    break
                time.sleep(2)

            if verified is None:
                raise RuntimeError(
                    f"ZimaOS returned {name} to the tracking tag, but the "
                    "container did not reach the verified update digest"
                )
        else:
            update_action_progress(stack_key, phase="recreating")
            casaos_recreate_container(old_container_id, project)

            verified = wait_for_container_recreate_target_digest(
                name,
                old_container_id,
                image_ref,
                expected_digests,
                timeout=600,
            )

        new_image_id = str(verified.get("image_id") or "").strip()
        if new_image_id:
            after_image_ids[name] = new_image_id
        verified_runtime[name] = verified

        if previous_state != "running":
            docker_stop_container(name)
            final_state = docker_container_state(name)
            if final_state not in {"exited", "dead", "created"}:
                raise RuntimeError(
                    f"Update succeeded, but {name} did not return to a stopped state"
                )
            restored_states[name] = final_state
        else:
            final_state = docker_container_state(name)
            if final_state != "running":
                raise RuntimeError(
                    f"Update reached the target image, but {name} is not running "
                    f"(state={final_state or 'unknown'})"
                )
            restored_states[name] = final_state

    return {
        "project": project,
        "containers": [item["name"] for item in target_containers],
        "before_image_ids": before_image_ids,
        "after_image_ids": after_image_ids,
        "target_verification": target_verification,
        "verified_runtime": verified_runtime,
        "restored_stopped_state": any(
            item["state"] != "running" for item in target_containers
        ),
        "final_states": restored_states,
        "engine": "verified-pull+zimaos-container-recreate",
    }



_DOCKER_INFO_LABEL_PREFIXES = (
    "com.docker.compose.",
    "org.opencontainers.image.",
    "org.label-schema.",
)
_DOCKER_INFO_LABEL_EXACT = {
    "casaos.icon",
    "icon",
    "net.unraid.docker.icon",
}


def _docker_info_safe_labels(labels):
    """Return useful non-environment Docker/OCI labels for the information view."""
    if not isinstance(labels, dict):
        return {}
    result = {}
    for key, value in labels.items():
        key = str(key or "").strip()
        if not key:
            continue
        low = key.lower()
        if low in _DOCKER_INFO_LABEL_EXACT or any(
            low.startswith(prefix) for prefix in _DOCKER_INFO_LABEL_PREFIXES
        ):
            result[key] = "" if value is None else str(value)
    return dict(sorted(result.items(), key=lambda item: item[0].lower()))


def _docker_stats_snapshot(container_names):
    """Read one no-stream stats snapshot for the requested containers."""
    names = [
        str(name or "").strip()
        for name in (container_names or [])
        if str(name or "").strip()
    ]
    if not names:
        return {}

    rc, out, _ = run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *names],
        20,
    )
    if rc != 0:
        return {}

    result = {}
    for line in str(out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        result[name] = {
            "cpu_percent": row.get("CPUPerc"),
            "memory_usage": row.get("MemUsage"),
            "memory_percent": row.get("MemPerc"),
            "network_io": row.get("NetIO"),
            "block_io": row.get("BlockIO"),
            "pids": row.get("PIDs"),
        }
    return result


def _docker_mount_size_snapshot(containers):
    """Measure mounted data folders from inside each running target container.

    Bind-mount host paths are normally not mounted into Update Monitor itself,
    so measuring the host Source path directly would often be impossible.
    The mounted data is, however, visible at Mount.Destination inside the
    application container. Use a read-only `du -sk` there, only when the user
    opens Docker information. Large folders are bounded by a timeout.

    Returns a dict keyed by (container_name, destination).
    """
    jobs = []
    for container in containers or []:
        if not isinstance(container, dict):
            continue
        name = str(container.get("Name") or "").lstrip("/").strip()
        running = bool((container.get("State") or {}).get("Running"))
        if not name:
            continue
        for mount in container.get("Mounts") or []:
            if not isinstance(mount, dict):
                continue
            destination = str(mount.get("Destination") or "").strip()
            if not destination:
                continue
            jobs.append((name, destination, running))

    if not jobs:
        return {}

    # Deduplicate without changing visible mount order.
    unique_jobs = list(dict.fromkeys(jobs))

    def measure(job):
        name, destination, running = job
        if not running:
            return job, {"size_bytes": None, "size_status": "not_running"}

        commands = [
            ["docker", "exec", "-u", "0", name, "du", "-sk", "--", destination],
            ["docker", "exec", "-u", "0", name, "du", "-sk", destination],
        ]
        last_error = None
        for cmd in commands:
            rc, out, err = run(cmd, 20)
            if rc == 124:
                return job, {"size_bytes": None, "size_status": "timeout"}
            if rc != 0:
                last_error = err or out
                continue

            first = str(out or "").strip().split()
            if not first:
                last_error = "No du output"
                continue
            try:
                kib = int(first[0])
            except (TypeError, ValueError):
                last_error = "Invalid du output"
                continue

            return job, {
                "size_bytes": max(0, kib) * 1024,
                "size_status": "ok",
            }

        return job, {
            "size_bytes": None,
            "size_status": "unavailable",
            "size_error": str(last_error or "")[:240] or None,
        }

    result = {}
    worker_count = min(4, len(unique_jobs))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(measure, job) for job in unique_jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                job, value = future.result()
            except Exception:
                continue
            name, destination, _running = job
            result[(name, destination)] = value

    return result


def _docker_info_container(container, stats=None, mount_sizes=None):
    config = container.get("Config") or {}
    state = container.get("State") or {}
    host = container.get("HostConfig") or {}
    network_settings = container.get("NetworkSettings") or {}
    restart = host.get("RestartPolicy") or {}
    health = state.get("Health") or {}
    stats = stats or {}

    networks = []
    for network_name, network in (network_settings.get("Networks") or {}).items():
        if not isinstance(network, dict):
            continue
        networks.append({
            "name": str(network_name),
            "ip_address": network.get("IPAddress"),
            "global_ipv6_address": network.get("GlobalIPv6Address"),
            "gateway": network.get("Gateway"),
            "ipv6_gateway": network.get("IPv6Gateway"),
            "mac_address": network.get("MacAddress"),
        })

    mount_sizes = mount_sizes or {}
    mounts = []
    mounted_data_size_bytes = 0
    mounted_data_size_count = 0
    container_name = str(container.get("Name") or "").lstrip("/").strip()

    for mount in container.get("Mounts") or []:
        if not isinstance(mount, dict):
            continue
        destination = str(mount.get("Destination") or "").strip()
        size_info = dict(mount_sizes.get((container_name, destination)) or {})
        size_bytes = size_info.get("size_bytes")
        if isinstance(size_bytes, int) and size_bytes >= 0:
            mounted_data_size_bytes += size_bytes
            mounted_data_size_count += 1

        mounts.append({
            "type": mount.get("Type"),
            "name": mount.get("Name"),
            "source": mount.get("Source"),
            "destination": mount.get("Destination"),
            "rw": mount.get("RW"),
            "propagation": mount.get("Propagation"),
            "size_bytes": size_bytes,
            "size_status": size_info.get("size_status") or "unknown",
        })

    devices = []
    for device in host.get("Devices") or []:
        if not isinstance(device, dict):
            continue
        devices.append({
            "host_path": device.get("PathOnHost"),
            "container_path": device.get("PathInContainer"),
            "permissions": device.get("CgroupPermissions"),
        })

    memory_limit = int(host.get("Memory") or 0)
    nano_cpus = int(host.get("NanoCpus") or 0)
    cpu_quota = int(host.get("CpuQuota") or 0)
    cpu_period = int(host.get("CpuPeriod") or 0)
    cpu_limit = None
    if nano_cpus > 0:
        cpu_limit = nano_cpus / 1_000_000_000
    elif cpu_quota > 0 and cpu_period > 0:
        cpu_limit = cpu_quota / cpu_period

    return {
        "id": container.get("Id"),
        "name": str(container.get("Name") or "").lstrip("/"),
        "created_at": container.get("Created"),
        "image_ref": config.get("Image"),
        "image_id": container.get("Image"),
        "platform": container.get("Platform"),
        "state": {
            "status": state.get("Status"),
            "running": state.get("Running"),
            "paused": state.get("Paused"),
            "restarting": state.get("Restarting"),
            "dead": state.get("Dead"),
            "oom_killed": state.get("OOMKilled"),
            "pid": state.get("Pid"),
            "exit_code": state.get("ExitCode"),
            "error": state.get("Error"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "health_status": health.get("Status"),
            "health_failing_streak": health.get("FailingStreak"),
        },
        "restart_policy": {
            "name": restart.get("Name") or "no",
            "maximum_retry_count": restart.get("MaximumRetryCount"),
            "restart_count": container.get("RestartCount"),
        },
        "resources": {
            "cpu_limit_cores": cpu_limit,
            "cpu_shares": host.get("CpuShares"),
            "memory_limit_bytes": memory_limit if memory_limit > 0 else None,
            "memory_swap_bytes": host.get("MemorySwap"),
            "pids_limit": host.get("PidsLimit"),
            "shm_size_bytes": host.get("ShmSize"),
        },
        "security": {
            "user": config.get("User"),
            "privileged": host.get("Privileged"),
            "read_only_rootfs": host.get("ReadonlyRootfs"),
            "cap_add": host.get("CapAdd") or [],
            "cap_drop": host.get("CapDrop") or [],
            "security_opt": host.get("SecurityOpt") or [],
            "devices": devices,
        },
        "process": {
            "entrypoint": config.get("Entrypoint"),
            "cmd": config.get("Cmd"),
            "working_dir": config.get("WorkingDir"),
            "hostname": config.get("Hostname"),
            "tty": config.get("Tty"),
        },
        "network": {
            "mode": host.get("NetworkMode"),
            "ports": container_published_ports(container),
            "networks": networks,
        },
        "storage": {
            "mounts": mounts,
            "mounted_data_size_bytes": (
                mounted_data_size_bytes if mounted_data_size_count > 0 else None
            ),
            "mounted_data_size_count": mounted_data_size_count,
        },
        "logging": {
            "driver": (host.get("LogConfig") or {}).get("Type"),
        },
        "compose": {
            "project": (config.get("Labels") or {}).get("com.docker.compose.project"),
            "service": (config.get("Labels") or {}).get("com.docker.compose.service"),
            "container_number": (config.get("Labels") or {}).get("com.docker.compose.container-number"),
            "working_dir": (config.get("Labels") or {}).get("com.docker.compose.project.working_dir"),
            "config_files": (config.get("Labels") or {}).get("com.docker.compose.project.config_files"),
        },
        "labels": _docker_info_safe_labels(config.get("Labels") or {}),
        # Never expose environment variable values in this UI. They can contain
        # passwords, API keys and tokens.
        "environment_variable_count": len(config.get("Env") or []),
        "stats": stats,
    }


def _docker_info_image(image):
    config = image.get("Config") or {}
    healthcheck = config.get("Healthcheck") or {}
    return {
        "id": image.get("Id"),
        "created_at": image.get("Created"),
        "architecture": image.get("Architecture"),
        "os": image.get("Os"),
        "variant": image.get("Variant"),
        "size_bytes": image.get("Size"),
        "virtual_size_bytes": image.get("VirtualSize"),
        "repo_tags": image.get("RepoTags") or [],
        "repo_digests": image.get("RepoDigests") or [],
        "user": config.get("User"),
        "working_dir": config.get("WorkingDir"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "exposed_ports": sorted((config.get("ExposedPorts") or {}).keys()),
        "volumes": sorted((config.get("Volumes") or {}).keys()),
        "healthcheck": {
            "test": healthcheck.get("Test"),
            "interval": healthcheck.get("Interval"),
            "timeout": healthcheck.get("Timeout"),
            "start_period": healthcheck.get("StartPeriod"),
            "retries": healthcheck.get("Retries"),
        } if healthcheck else None,
        "labels": _docker_info_safe_labels(config.get("Labels") or {}),
        "environment_variable_count": len(config.get("Env") or []),
    }


def docker_information(stack_key, image_ref):
    app_item = find_scanned_app(stack_key)
    if not app_item:
        raise RuntimeError("App not found in current scan")

    wanted_ref = str(image_ref or "").strip()
    item = None
    for candidate in app_item.get("items") or []:
        if str(candidate.get("image_ref") or "").strip() == wanted_ref:
            item = candidate
            break
    if item is None:
        raise RuntimeError("Docker image not found in current scan")

    names = [
        str(container.get("name") or "").strip()
        for container in item.get("containers") or []
        if str(container.get("name") or "").strip()
    ]

    inspected = []
    if names:
        rc, out, err = run(["docker", "inspect", *names], 30)
        if rc != 0:
            raise RuntimeError(err or out or "docker inspect failed")
        try:
            payload = json.loads(out or "[]")
            if isinstance(payload, list):
                inspected = payload
        except Exception as exc:
            raise RuntimeError("Could not parse docker inspect output") from exc

    stats = _docker_stats_snapshot(names)
    mount_sizes = _docker_mount_size_snapshot(inspected)
    containers = [
        _docker_info_container(
            container,
            stats.get(str(container.get("Name") or "").lstrip("/")),
            mount_sizes,
        )
        for container in inspected
        if isinstance(container, dict)
    ]

    image = None
    image_id = None
    if inspected:
        image_id = str((inspected[0] or {}).get("Image") or "").strip()
    if not image_id:
        image_id = wanted_ref

    if image_id:
        rc, out, _ = run(["docker", "image", "inspect", image_id], 25)
        if rc == 0:
            try:
                payload = json.loads(out or "[]")
                if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                    image = _docker_info_image(payload[0])
            except Exception:
                image = None

    return {
        "checked_at": utc_now(),
        "docker_engine_version": docker_version(),
        "stack_key": stack_key,
        "app_name": app_item.get("name"),
        "image_ref": wanted_ref,
        "update_status": item.get("status"),
        "installed_tag": item.get("tag"),
        "resolved_installed_tag": item.get("resolved_installed_tag"),
        "available_tag": item.get("newer_tag") or item.get("resolved_available_tag"),
        "local_digests": item.get("local_digests") or [],
        "remote_digest": item.get("remote_digest"),
        "containers": containers,
        "image": image,
        "environment_values_hidden": True,
    }


@app.get("/api/docker-info")
def docker_info(stack_key: str, image_ref: str, request: Request):
    require_auth(request)
    try:
        return docker_information(stack_key, image_ref)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status")
def status(request: Request):
    require_auth(request)
    refresh_scan_policy_fields()
    with scan_lock:
        scan_payload = copy.deepcopy(scan_state)
    annotate_self_protection(scan_payload)
    annotate_version_confirmations(scan_payload)
    return {"version": VERSION, "docker_version": docker_version(), "zimaos_app_management_detected": casaos_detected(), "settings": dict(settings), "github": github_token_public_status(), "backup_encryption": backup_encryption_public_status(), "project_links": {"cache_file": str(PROJECT_LINK_CACHE_FILE), "cached_repositories": len(PROJECT_LINK_CACHE)}, "resource_usage": container_resource_usage(), "service_runtime": {"started_at": SERVICE_STARTED_AT}, "automation": {"pending_post_scans": pending_app_scan_keys()}, "scan": scan_payload}


@app.post("/api/version-confirmation")
def version_confirmation_create(data: VersionConfirmationRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    try:
        confirmation = confirm_installed_version(data.stack_key, data.image_ref)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "confirmation": confirmation}


@app.get("/api/resources")
def resources(request: Request):
    require_auth(request)
    return {
        "resource_usage": container_resource_usage(),
        "service_runtime": {"started_at": SERVICE_STARTED_AT},
    }


@app.get("/api/action-progress")
def action_progress_status(stack_key: str, request: Request):
    require_auth(request)
    with message_bus_state_lock:
        bus_state = dict(message_bus_state)
    return {
        "stack_key": stack_key,
        "progress": action_progress_snapshot(stack_key),
        "message_bus": bus_state,
    }


@app.get("/api/runtime-status")
def runtime_status(request: Request):
    require_auth(request)
    runtime_apps, removed_stack_keys = live_app_runtime_status()
    return {
        "checked_at": utc_now(),
        "apps": runtime_apps,
        "removed_stack_keys": removed_stack_keys,
        "scan": live_scan_snapshot(),
        "actions": action_progress_public_snapshot(),
        "automation": {"pending_post_scans": pending_app_scan_keys()},
    }


@app.post("/api/app-uninstall")
def app_uninstall(data: AppUninstallRequest, request: Request):
    require_auth(request)

    if scan_state.get("state") == "scanning":
        raise HTTPException(
            status_code=409,
            detail="Wait until the current update check is finished",
        )

    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the current post-action verification scan is finished",
        )

    app_item = find_scanned_app(data.stack_key)
    if not app_item:
        raise HTTPException(
            status_code=404,
            detail="App not found in current scan",
        )

    reject_self_destructive_action(app_item, "uninstall")

    compose_project = str(
        app_item.get("compose_project") or ""
    ).strip()
    if not compose_project:
        raise HTTPException(
            status_code=400,
            detail="This app is not managed by ZimaOS Compose",
        )

    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another app update, uninstall or runtime action is already running",
        )

    if has_pending_app_scan():
        app_update_lock.release()
        raise HTTPException(
            status_code=409,
            detail="Wait until the current post-action verification scan is finished",
        )

    begin_action_progress(data.stack_key, "uninstall", app_item)
    try:
        casaos_uninstall_compose(
            compose_project,
            delete_config_folder=bool(data.delete_config_folder),
        )

        # Keep the request active until ZimaOS/Docker really removed the app.
        uninstall_state = wait_for_uninstall_complete(
            compose_project,
            timeout=180,
            progress_stack_key=data.stack_key,
        )
        finish_action_progress(data.stack_key, True)
    except RuntimeError as exc:
        finish_action_progress(data.stack_key, False, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        app_update_lock.release()

    remove_uninstalled_app_policy(data.stack_key)

    # Do NOT start a normal/full update check after uninstall. The operation was
    # already confirmed against ZimaOS/Docker; remove the old app locally from
    # the current snapshot and leave the configured scan interval untouched.
    remove_uninstalled_app_from_scan_snapshot(
        data.stack_key,
        compose_project,
    )

    return {
        "success": True,
        "app": app_item.get("name"),
        "compose_project": compose_project,
        "delete_config_folder": bool(data.delete_config_folder),
        "uninstall_state": uninstall_state,
    }


@app.post("/api/app-runtime")
def app_runtime(data: AppRuntimeRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()

    action = str(data.action or "").strip().lower()
    if action not in {"start", "stop"}:
        raise HTTPException(status_code=400, detail="Unsupported container action")

    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the current post-action verification scan is finished",
        )

    app_item = find_scanned_app(data.stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")

    if action == "stop":
        reject_self_destructive_action(app_item, "stop")

    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another app update or runtime action is already running",
        )

    if has_pending_app_scan():
        app_update_lock.release()
        raise HTTPException(
            status_code=409,
            detail="Wait until the current post-action verification scan is finished",
        )

    try:
        result = perform_runtime_action(app_item, action)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        app_update_lock.release()

    return {
        "success": True,
        "app": app_item.get("name"),
        **result,
    }


@app.post("/api/scan")
def scan(request: Request):
    require_auth(request)

    block_reason = full_scan_block_reason()
    if block_reason:
        raise HTTPException(
            status_code=409,
            detail=(
                "A Docker action or targeted verification is active. "
                "A full update check cannot start now."
            ),
        )
    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the post-action verification is finished",
        )

    started = start_scan(origin="manual-api")
    return {
        "started": started,
        "state": "scanning" if started else scan_state.get("state"),
    }


@app.post("/api/app-scan")
def app_scan(data: AppScanRequest, request: Request):
    """Run one manual update check for exactly one Docker app.

    This deliberately reuses the authoritative targeted scan path used after
    an update, but does not queue a full scan and does not change Docker.
    """
    require_auth(request)

    stack_key = str(data.stack_key or "").strip()
    if not stack_key:
        raise HTTPException(status_code=400, detail="App is required")
    if not find_scanned_app(stack_key):
        raise HTTPException(status_code=404, detail="App not found in current scan")

    # A queued post-action verification must keep priority. A manual per-app
    # check must never jump in front of an update verification or Docker action.
    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the post-action verification is finished",
        )
    if active_mutating_action_keys():
        raise HTTPException(
            status_code=409,
            detail="Wait until the current Docker action is finished",
        )

    started = start_app_scan(stack_key)
    if not started:
        raise HTTPException(
            status_code=409,
            detail="A scan or Docker action is already active",
        )

    return {
        "started": True,
        "state": "scanning",
        "scan_scope": "app",
        "scan_stack_key": stack_key,
    }


def _app_versions_unlocked(stack_key: str, request: Request):
    """
    Return selectable versions for any registry-backed Docker image.

    Source order:
      1. Docker/OCI registry tags (Docker Hub, GHCR, LSCR/LinuxServer, Quay,
         and other compatible registries)
      2. GitHub Releases/Tags only when the registry has no usable version tags

    This endpoint is intentionally independent of whether the currently
    configured image tag is "latest", a LinuxServer version tag, or strict
    semantic versioning.
    """
    require_auth(request)

    app_item = find_scanned_app(stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")

    fetched_selectable = {}
    fetched_newer = {}
    errors = []
    sources = {}
    version_items = 0
    resolved_project_urls = {}
    resolved_installed_versions = {}
    resolved_available_versions = {}

    with scan_lock:
        scan_platform = str(scan_state.get("platform") or "").strip()

    for item in app_item.get("items") or []:
        if item.get("update_policy") in {"digest_pinned", "local"}:
            continue

        image_ref = str(item.get("image_ref") or "").strip()
        if not image_ref:
            continue

        parsed = parse_image_ref(image_ref)
        current_tag = str(parsed.get("tag") or item.get("tag") or "").strip()
        repo_key = str(parsed.get("normalized_repo") or "").strip()

        if not repo_key:
            errors.append(f"Could not determine a registry for {image_ref}")
            continue

        version_items += 1

        registry_values = []
        registry_error = None
        try:
            registry_values, registry_error = registry_tags(
                repo_key,
                max_pages=10 if _is_linuxserver_repo(repo_key) else 5,
            )
        except Exception as exc:
            registry_error = str(exc)

        registry_values = registry_values or []
        if registry_error:
            errors.append(f"{image_ref}: {registry_error}")

        # Registry tags are the authoritative installable list.
        selectable = registry_selectable_version_tags(
            registry_values,
            repo_key=repo_key,
            limit=100,
            current_tag=current_tag,
        )

        project_url = app_github_project_url(app_item, image_ref)
        github_values = []
        github_source = None
        github_error = None

        # GitHub is supplemental for ordinary images. For LinuxServer, retain
        # the project link for the UI but never import upstream release tags into
        # the Docker build-version catalogue.
        if project_url:
            resolved_project_urls[image_ref] = project_url
            if not _is_linuxserver_repo(repo_key):
                try:
                    github_values, github_source, github_error = github_project_tags(project_url)
                except Exception as exc:
                    github_values = []
                    github_source = "github_error"
                    github_error = f"GitHub lookup failed: {str(exc)[:240]}"
                github_values = github_values or []
                if github_error:
                    errors.append(f"{project_url}: {github_error}")

        if github_values:
            selectable = merge_registry_and_github_selectable(
                selectable,
                github_values,
                registry_values,
                limit=100,
                repo_key=repo_key,
                current_tag=current_tag,
            )
            sources[image_ref] = (
                "registry+" + str(github_source or "github")
                if registry_values
                else str(github_source or "github")
            )
        elif selectable:
            sources[image_ref] = "registry"
        else:
            sources[image_ref] = "registry"

        resolved_installed = str(item.get("resolved_installed_tag") or "").strip()
        local_digests = list(item.get("local_digests") or [])
        if (
            _needs_installed_version_resolution(current_tag)
            and local_digests
            and selectable
        ):
            resolved_installed = (
                installed_version_cache_get(repo_key, local_digests, selectable)
                or resolve_installed_version_tag(
                    repo_key,
                    local_digests,
                    selectable,
                    scan_platform,
                )
                or resolved_installed
            )
        elif not resolved_installed and not _needs_installed_version_resolution(current_tag):
            resolved_installed = current_tag

        if resolved_installed:
            resolved_installed_versions[image_ref] = resolved_installed

        # If a rolling tag has a pending image update, resolve the concrete
        # release behind the already-known new remote digest. This makes the
        # main card and the interactive version picker agree without ever
        # guessing that the first catalogue entry must equal :latest.
        if (
            item.get("status") == "IMAGE_UPDATE"
            and _needs_installed_version_resolution(current_tag)
            and selectable
        ):
            remote_candidates = {
                str(value or "").strip().lower()
                for value in (item.get("remote_digest"), item.get("remote_platform_digest"))
                if str(value or "").strip()
            }
            if remote_candidates:
                resolved_available = resolve_installed_version_tag(
                    repo_key,
                    remote_candidates,
                    selectable,
                    scan_platform,
                    batch_size=4,
                    deadline_seconds=16.0,
                    cache_result=False,
                )
                if resolved_available:
                    resolved_available_versions[image_ref] = resolved_available

        # Existing update detection remains conservative/newer-only.
        if parse_version(current_tag):
            if registry_values:
                newer = compatible_newer_tags_relaxed_prefix(
                    current_tag,
                    registry_values,
                    limit=30,
                )
            else:
                newer = []
        else:
            newer = []

        fetched_selectable[image_ref] = selectable
        fetched_newer[image_ref] = newer

    if version_items < 1:
        raise HTTPException(
            status_code=400,
            detail="This app has no registry-backed image that can switch versions",
        )

    with scan_lock:
        current_app = None
        for candidate in scan_state.get("apps") or []:
            if candidate.get("stack_key") == stack_key:
                current_app = candidate
                break

        if current_app is not None:
            for current_item in current_app.get("items") or []:
                image_ref = str(current_item.get("image_ref") or "").strip()
                if image_ref not in fetched_selectable:
                    continue

                selectable = list(fetched_selectable.get(image_ref) or [])
                newer = list(fetched_newer.get(image_ref) or [])

                current_item["available_version_tags"] = selectable

                resolved_installed = str(
                    resolved_installed_versions.get(image_ref) or ""
                ).strip()
                if resolved_installed:
                    current_item["resolved_installed_tag"] = resolved_installed
                    for result_item in scan_state.get("results") or []:
                        if (
                            result_item.get("stack_key") == stack_key
                            and str(result_item.get("image_ref") or "").strip() == image_ref
                        ):
                            result_item["resolved_installed_tag"] = resolved_installed

                resolved_available = str(
                    resolved_available_versions.get(image_ref) or ""
                ).strip()
                is_rolling_image_update = bool(
                    current_item.get("status") == "IMAGE_UPDATE"
                    and _needs_installed_version_resolution(
                        str(current_item.get("tag") or "").strip()
                    )
                )
                if is_rolling_image_update:
                    # Never retain a previously resolved version when the new
                    # lookup cannot reproduce it. The UI may safely fall back to
                    # the corrected catalogue, but stale cross-channel data must
                    # not outrank that catalogue.
                    current_item["resolved_available_tag"] = resolved_available or None
                    if resolved_available:
                        current_item["available_version_resolution"] = {
                            "status": "resolved",
                            "source": "remote-digest",
                            "detail": resolved_available,
                            "checked_at": utc_now(),
                        }
                    else:
                        current_item.pop("available_version_resolution", None)

                    for result_item in scan_state.get("results") or []:
                        if (
                            result_item.get("stack_key") == stack_key
                            and str(result_item.get("image_ref") or "").strip() == image_ref
                        ):
                            result_item["resolved_available_tag"] = resolved_available or None
                            if resolved_available:
                                result_item["available_version_resolution"] = dict(
                                    current_item["available_version_resolution"]
                                )
                            else:
                                result_item.pop("available_version_resolution", None)

                # Do not overwrite scan-time update detection for tags the
                # generic parser cannot safely order.
                if parse_version(str(current_item.get("tag") or "").strip()):
                    current_item["newer_tags"] = newer
                    current_item["newer_tag"] = newer[0] if newer else None

                current_item["version_source"] = sources.get(image_ref)

                resolved_url = resolved_project_urls.get(image_ref)
                if resolved_url:
                    resolved_link = resolve_project_link(image_ref, metadata_url=resolved_url, metadata_source="github-version-resolution")
                    current_item["project_url"] = resolved_url
                    current_item["project_provider"] = (resolved_link or {}).get("provider") or "github"
                    current_item["project_url_source"] = (resolved_link or {}).get("source")
                    if not current_app.get("project_url"):
                        current_app["project_url"] = resolved_url
                        current_app["project_provider"] = current_item.get("project_provider")
                        current_app["project_url_source"] = current_item.get("project_url_source")

            apply_monitor_policy_fields(current_app)
            versions = list(current_app.get("policy_available_tags") or [])
            version_group = dict(current_app.get("version_group") or {})
            if (
                not versions
                and current_app.get("version_group_state") == "ambiguous"
            ):
                errors.append(
                    "Multiple independent version groups were detected; "
                    "no safe primary version group could be selected"
                )
            save_json(SCAN_FILE, scan_state)
        else:
            versions = []
            version_group = {}

    return {
        "success": True,
        "stack_key": stack_key,
        "versions": versions,
        "version_group": version_group,
        "errors": errors,
        "sources": sources,
        "project_urls": resolved_project_urls,
        "installed_versions": resolved_installed_versions,
    }


@app.get("/api/app-versions")
def app_versions(stack_key: str, request: Request):
    """Exclusive manual version lookup.

    Registry/GitHub version discovery reads and updates the same app/version
    state as scans and update installation. It therefore participates in the
    same operation gate instead of running as an unrelated parallel request.
    """
    require_auth(request)

    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Wait until the current scan or app operation is finished before checking versions",
        )

    try:
        if has_pending_app_scan():
            raise HTTPException(
                status_code=409,
                detail="Wait until the post-update verification scan is finished before checking versions",
            )
        return _app_versions_unlocked(stack_key, request)
    finally:
        app_update_lock.release()


@app.put("/api/app-policy")
def app_policy(data: AppPolicyRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()

    app_item = find_scanned_app(data.stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")

    mode = str(data.mode or "").strip().lower()
    if mode not in {"fixed", "upgrade", "follow", "notify"}:
        raise HTTPException(status_code=400, detail="Unsupported update policy")

    available_tags = policy_available_tags(app_item)
    target_tag = str(data.target_tag or "").strip() or None

    if mode in {"upgrade", "follow", "notify"} and not app_item.get("compose_project"):
        raise HTTPException(
            status_code=400,
            detail="ZimaOS Compose app ID is missing for this app",
        )

    if mode == "upgrade":
        if not target_tag:
            raise HTTPException(status_code=400, detail="Select a target version")
        if target_tag not in available_tags:
            raise HTTPException(
                status_code=400,
                detail="The selected version is not available in the current scan",
            )
    else:
        target_tag = None

    auto_time = _normalize_auto_time(data.auto_time)
    if str(data.auto_time or auto_time).strip() != auto_time:
        raise HTTPException(status_code=400, detail="Invalid automatic update time")

    auto_days = _normalize_auto_days(data.auto_days)
    requested_days = data.auto_days if isinstance(data.auto_days, list) else auto_days
    if any((not isinstance(day, int)) or day < 0 or day > 6 for day in requested_days):
        raise HTTPException(status_code=400, detail="Invalid automatic update weekdays")

    auto_timezone = _normalize_auto_timezone(data.auto_timezone)
    requested_timezone = str(data.auto_timezone or auto_timezone).strip()
    if requested_timezone and requested_timezone != auto_timezone:
        raise HTTPException(status_code=400, detail="Invalid automatic update timezone")

    auto_enabled = bool(data.auto_enabled)
    if auto_enabled and mode not in {"upgrade", "follow"}:
        raise HTTPException(
            status_code=400,
            detail="Automatic updates require 'Switch to a new version' or 'Follow tag / automatic'",
        )

    auto_backup_mode = _normalize_backup_mode(data.auto_backup_mode, "none")
    requested_backup_mode = str(data.auto_backup_mode or auto_backup_mode).strip().lower()
    if requested_backup_mode not in {"none", "quick", "full"}:
        raise HTTPException(status_code=400, detail="Invalid automatic backup mode")

    saved = save_monitor_policy(
        data.stack_key,
        mode,
        target_tag,
        auto_enabled=auto_enabled,
        auto_immediate=bool(data.auto_immediate),
        auto_time=auto_time,
        auto_days=auto_days,
        auto_timezone=auto_timezone,
        auto_backup_mode=auto_backup_mode,
    )

    with scan_lock:
        for current in scan_state.get("apps") or []:
            if current.get("stack_key") == data.stack_key:
                apply_monitor_policy_fields(current)
                break
        save_json(SCAN_FILE, scan_state)

    return {
        "success": True,
        "stack_key": data.stack_key,
        "policy": saved,
    }



@app.get("/api/app-backups")
def app_backups(stack_key: str, request: Request):
    require_auth(request)
    app_item = find_scanned_app(stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")
    return {
        "stack_key": stack_key,
        "backups": app_backup_list(app_item),
        "retention": backup_retention_public_status(stack_key),
        "encryption": backup_encryption_public_status(),
    }


@app.put("/api/backup-settings")
def backup_settings_update(data: BackupSettingsRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()

    retention = None
    max_per_app = None
    stack_key = str(data.stack_key or "").strip()

    if data.retention is not None:
        requested = str(data.retention or "").strip().lower()
        retention = _normalize_backup_retention(requested)
        if requested != retention or retention not in BACKUP_RETENTION_OPTIONS:
            raise HTTPException(status_code=400, detail="Unsupported backup retention")

    if data.max_per_app is not None:
        if not stack_key:
            raise HTTPException(status_code=400, detail="App is required for backup count")
        if not find_scanned_app(stack_key):
            raise HTTPException(status_code=404, detail="App not found in current scan")
        try:
            requested_max = int(data.max_per_app)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Unsupported backup count") from exc
        if requested_max not in BACKUP_MAX_PER_APP_OPTIONS:
            raise HTTPException(status_code=400, detail="Unsupported backup count")
        max_per_app = requested_max

    if retention is None and max_per_app is None:
        raise HTTPException(status_code=400, detail="No backup setting supplied")

    with settings_lock:
        if retention is not None:
            settings["backup_retention"] = retention
        if max_per_app is not None:
            per_app = _normalize_backup_max_per_app_map(
                settings.get("backup_max_per_app_by_stack")
            )
            per_app[stack_key] = max_per_app
            settings["backup_max_per_app_by_stack"] = per_app
        save_json(SETTINGS_FILE, settings)

    try:
        if retention is not None:
            _rewrite_backup_retention_metadata()
        cleanup_expired_backups()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not apply backup settings: {exc}",
        ) from exc

    return {
        "success": True,
        "retention": backup_retention_public_status(stack_key if max_per_app is not None else None),
        "settings": dict(settings),
    }


@app.delete("/api/app-backups")
def app_backup_delete(stack_key: str, backup_id: str, request: Request):
    require_auth(request)
    reject_mutation_during_scan()

    app_item = find_scanned_app(stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")

    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another app update, restore or uninstall is already running",
        )

    try:
        deleted_id = delete_app_backup(app_item, backup_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        app_update_lock.release()

    return {
        "success": True,
        "deleted_backup_id": deleted_id,
        "stack_key": stack_key,
        "backups": app_backup_list(app_item),
        "retention": backup_retention_public_status(),
    }


@app.post("/api/app-restore")
def app_restore(data: AppRestoreRequest, request: Request):
    require_auth(request)

    if scan_state.get("state") == "scanning":
        raise HTTPException(
            status_code=409,
            detail="Wait until the current update check is finished",
        )

    app_item = find_scanned_app(data.stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")
    if not app_item.get("compose_project"):
        raise HTTPException(status_code=400, detail="Backup restore requires a ZimaOS Compose app")
    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the current verification scan is finished",
        )
    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another app update, restore or uninstall is already running",
        )

    if has_pending_app_scan():
        app_update_lock.release()
        raise HTTPException(
            status_code=409,
            detail="Wait until the current verification scan is finished",
        )

    restore_error = None
    result = None
    begin_action_progress(data.stack_key, "restore", app_item)
    try:
        update_action_progress(data.stack_key, 1, determinate=True, phase="restore_prepare")
        result = restore_app_backup(
            app_item,
            backup_id=data.backup_id,
            stack_key=data.stack_key,
        )
        finish_action_progress(data.stack_key, True)
    except RuntimeError as exc:
        restore_error = exc
        finish_action_progress(data.stack_key, False, str(exc))
    finally:
        schedule_app_scan(data.stack_key)
        app_update_lock.release()

    if restore_error is not None:
        raise HTTPException(status_code=500, detail=str(restore_error)) from restore_error

    return {
        "success": True,
        "app": app_item.get("name"),
        **(result or {}),
    }


@app.put("/api/backup-encryption")
def backup_encryption_configure(data: BackupEncryptionRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    try:
        result = configure_backup_encryption(data.enabled, data.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "backup_encryption": result}


@app.post("/api/backup-encryption/unlock")
def backup_encryption_unlock(data: BackupUnlockRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    try:
        result = unlock_backup_encryption(data.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "backup_encryption": result}



def register_successful_image_source_switch(app_item, result):
    """Persist the selected source before the targeted follow-up scan."""
    if not isinstance(result, dict):
        return False

    stack_key = str((app_item or {}).get("stack_key") or "").strip()
    project = str(
        result.get("project")
        or (app_item or {}).get("compose_project")
        or ""
    ).strip()
    new_ref = str(result.get("new_image_ref") or "").strip()
    services = [
        str(value or "").strip()
        for value in (result.get("compose_services") or [])
        if str(value or "").strip()
    ]

    verification = result.get("target_verification") or {}
    verified_digests = []

    if isinstance(verification, dict):
        verified_digests = list(
            verification.get("expected_digests")
            or verification.get("local_digests")
            or []
        )

    if not project or not new_ref or not services:
        return False

    for service in services:
        remember_image_source_assignment(
            project,
            service,
            new_ref,
            stack_key=stack_key,
            verified_digests=verified_digests,
            source="verified-source-switch",
        )

    changed = False

    with scan_lock:
        results = list(scan_state.get("results") or [])

        for item in results:
            if not isinstance(item, dict):
                continue

            if str(item.get("stack_key") or "").strip() != stack_key:
                continue

            item_services = {
                str(container.get("compose_service") or "").strip()
                for container in (item.get("containers") or [])
                if isinstance(container, dict)
                and str(container.get("compose_service") or "").strip()
            }

            if not item_services.intersection(services):
                continue

            old_ref = str(item.get("image_ref") or "").strip()

            if old_ref != new_ref:
                item["previous_image_ref"] = old_ref or None
                item["image_ref"] = new_ref
                try:
                    item["tag"] = parse_image_ref(new_ref).get("tag")
                except Exception:
                    pass
                changed = True

            if verified_digests:
                normalized = sorted({
                    str(value or "").strip().lower()
                    for value in verified_digests
                    if str(value or "").strip()
                })
                item["local_digests"] = normalized
                item["remote_digest"] = normalized[0] if normalized else None
                item["remote_digest_short"] = (
                    normalized[0].replace("sha256:", "")[:12]
                    if normalized else None
                )
                item["post_update_verified"] = True

            item["source_change_pending_verification"] = True
            item["scan_warning"] = (
                "Image source changed successfully; targeted verification pending"
            )

        if changed:
            apps, app_summary = build_apps(results)
            scan_state["results"] = results
            scan_state["apps"] = apps
            scan_state["app_summary"] = app_summary
            scan_state["source_change_committed_at"] = utc_now()
            save_json(SCAN_FILE, scan_state)

    with IMAGE_SOURCE_DISCOVERY_CACHE_LOCK:
        IMAGE_SOURCE_DISCOVERY_CACHE.clear()

    return True



@app.get("/api/image-source-options")
def image_source_options(stack_key: str, request: Request):
    require_auth(request)
    app_item = find_scanned_app(stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")
    try:
        return image_source_options_for_app(app_item)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Image source discovery failed: {type(exc).__name__}: {str(exc)[:240]}",
        ) from exc


@app.post("/api/image-source-switch")
def image_source_switch(data: ImageSourceSwitchRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()

    app_item = find_scanned_app(data.stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")
    if not image_source_available_for_app(app_item):
        raise HTTPException(status_code=400, detail="No verified alternative image source is available")

    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the current verification scan is finished",
        )

    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another app update, restore or uninstall is already running",
        )

    if has_pending_app_scan():
        app_update_lock.release()
        raise HTTPException(
            status_code=409,
            detail="Wait until the current verification scan is finished",
        )

    backup_mode = _normalize_backup_mode(data.backup_mode, "full")
    if backup_mode not in {"quick", "full"}:
        app_update_lock.release()
        raise HTTPException(
            status_code=400,
            detail="Image source switching requires a quick or full backup",
        )

    switch_error = None
    result = None
    backup_result = None
    begin_action_progress(data.stack_key, "update", app_item)

    try:
        # Image-source progress is milestone based. Percentages represent
        # completed workflow stages, not guessed Docker download bytes.
        update_action_progress(
            data.stack_key,
            5,
            determinate=True,
            phase="backup",
        )
        backup_result = create_pre_update_backup(
            app_item,
            backup_mode,
            stack_key=data.stack_key,
        )
        update_action_progress(
            data.stack_key,
            15,
            determinate=True,
            phase="starting",
        )

        result = perform_image_source_switch(
            app_item,
            data.image_key,
            data.source_id,
            accept_warnings=bool(data.accept_warnings),
        )
        if isinstance(result, dict):
            result["backup"] = backup_result

        register_successful_image_source_switch(app_item, result)

        finish_action_progress(data.stack_key, True)
    except RuntimeError as exc:
        switch_error = exc
        finish_action_progress(data.stack_key, False, str(exc))
    finally:
        schedule_app_scan(
            data.stack_key,
            verification_result=result if switch_error is None else None,
        )
        app_update_lock.release()

    if switch_error is not None:
        raise HTTPException(status_code=500, detail=str(switch_error)) from switch_error

    return {
        "success": True,
        "app": app_item.get("name"),
        **(result or {}),
    }


@app.post("/api/app-update")
def app_update(data: AppUpdateRequest, request: Request):
    require_auth(request)

    if scan_state.get("state") == "scanning":
        raise HTTPException(status_code=409, detail="Wait until the current update check is finished")

    app_item = find_scanned_app(data.stack_key)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found in current scan")

    if not app_item.get("can_image_update") and not app_item.get("can_version_update"):
        raise HTTPException(
            status_code=400,
            detail="This update is currently blocked by its update policy",
        )

    if has_pending_app_scan():
        raise HTTPException(
            status_code=409,
            detail="Wait until the post-update verification scan is finished",
        )

    if not app_update_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another app update is already running")

    if has_pending_app_scan():
        app_update_lock.release()
        raise HTTPException(
            status_code=409,
            detail="Wait until the post-update verification scan is finished",
        )

    requested_backup_mode = str(data.backup_mode or "none").strip().lower()
    if requested_backup_mode not in {"none", "quick", "full"}:
        app_update_lock.release()
        raise HTTPException(status_code=400, detail="Invalid backup mode")
    backup_mode = _normalize_backup_mode(requested_backup_mode, "none")

    update_error = None
    result = None
    backup_result = None
    begin_action_progress(data.stack_key, "update", app_item)

    try:
        if backup_mode != "none":
            update_action_progress(data.stack_key, determinate=False, phase="backup")
            backup_result = create_pre_update_backup(
                app_item,
                backup_mode,
                stack_key=data.stack_key,
            )
            update_action_progress(data.stack_key, determinate=False, phase="starting")

        if app_item.get("can_version_update"):
            result = perform_version_update(app_item)
        else:
            result = perform_image_update(app_item)
        if isinstance(result, dict) and backup_result:
            result["backup"] = backup_result
        finish_action_progress(data.stack_key, True)
    except RuntimeError as exc:
        update_error = exc
        finish_action_progress(data.stack_key, False, str(exc))
    finally:
        # MANUAL UPDATE RULE:
        # Never start a full/interval scan here. Queue exactly one targeted scan
        # for this stack while the Docker-operation lock is still held. The
        # pending marker then blocks the 1/6/12/24-hour scheduler until this one
        # app verification has completed.
        schedule_app_scan(
            data.stack_key,
            verification_result=result if update_error is None else None,
        )
        app_update_lock.release()

    if update_error is not None:
        raise HTTPException(status_code=500, detail=str(update_error)) from update_error

    return {
        "success": True,
        "app": app_item.get("name"),
        **result,
    }


@app.put("/api/github-token")
def github_token_save(data: GithubTokenRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    try:
        token = _normalize_github_token(data.token)
        if not token:
            raise RuntimeError("Enter a GitHub token")
        test = test_github_token(token)
        save_github_token_secret(token)
        # Refill repository version data under the authenticated quota instead
        # of keeping entries originally populated through anonymous fallbacks.
        clear_github_version_cache()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "github": github_token_public_status(),
        "test": test,
    }


@app.post("/api/github-token/test")
def github_token_test(data: GithubTokenRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    try:
        supplied = _normalize_github_token(data.token)
        result = test_github_token(supplied if supplied else None)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "github": github_token_public_status(),
        "test": result,
    }


@app.delete("/api/github-token")
def github_token_delete(request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    remove_saved_github_token()
    # If UPDATE_MONITOR_GITHUB_TOKEN exists in Docker, that environment token
    # becomes active again automatically and cannot be removed from the UI.
    return {
        "success": True,
        "github": github_token_public_status(),
    }


@app.put("/api/settings")
def update_settings(data: SettingsRequest, request: Request):
    require_auth(request)
    reject_mutation_during_scan()
    allowed = {0, 3600, 21600, 43200, 86400}
    if data.scan_interval_seconds not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported scan interval")
    with settings_lock:
        settings["scan_interval_seconds"] = data.scan_interval_seconds
        save_json(SETTINGS_FILE, settings)
    return {"success": True, "settings": dict(settings)}
