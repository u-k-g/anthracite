# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bridge smoke test, launched by tests/runtests.nu under the FreeCAD GUI.

Drives the loopback bridge exactly as an external agent would: authenticate with the
discovery token, query status, reject a mutation with no active document, then commit
a real edit and confirm it reached the operation history.
"""
import json
import os
import select
import socket
import time
import traceback

MARKER = "ANTHRACITE_BRIDGE_SMOKE_OK"


def main():
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run this test through just test with an isolated profile")

    import FreeCAD as App
    from PySide6 import QtWidgets

    import AnthraciteBridge

    application = QtWidgets.QApplication.instance()
    status = AnthraciteBridge.start()
    assert status["running"] is True, status

    with open(AnthraciteBridge.discovery_path(), encoding="utf-8") as handle:
        discovery = json.load(handle)

    rejected = socket.create_connection((discovery["host"], discovery["port"]), timeout=10)
    rejected.sendall(b"not-the-token\n")
    rejected.settimeout(5)
    assert rejected.recv(1) == b"", "a wrong token must be rejected"
    rejected.close()

    client = socket.create_connection((discovery["host"], discovery["port"]), timeout=30)
    client.sendall((discovery["token"] + "\n").encode("utf-8"))
    reader = client.makefile("rb")

    def call(request):
        client.sendall((json.dumps(request) + "\n").encode("utf-8"))
        deadline = time.time() + 30
        while time.time() < deadline:
            readable, _, _ = select.select([client], [], [], 0.05)
            if readable:
                return json.loads(reader.readline())
            application.processEvents()
        raise TimeoutError("the bridge did not answer")

    ping = call({"id": 1, "type": "status"})
    assert ping["status"] == "ok" and ping["running"] is True, ping

    for document in list(App.listDocuments()):
        App.closeDocument(document.Name)
    if App.ActiveDocument is None:
        before = set(App.listDocuments())
        empty = call({"id": 2, "type": "execute", "params": {"code": "doc.addObject('Part::Box', 'Box')"}})
        assert empty["result"]["ok"] is False, empty
        created = set(App.listDocuments()) - before
        assert not created, f"the bridge must never create a document: {created}"

    document = App.newDocument("AnthraciteBridgeSmoke")
    created = call({
        "id": 3,
        "type": "execute",
        "params": {"code": "cad.action('Create smoke box')\ndoc.addObject('Part::Box', 'Box')"},
    })
    result = created["result"]
    assert result["ok"] is True, result
    assert document.getObject("Box") is not None, "the edit did not commit"
    assert result["revision"] >= 1, result

    history = AnthraciteBridge.recent(20)
    assert any(entry.get("status") == "committed" and entry.get("document") == document.Name for entry in history), history

    # The CLI is the agent-facing interface: same bridge, JSON with shot paths instead
    # of base64. It runs in a child process, so pump events while it waits on the GUI thread.
    import subprocess
    cli = os.path.join(os.path.dirname(AnthraciteBridge.__file__), "anthracite")
    # Inside FreeCAD, sys.executable is the FreeCAD binary, not a Python interpreter.
    interpreter = os.environ.get("ANTHRACITE_PYTHON", "python3")
    program = (
        "cad.action('CLI resize')\n"
        "doc.getObject('Box').Length = 25\n"
        "cad.render(view='front', width=200, height=150)"
    )
    child = subprocess.Popen([interpreter, cli, "exec", program],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while child.poll() is None and time.time() < deadline:
        application.processEvents()
        time.sleep(0.05)
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, f"cli failed: {stderr}"
    payload = json.loads(stdout)
    assert payload["ok"] is True, payload
    assert "images" not in payload, "the CLI must not print base64 images"
    shots = payload.get("shots") or []
    assert shots, payload
    assert all(os.path.isfile(shot["path"]) for shot in shots), shots
    assert abs(document.getObject("Box").Length.Value - 25.0) < 1e-6, "the CLI edit did not apply"

    client.close()
    AnthraciteBridge.stop()
    App.closeDocument(document.Name)
    # Write straight to the pipe: FreeCAD buffers its Python stdout and only flushes it
    # during the Qt teardown that crashes on this platform.
    os.write(1, (MARKER + "\n").encode("utf-8"))
    os._exit(0)


try:
    main()
except BaseException:
    traceback.print_exc()
    os._exit(1)
