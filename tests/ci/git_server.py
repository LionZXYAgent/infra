"""Small authenticated, fetch-only smart HTTP Git server for disposable tests."""
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlsplit


class GitServer:
    def __init__(self, repositories: dict[str, tuple[Path, str, str]],
                 host: str = '127.0.0.1', port: int = 0):
        self.repositories = repositories
        self.host = host
        self.port = port

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(30)

            def log_message(self, *args):
                pass  # URLs, headers, and backend errors must not become diagnostics.

            def reject(self, status):
                self.send_response(status)
                if status == 401:
                    self.send_header('WWW-Authenticate', 'Basic realm="CI Git"')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_GET(self):
                self.serve()

            def do_POST(self):
                self.serve()

            def serve(self):
                parsed = urlsplit(self.path)
                pieces = parsed.path.lstrip('/').split('/', 1)
                if len(pieces) != 2 or pieces[0] not in owner.repositories:
                    return self.reject(404)
                name, suffix = pieces
                allowed = (self.command == 'GET' and suffix == 'info/refs'
                           and parsed.query == 'service=git-upload-pack') or (
                           self.command == 'POST' and suffix == 'git-upload-pack'
                           and not parsed.query)
                if not allowed:
                    return self.reject(403)
                repo, user, password = owner.repositories[name]
                expected = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
                if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
                    return self.reject(401)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    return self.reject(400)
                if self.headers.get('Transfer-Encoding') or not 0 <= length <= 16 * 1024 * 1024:
                    return self.reject(413)
                # PATH_TRANSLATED maps only a known repository and whitelisted suffix.
                # Do not pass an inherited credential/config environment to the backend.
                env = {'PATH': os.environ['PATH'], 'GIT_CONFIG_NOSYSTEM': '1',
                       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_HTTP_EXPORT_ALL': '1',
                       'PATH_TRANSLATED': str(repo.resolve()) + '/' + suffix,
                       'REQUEST_METHOD': self.command, 'QUERY_STRING': parsed.query,
                       'CONTENT_TYPE': self.headers.get('Content-Type', ''),
                       'CONTENT_LENGTH': str(length)}
                try:
                    result = subprocess.run(['git', '-c', 'http.receivepack=false', 'http-backend'],
                                            input=self.rfile.read(length), env=env,
                                            capture_output=True, timeout=30)
                    if result.returncode:
                        return self.reject(500)
                    headers, body = result.stdout.split(b'\r\n\r\n', 1)
                    fields = [line.decode().split(':', 1) for line in headers.split(b'\r\n')]
                    status = next((int(v.strip().split()[0]) for k, v in fields if k == 'Status'), 200)
                    self.send_response(status)
                    for key, value in fields:
                        if key.lower() in {'content-type', 'cache-control', 'expires', 'pragma'}:
                            self.send_header(key, value.strip())
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (subprocess.TimeoutExpired, ValueError, OSError):
                    self.reject(500)

        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass

        self.httpd = QuietServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.timeout = 5
        self.base_url = f'http://{self.host}:{self.httpd.server_port}'
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
