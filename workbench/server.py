import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import run_analysis
from workbench.documents import generate_document


STATIC = Path(__file__).resolve().parent.parent / "static"


class Workbench:
    def __init__(self, database, repository):
        self.database = database
        self.repository = repository
        self.thread = None
        self.lock = threading.Lock()

    def start_analysis(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self.analyze, daemon=True)
            self.thread.start()

    def analyze(self):
        try:
            run_analysis(self.database, self.repository)
        except Exception as error:
            print(f"Analysis failed: {error}", flush=True)


class Handler(BaseHTTPRequestHandler):
    workbench = None

    def json_response(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def html_response(self, content, status=200, content_type="text/html; charset=utf-8"):
        body = content.encode() if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 1024 * 1024:
            raise ValueError("请求过大")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            database = self.workbench.database
            if path == "/":
                self.html_response((STATIC / "index.html").read_bytes())
                return
            if path == "/static/app.js":
                self.html_response((STATIC / "app.js").read_bytes(), content_type="text/javascript; charset=utf-8")
                return
            if path == "/static/styles.css":
                self.html_response((STATIC / "styles.css").read_bytes(), content_type="text/css; charset=utf-8")
                return
            if path == "/api/comparison":
                comparison = database.latest_comparison()
                self.json_response({"comparison": comparison, "initializing": comparison is None})
                return
            if path == "/api/operators":
                comparison_id = int(parse_qs(parsed.query)["comparison_id"][0])
                self.json_response({"operators": database.list_operators(comparison_id)})
                return
            if path.startswith("/api/operators/"):
                operator_id = int(path.rsplit("/", 1)[1])
                operator = database.get_operator(operator_id)
                if operator is None:
                    self.json_response({"error": "算子不存在"}, 404)
                    return
                operator["events"] = database.list_events(operator["comparison_id"], operator_id)
                self.json_response({"operator": operator})
                return
            if path == "/api/events":
                comparison_id = int(parse_qs(parsed.query)["comparison_id"][0])
                self.json_response({"events": database.list_events(comparison_id)})
                return
            if path == "/api/events/all":
                self.json_response({"events": database.all_events()})
                return
            if path == "/api/documents":
                self.json_response({"documents": database.list_documents()})
                return
            if path == "/api/jiras":
                self.json_response({"jiras": database.list_jiras()})
                return
            if path.startswith("/api/documents/"):
                parts = path.strip("/").split("/")
                document = database.get_document(int(parts[2]))
                if document is None:
                    self.json_response({"error": "文档不存在"}, 404)
                    return
                if len(parts) == 4 and parts[3] == "download":
                    self.html_response(document["content"], content_type="text/markdown; charset=utf-8")
                else:
                    self.json_response({"document": document})
                return
            if path.startswith("/jira/"): 
                issue = database.get_jira(path.rsplit("/", 1)[1])
                if issue is None:
                    self.html_response("Jira not found", 404, "text/plain; charset=utf-8")
                    return
                self.html_response(mock_jira_page(issue))
                return
            self.json_response({"error": "Not found"}, 404)
        except Exception as error:
            self.json_response({"error": str(error)}, 500)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            database = self.workbench.database
            if path == "/api/comparisons":
                self.workbench.start_analysis()
                self.json_response({"comparison": database.latest_comparison()}, 202)
                return
            if path == "/api/documents/batch":
                ids = self.body().get("operator_ids", [])
                results = []
                for operator_id in ids[:100]:
                    try:
                        operator, created = generate_document(database, operator_id, self.workbench.repository)
                        results.append({"operator_id": operator_id, "ok": True, "created": created})
                    except Exception as error:
                        results.append({"operator_id": operator_id, "ok": False, "error": str(error)})
                self.json_response({"results": results})
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "operators"]:
                operator_id = int(parts[2])
                action = parts[3]
                if action == "document":
                    operator, created = generate_document(database, operator_id, self.workbench.repository)
                    self.json_response({"operator": operator, "created": created})
                    return
                if action == "approve":
                    database.approve_document(operator_id)
                    self.json_response({"operator": database.get_operator(operator_id)})
                    return
                if action == "jira":
                    issue, created = database.create_jira(operator_id)
                    self.json_response({"issue": issue, "created": created})
                    return
            self.json_response({"error": "Not found"}, 404)
        except ValueError as error:
            self.json_response({"error": str(error)}, 400)
        except Exception as error:
            self.json_response({"error": str(error)}, 500)

    def log_message(self, format, *args):
        print(f"{self.address_string()} {format % args}", flush=True)


def mock_jira_page(issue):
    name = html.escape(issue["name"])
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>{html.escape(issue['issue_key'])} · Mock Jira</title>
<style>body{{font:16px system-ui;max-width:900px;margin:60px auto;padding:0 24px;color:#172033}}
a{{color:#4f46e5}}code{{background:#f1f5f9;padding:3px 6px}}.card{{border:1px solid #dbe3ef;border-radius:16px;padding:28px}}
.badge{{background:#fff7ed;color:#c2410c;padding:5px 10px;border-radius:20px}}</style></head>
<body><a href='/'>← 返回算子工作台</a><div class='card'><span class='badge'>Mock Jira，不是真实工单</span>
<h1>{html.escape(issue['issue_key'])}：适配 {name}</h1><p><b>状态：</b>{html.escape(issue['status'])}</p>
<p><b>版本：</b>{html.escape(issue['base_version'])} → {html.escape(issue['target_version'])}</p>
<p><b>源码：</b><code>{html.escape(issue['path'])}</code></p><h2>任务说明</h2>
<p>完成 {name} 在目标国产芯片后端的适配、正确性验证和性能对照。详细依据见已审核的关联文档。</p>
<p>后续可在此处接入内部 Jira API，并同步负责人、状态和真实 URL。</p></div></body></html>"""


def serve(database, repository, host, port):
    WorkbenchClass = type("BoundWorkbench", (Workbench,), {})
    workbench = WorkbenchClass(database, repository)
    handler = type("BoundHandler", (Handler,), {"workbench": workbench})
    workbench.start_analysis()
    server = ThreadingHTTPServer((host, port), handler)
    print(f"算子工作台已启动：http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
