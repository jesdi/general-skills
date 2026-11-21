"""Fake pr-exam host for babysit-pr tests: `POST /api/exam`, as in agent-ops
specs/pr-exam-host/design.md "Route contract". Answers every POST with `answer` (200) and records
each request as {"path", "body"} in `requests`. An `answer` of None drops the connection unanswered,
as a host that is down.

    with FakeHost({"state": "open", ...}) as host:   # host.url is the PR_EXAM_HOST value
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeHost:
    def __init__(self, answer=None):
        self.answer = {"repo": "jesdi/demo", "pr": 58, "url": "https://box.example.ts.net/exam/jesdi/demo/58",
                       "state": "open", "head_sha": "head1", "debt": False,
                       "detail": "attempt 1, 5 questions", "pending": True, **(answer or {})}
        self.requests = []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                host.requests.append({"path": self.path, "body": json.loads(body or b"null")})
                answer = host.answer
                if answer is None:
                    return
                data = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
