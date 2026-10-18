"""Холодный старт и смена UI одного origin в настоящем QtWebEngine.

Отдельный процесс изолирует Chromium и его QApplication от прочих Qt-тестов.
Локальный сервер отдаёт HTML и ES-модуль, имитируя загрузку Studio/Gradio.
"""

import os
import subprocess
import sys

import pytest

PROBE = r'''
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(['rvc-browser-test'])

from comfyui_studio.launcher.ui.rvc_browser_page import RvcBrowserPage

root = Path(sys.argv[1])
first = sys.argv[2]
mode = first
requests = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        requests.append((mode, self.path))
        if self.path == '/ui.js':
            body = f'document.body.dataset.interface = {mode!r};'.encode()
            content_type = 'text/javascript'
        else:
            body = b'<html><body><script type="module" src="/ui.js"></script></body></html>'
            content_type = 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=31536000')
        self.end_headers()
        self.wfile.write(body)

class Browser(RvcBrowserPage):
    PROFILE_DIR = str(root / 'storage')
    PROFILE_NAME = root.name

    def _on_profile_created(self, profile):
        profile.setCachePath(str(root / 'http-cache'))
        super()._on_profile_created(profile)

def until(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, 'Browser did not load the interface'
        app.processEvents()
        time.sleep(0.01)

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
page = Browser()
page.resize(900, 600)
page.show()
try:
    for mode in [first, 'gradio' if first == 'studio' else 'studio', first]:
        page.load(server.server_port)
        until(lambda: page._page_ready)
        result = []
        page._page.runJavaScript('document.body.dataset.interface', result.append)
        until(lambda: bool(result))
        assert result == [mode], (mode, result, requests)
        assert page._browser_stack.currentWidget() is page.view
        assert (mode, '/ui.js') in requests
        page.unload()
    print('cold start and both mode switches passed:', first)
finally:
    page.unload()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    server.shutdown()
    server.server_close()
'''


@pytest.mark.parametrize("first_mode", ["studio", "gradio"])
def test_cold_start_and_switching_interfaces(tmp_path, first_mode):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        env.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " --disable-gpu"
    ).strip()
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(tmp_path), first_mode],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=75, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
