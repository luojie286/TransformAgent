from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def sse_delta(text: str) -> bytes:
    payload = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@pytest.fixture
def http_endpoint():
    servers = []

    def start(respond):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, self.headers.get("Authorization"), payload))
                try:
                    respond(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}/v1/chat/completions", calls

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()
