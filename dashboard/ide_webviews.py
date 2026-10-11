"""An isolated browser origin for IDE webviews, with no Apex API routes."""
from __future__ import annotations

import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request


class WebviewGateway:
    def __init__(self, sid: int):
        self.sid = sid
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.port = self.socket.getsockname()[1]
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.api_route(f'/code/ide/{sid}/webview/{{path:path}}', methods=['GET', 'HEAD', 'OPTIONS'])
        async def webview(path: str, request: Request):
            from dashboard.code_ide import proxy
            request.state.isolated_webview = True
            return await proxy(sid, 'webview/' + path, request)

        self.server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.socket]),
                                       name=f'code-webview-{sid}', daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if not self.server.started:
            self.stop()
            raise RuntimeError('The IDE webview gateway could not start.')

    def stop(self):
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        self.socket.close()
