# SPDX-License-Identifier: LGPL-2.1-or-later
"""Anthracite bridge: expose the checked FreeCAD executor to external agents.

A background thread accepts loopback connections and reads line-delimited JSON
commands. Execution is deferred to the FreeCAD GUI thread through a repeating QTimer
drain so every document change and native transaction stays on the thread FreeCAD
requires. The transaction, recompute, validation, and rollback semantics live in
``AnthraciteExecutor``; this module only transports commands, serializes execution
across clients, and records an inspectable operation log.

Agents reach the bridge through the ``anthracite`` command-line client. Connection
details are published to a discovery file; the bridge itself speaks no agent protocol.
"""

from __future__ import annotations

import collections
import datetime
import json
import os
import socket
import socketserver
import threading
import time
import uuid
from typing import Any, Callable

import FreeCAD as App

import AnthraciteExecutor
import AnthraciteInspect

try:
    import FreeCADGui as Gui  # noqa: F401
except Exception:  # Console build: the bridge is not started without a GUI.
    Gui = None

try:
    from PySide6 import QtCore, QtWidgets
except Exception:  # pragma: no cover - FreeCAD's GUI always ships Qt.
    QtCore = None
    QtWidgets = None

BRIDGE_VERSION = 1
TIMER_INTERVAL_MS = 40
CONTEXT_INTERVAL_MS = 500
IDLE_TIMEOUT_SECONDS = 1800
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_LOG_LINES = 2000
MAX_CODE_BYTES = 20000
MAX_TEXT_BYTES = 8000


def _state_directory() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, ".anthracite")


def discovery_path() -> str:
    return os.path.join(_state_directory(), "bridge.json")


def operations_path() -> str:
    return os.path.join(_state_directory(), "operations.ndjson")


