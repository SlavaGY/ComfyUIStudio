"""Recover a real embedded renderer after a crash and reuse the same port."""

import os
import subprocess
import sys

PROBE = r'''
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(['lm-browser-test'])
from comfyui_studio.launcher.ui.lm_studio_browser_page import LmStudioBrowserPage

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_GET(self):
        body = b"""<html><body data-test="LM Studio recovery test"><script>window.marked={use:()=>{}};window.t=s=>s;
            /*COPY_HELPER*/</script><button
            style="position:absolute;left:0;top:0;width:160px;height:50px"
            onclick="copyStudioText('Studio clipboard test')">Copy</button></body></html>"""
        helper = Path('comfyui_studio/lm_studio/static/js/richtext.js').read_bytes()
        body = body.replace(b'/*COPY_HELPER*/', helper)
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class Browser(LmStudioBrowserPage):
    PROFILE_DIR = str(Path(sys.argv[1]) / 'storage')
    PROFILE_NAME = Path(sys.argv[1]).name

def until(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, 'Recovery timed out'
        app.processEvents()
        time.sleep(0.01)

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
page = Browser()
page.resize(900, 600)
page.show()
try:
    page.load(server.server_port)
    until(lambda: page._page_ready)
    page._page.runJavaScript("document.querySelector('button').click()")
    until(lambda: app.clipboard().text() == 'Studio clipboard test')
    old_page = page._page
    # Terminate only the renderer PID obtained from this isolated test page.
    renderer = psutil.Process(page._page.renderProcessPid())
    renderer.kill()
    renderer.wait(timeout=5)
    until(page.needs_reload)
    assert page._retry_btn.isVisible()
    page._retry_btn.click()
    until(lambda: page._page_ready)
    assert page._page is not old_page
    assert not page.needs_reload() and not page._retry_btn.isVisible()
    page.unload()
    assert page.needs_reload()
    page.load(server.server_port)
    until(lambda: page._page_ready)
    result = []
    page._page.runJavaScript('document.body.dataset.test', result.append)
    until(lambda: bool(result))
    assert result == ['LM Studio recovery test'], result
    print('Renderer crash, retry and same-port restart passed')
finally:
    page.unload()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    server.shutdown()
    server.server_close()
'''


def test_renderer_crash_and_restart(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