def cli_path() -> str:
    """Absolute path to the command-line client bundled beside this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "anthracite")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bounded(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… truncated {len(text) - limit} characters"


def _write_private(path: str, data: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(data)


class _Pending:
    """One queued request awaiting execution on the GUI thread."""

    def __init__(self, request_type: str, params: dict):
        self.request_type = request_type
        self.params = params
        self.event = threading.Event()
        self.result: dict | None = None
        self.images: list[str] = []
        self.message: str | None = None
        self.stages: list[str] = []
        self.document: str | None = None
        self.document_token: str | None = None

    def report_stage(self, stage: str) -> None:
        self.stages.append(str(stage))

    def finish(self, result: dict, images: list[str], document: str | None, token: str | None) -> None:
        self.result = result
        self.images = images
        self.document = document
        self.document_token = token
        self.event.set()

    def fail(self, message: str) -> None:
        self.message = message
        self.event.set()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        bridge: "_Bridge" = self.server.bridge  # type: ignore[attr-defined]
        bridge.serve(self.request)


class _Bridge:
    def __init__(self) -> None:
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._timer = None
        self._token = ""
        self._host = "127.0.0.1"
        self._port = 0
        self._started_at: str | None = None
        self._queue: collections.deque[_Pending] = collections.deque()
        self._queue_lock = threading.Lock()
        self._context = {
            "document": None,
            "documentToken": None,
            "revision": 0,
            "documents": [],
            "pendingTransaction": {"open": False, "name": None, "workbench": None},
        }
        self._clients = 0
        self._busy = False
        self._blocked = False
        self._blocked_reason = ""
        self._ring: collections.deque = collections.deque(maxlen=200)
        self._subscribers: list[Callable[[dict], None]] = []
        self._log_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self, host: str = "127.0.0.1", port: int = 0) -> dict:
        if self._server is not None:
            return self.status()
        if Gui is None or QtWidgets is None:
            raise RuntimeError("The Anthracite bridge requires the FreeCAD GUI.")
        server = _Server((host, port), _Handler)
        server.bridge = self  # type: ignore[attr-defined]
        self._server = server
        self._host, self._port = server.server_address[0], server.server_address[1]
        self._token = uuid.uuid4().hex
        self._started_at = _utc_now()
        self._load_history()
        self._thread = threading.Thread(target=server.serve_forever, name="anthracite-bridge", daemon=True)
        self._thread.start()
        self._publish_discovery()
        application = QtWidgets.QApplication.instance()
        if application is None:
            raise RuntimeError("The Anthracite bridge requires a running Qt application.")
        self._timer = QtCore.QTimer(application)
        self._timer.setInterval(TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self._drain)
        self._timer.start()
        self._context_timer = QtCore.QTimer(application)
        self._context_timer.setInterval(CONTEXT_INTERVAL_MS)
        self._context_timer.timeout.connect(self._refresh_context)
        self._context_timer.start()
        self._refresh_context()
        return self.status()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = None
        self._context_timer = None
        if self._context_timer is not None:
            self._context_timer.stop()
            self._context_timer = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None
        with self._queue_lock:
            for item in self._queue:
                item.fail("The Anthracite bridge stopped before this call ran.")
            self._queue.clear()
        try:
            os.remove(discovery_path())
        except OSError:
            pass

    def is_running(self) -> bool:
        return self._server is not None

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "version": BRIDGE_VERSION,
            "host": self._host,
            "port": self._port,
            "startedAt": self._started_at,
            "document": self._context["document"],
            "activeDocument": self._context["document"],
            "documents": list(self._context["documents"]),
            "documentToken": self._context["documentToken"],
            "revision": self._context["revision"],
            "busy": self._busy,
            "blocked": self._blocked,
            "blockedReason": self._blocked_reason,
            "queued": len(self._queue),
            "clients": self._clients,
            "pendingTransaction": dict(self._context.get("pendingTransaction")
                                       or {"open": False, "name": None, "workbench": None}),
        }

    # -- discovery ---------------------------------------------------------

    def _publish_discovery(self) -> None:
        payload = {
            "version": BRIDGE_VERSION,
            "host": self._host,
            "port": self._port,
            "token": self._token,
            "pid": os.getpid(),
            "startedAt": self._started_at,
            "command": os.path.abspath(__file__),
        }
        _write_private(discovery_path(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    # -- connections -------------------------------------------------------

    def serve(self, connection: socket.socket) -> None:
        connection.settimeout(IDLE_TIMEOUT_SECONDS)
        reader = connection.makefile("rb")
        writer = connection.makefile("wb")
        authenticated = False
        try:
            token = reader.readline(4096)
            if token.strip().decode("utf-8", "replace") != self._token:
                return
            authenticated = True
            self._clients += 1
            while True:
                raw = reader.readline(MAX_REQUEST_BYTES)
                if not raw:
                    break
                if len(raw) >= MAX_REQUEST_BYTES and not raw.endswith(b"\n"):
                    self._write(writer, {"status": "error", "message": "Request exceeds the size limit."})
                    break
                try:
                    request = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._write(writer, {"status": "error", "message": "Request is not valid JSON."})
                    continue
                self._write(writer, self._dispatch(request))
        except (TimeoutError, OSError):
            pass
        finally:
            if authenticated:
                self._clients -= 1
            for stream in (reader, writer):
                try:
                    stream.close()
                except OSError:
                    pass
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _write(writer, response: dict) -> None:
        try:
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            writer.flush()
        except OSError:
            pass

    def _dispatch(self, request: dict) -> dict:
        request_id = request.get("id")
        request_type = request.get("type")
        params = request.get("params") or {}
        if request_type in ("status", "ping"):
            return {"id": request_id, "status": "ok", **self.status()}
        if request_type != "execute":
            return {"id": request_id, "status": "error", "message": f"Unknown command: {request_type!r}"}
        if not isinstance(params.get("code"), str):
            return {"id": request_id, "status": "error", "message": "The execute command needs string 'code'."}
        if self._server is None:
            return {"id": request_id, "status": "error", "message": "The Anthracite bridge is not running."}
        pending = _Pending(request_type, params)
        with self._queue_lock:
            self._queue.append(pending)
        pending.event.wait()
        if pending.message is not None:
            return {"id": request_id, "status": "error", "message": pending.message}
        return {
            "id": request_id,
            "status": "ok",
            "result": pending.result or {},
            "images": pending.images,
            "document": getattr(pending, "document", None),
            "documentToken": getattr(pending, "document_token", None),
        }

    # -- GUI-thread execution ---------------------------------------------

    def _interaction_block(self) -> str:
        """Why a queued call must wait: a modal dialog or a held mouse button."""
        application = QtWidgets.QApplication.instance()
        if application is None:
            return ""
        modal = application.activeModalWidget()
        if modal is not None:
            return f"modal:{type(modal).__name__}"
        if application.mouseButtons() != QtCore.Qt.MouseButton.NoButton:
            return "mouse"
        return ""

    def _drain(self) -> None:
        while True:
            with self._queue_lock:
                if not self._queue:
                    self._blocked = False
                    self._blocked_reason = ""
                    return
                pending = self._queue[0]
            reason = self._interaction_block()
            if reason:
                self._blocked = True
                self._blocked_reason = reason
                return
            with self._queue_lock:
                self._queue.popleft()
            self._run(pending)

    def _run(self, pending: _Pending) -> None:
        self._busy = True
        self._blocked = False
        started = time.monotonic()
        operation_id = uuid.uuid4().hex[:12]
        code = pending.params.get("code", "")
        client = str(pending.params.get("client") or "agent")
        result: dict = {}
        images: list[str] = []
        document_name: str | None = None
        document_token: str | None = None
        try:
            document = App.ActiveDocument
            document_name = document.Name if document is not None else None
            if document is not None:
                document_token = AnthraciteInspect.document_token(document)
            expected_token = pending.params.get("documentToken")
            if document is not None and expected_token and expected_token != document_token:
                result = {
                    "ok": False,
                    "conflict": True,
                    "notExecuted": True,
                    "revision": self._context["revision"],
                    "error": {
                        "type": "DocumentChanged",
                        "message": "The active FreeCAD document changed since the last observation; inspect before editing.",
                    },
                }
            else:
                revision = pending.params.get("revision")
                expected_revision = int(revision) if isinstance(revision, int) else None
                payload = json.loads(
                    AnthraciteExecutor.execute_json(code, expected_revision, pending.report_stage)
                )
                images = [image for image in payload.pop("images", []) if isinstance(image, str)]
                result = payload
        except Exception as error:  # A bridge fault must never strand the caller.
            result = {"ok": False, "notExecuted": True,
                      "error": {"type": type(error).__name__, "message": str(error)}}
        finally:
            self._refresh_context()
            document = App.ActiveDocument
            document_name = document.Name if document is not None else None
            document_token = self._context["documentToken"]
            self._busy = False
            pending.finish(result, images, document_name, document_token)
            self._record(operation_id, client, code, result, images, started, pending.stages)

    def _refresh_context(self) -> None:
        """Keep the cached context live; status is served off the GUI thread.

        Without this the bridge reports whatever document the last call ran against,
        which is wrong as soon as the user switches or closes documents.
        """
        document = App.ActiveDocument
        token = None
        if document is not None:
            try:
                token = AnthraciteInspect.document_token(document)
            except Exception:
                token = None
        try:
            names = AnthraciteExecutor.open_document_names()
        except Exception:
            names = []
        pending = {"open": False, "name": None, "workbench": None}
        if document is not None:
            try:
                pending = AnthraciteExecutor.pending_transaction(document)
            except Exception:
                pending = {
                    "open": bool(getattr(document, "HasPendingTransaction", False)),
                    "name": None,
                    "workbench": None,
                }
        self._context = {
            "document": document.Name if document is not None else None,
            "documentToken": token,
            "revision": AnthraciteExecutor.revision(document),
            "documents": names,
            "pendingTransaction": pending,
        }

    # -- operation log -----------------------------------------------------

    def _record(self, operation_id, client, code, result, images, started, stages) -> None:
        record = {
            "id": operation_id,
            "timestamp": _utc_now(),
            "client": client,
            "status": self._status_of(result),
            "document": self._context["document"],
            "revisionBefore": result.get("revisionBefore"),
            "revisionAfter": result.get("revision"),
            "label": (result.get("action") or {}).get("label") if isinstance(result.get("action"), dict) else None,
            "durationMs": round((time.monotonic() - started) * 1000),
            "stages": stages,
            "code": _bounded(code, MAX_CODE_BYTES),
            "stdout": _bounded(result.get("stdout", ""), MAX_TEXT_BYTES),
            "stderr": _bounded(result.get("stderr", ""), MAX_TEXT_BYTES),
            "error": result.get("error"),
            "conflict": bool(result.get("conflict")),
            "requiresInspection": bool(result.get("requiresInspection")),
            "imageCount": len(images),
        }
        with self._log_lock:
            self._ring.append(record)
            self._append_log(record)
        # Images travel only with the live event: the persisted record keeps just a
        # count so the operation log never carries base64 payloads.
        self._notify({"kind": "operation", "operation": record, "images": images})

    @staticmethod
    def _status_of(result: dict) -> str:
        if result.get("readOnly"):
            return "readOnly"
        if result.get("ok"):
            return "committed" if result.get("action") else "unchanged"
        if result.get("conflict") or result.get("notExecuted"):
            return "rejected"
        if result.get("requiresInspection"):
            return "unknown"
        if result.get("rolledBack"):
            return "rolled_back"
        return "failed"

    def _append_log(self, record: dict) -> None:
        path = operations_path()
        try:
            directory = os.path.dirname(path)
            os.makedirs(directory, mode=0o700, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
                self._rotate_log(path)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    @staticmethod
    def _rotate_log(path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-MAX_LOG_LINES:]
            temporary = f"{path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
            os.replace(temporary, path)
        except OSError:
            pass

    def _load_history(self) -> None:
        try:
            with open(operations_path(), "r", encoding="utf-8", errors="replace") as handle:
                for line in handle.readlines()[-200:]:
                    try:
                        self._ring.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass

    # -- observers ---------------------------------------------------------

    def recent(self, limit: int = 50) -> list[dict]:
        return list(self._ring)[-limit:]

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _notify(self, event: dict) -> None:
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:
                pass


_bridge = _Bridge()


def start(host: str = "127.0.0.1", port: int = 0) -> dict:
    """Start the bridge server and GUI-thread drain; safe to call repeatedly."""

    return _bridge.start(host, port)


def stop() -> None:
    _bridge.stop()


def status() -> dict:
    return _bridge.status()


def recent(limit: int = 50) -> list[dict]:
    return _bridge.recent(limit)


def subscribe(callback: Callable[[dict], None]) -> None:
    _bridge.subscribe(callback)


def unsubscribe(callback: Callable[[dict], None]) -> None:
    _bridge.unsubscribe(callback)
