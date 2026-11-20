import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from workbench.classification import with_classification


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    base_version TEXT NOT NULL,
    target_version TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    target_sha TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(repo, base_sha, target_sha, extractor_version)
);
CREATE TABLE IF NOT EXISTS operator_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id INTEGER NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    change_type TEXT NOT NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    diff TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL,
    confidence REAL NOT NULL,
    is_work_item INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(comparison_id, kind, name, path, change_type)
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id INTEGER NOT NULL UNIQUE REFERENCES operator_changes(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    generator TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    approved_at TEXT
);
CREATE TABLE IF NOT EXISTS jira_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id INTEGER NOT NULL UNIQUE REFERENCES operator_changes(id) ON DELETE CASCADE,
    issue_key TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id INTEGER REFERENCES comparisons(id) ON DELETE CASCADE,
    operator_id INTEGER REFERENCES operator_changes(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_comparison ON operator_changes(comparison_id);
CREATE INDEX IF NOT EXISTS idx_events_comparison ON events(comparison_id, id);
"""


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def dict_row(row):
        return dict(row) if row is not None else None

    def find_comparison(self, repo, base_sha, target_sha, extractor_version):
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM comparisons
                   WHERE repo=? AND base_sha=? AND target_sha=? AND extractor_version=?""",
                (repo, base_sha, target_sha, extractor_version),
            ).fetchone()
        return self.comparison_with_stats(row)

    def latest_comparison(self):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comparisons ORDER BY id DESC LIMIT 1").fetchone()
        return self.comparison_with_stats(row)

    def get_comparison(self, comparison_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comparisons WHERE id=?", (comparison_id,)).fetchone()
        return self.comparison_with_stats(row)

    def comparison_with_stats(self, row):
        if row is None:
            return None
        result = dict(row)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT o.*, d.id AS document_id, d.status AS document_status,
                          j.id AS jira_id
                   FROM operator_changes o
                   LEFT JOIN documents d ON d.operator_id=o.id
                   LEFT JOIN jira_issues j ON j.operator_id=o.id
                   WHERE o.comparison_id=?""",
                (row["id"],),
            ).fetchall()
        items = [with_classification(item) for item in rows]
        work_items = [item for item in items if item["is_default_work_item"]]
        result["stats"] = {
            "total": len(work_items),
            "added": sum(item["change_type"] == "added" for item in work_items),
            "modified": sum(item["change_type"] == "modified" for item in work_items),
            "removed": sum(item["change_type"] == "removed" for item in work_items),
            "call_sites": sum(item["scope"] == "call_site" for item in items),
            "excluded": sum(not item["is_default_work_item"] for item in items),
            "documents": sum(item["document_id"] is not None for item in work_items),
            "approved": sum(item["document_status"] == "approved" for item in work_items),
            "jiras": sum(item["jira_id"] is not None for item in work_items),
        }
        return result

    def create_comparison(self, repo, base_version, target_version, base_sha, target_sha, extractor_version):
        existing = self.find_comparison(repo, base_sha, target_sha, extractor_version)
        if existing:
            return existing, False
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO comparisons
                   (repo, base_version, target_version, base_sha, target_sha,
                    extractor_version, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'running', ?)""",
                (repo, base_version, target_version, base_sha, target_sha, extractor_version, utc_now()),
            )
            comparison_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO events (comparison_id, event_type, message, created_at) VALUES (?, ?, ?, ?)",
                (comparison_id, "analysis_started", f"开始分析 {base_version} → {target_version}", utc_now()),
            )
        return self.get_comparison(comparison_id), True

    def finish_comparison(self, comparison_id, status, error=None):
        with self.connect() as conn:
            conn.execute(
                "UPDATE comparisons SET status=?, error=?, completed_at=? WHERE id=?",
                (status, error, utc_now(), comparison_id),
            )
            conn.execute(
                "INSERT INTO events (comparison_id, event_type, message, created_at) VALUES (?, ?, ?, ?)",
                (comparison_id, f"analysis_{status}", error or "版本分析完成", utc_now()),
            )

    def replace_operators(self, comparison_id, operators):
        with self.connect() as conn:
            conn.execute("DELETE FROM operator_changes WHERE comparison_id=?", (comparison_id,))
            for item in operators:
                conn.execute(
                    """INSERT OR IGNORE INTO operator_changes
                       (comparison_id, name, kind, change_type, path, line, source, diff,
                        evidence, confidence, is_work_item, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        comparison_id,
                        item["name"],
                        item["kind"],
                        item["change_type"],
                        item["path"],
                        item["line"],
                        item.get("source", ""),
                        item.get("diff", ""),
                        item["evidence"],
                        item["confidence"],
                        item["is_work_item"],
                        json.dumps(item.get("metadata", {}), ensure_ascii=False),
                    ),
                )
        return self.list_operators(comparison_id)

    def list_operators(self, comparison_id):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT o.*, d.id AS document_id, d.status AS document_status,
                          j.issue_key, j.url AS jira_url, j.status AS jira_status
                   FROM operator_changes o
                   LEFT JOIN documents d ON d.operator_id=o.id
                   LEFT JOIN jira_issues j ON j.operator_id=o.id
                   WHERE o.comparison_id=?
                   ORDER BY o.is_work_item DESC, o.change_type, o.kind, o.name, o.path""",
                (comparison_id,),
            ).fetchall()
        return [with_classification(row) for row in rows]

    def get_operator(self, operator_id):
        with self.connect() as conn:
            row = conn.execute(
                """SELECT o.*, c.repo, c.base_version, c.target_version, c.base_sha, c.target_sha,
                          d.id AS document_id, d.content AS document_content,
                          d.generator AS document_generator, d.status AS document_status,
                          d.created_at AS document_created_at, d.approved_at,
                          j.issue_key, j.url AS jira_url, j.status AS jira_status
                   FROM operator_changes o
                   JOIN comparisons c ON c.id=o.comparison_id
                   LEFT JOIN documents d ON d.operator_id=o.id
                   LEFT JOIN jira_issues j ON j.operator_id=o.id
                   WHERE o.id=?""",
                (operator_id,),
            ).fetchone()
        if row is None:
            return None
        result = with_classification(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        revision = result["target_sha"] if result["change_type"] != "removed" else result["base_sha"]
        if result["line"]:
            result["source_url"] = f"{result['repo']}/blob/{revision}/{result['path']}#L{result['line']}"
        else:
            result["source_url"] = f"{result['repo']}/blob/{revision}/{result['path']}"
        return result

    def create_document(self, operator_id, content, generator, source_hash):
        with self.connect() as conn:
            existing = conn.execute("SELECT id FROM documents WHERE operator_id=?", (operator_id,)).fetchone()
            if existing:
                return existing["id"], False
            cursor = conn.execute(
                """INSERT INTO documents (operator_id, content, generator, source_hash, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (operator_id, content, generator, source_hash, utc_now()),
            )
            document_id = cursor.lastrowid
            operator = conn.execute("SELECT comparison_id FROM operator_changes WHERE id=?", (operator_id,)).fetchone()
            conn.execute(
                """INSERT INTO events (comparison_id, operator_id, event_type, message, created_at)
                   VALUES (?, ?, 'document_created', ?, ?)""",
                (operator["comparison_id"], operator_id, f"文档草案已生成（{generator}）", utc_now()),
            )
        return document_id, True

    def approve_document(self, operator_id):
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM documents WHERE operator_id=?", (operator_id,)).fetchone()
            if row is None:
                raise ValueError("请先生成文档")
            conn.execute(
                "UPDATE documents SET status='approved', approved_at=? WHERE operator_id=?",
                (utc_now(), operator_id),
            )
            operator = conn.execute("SELECT comparison_id FROM operator_changes WHERE id=?", (operator_id,)).fetchone()
            conn.execute(
                """INSERT INTO events (comparison_id, operator_id, event_type, message, created_at)
                   VALUES (?, ?, 'document_approved', '文档已审核', ?)""",
                (operator["comparison_id"], operator_id, utc_now()),
            )

    def create_jira(self, operator_id):
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM jira_issues WHERE operator_id=?", (operator_id,)).fetchone()
            if existing:
                return dict(existing), False
            document = conn.execute("SELECT status FROM documents WHERE operator_id=?", (operator_id,)).fetchone()
            if document is None or document["status"] != "approved":
                raise ValueError("文档审核后才能创建 Jira")
            issue_key = f"DEMO-{operator_id:04d}"
            cursor = conn.execute(
                """INSERT INTO jira_issues (operator_id, issue_key, url, status, created_at)
                   VALUES (?, ?, ?, 'To Do (Mock)', ?)""",
                (operator_id, issue_key, f"/jira/{issue_key}", utc_now()),
            )
            operator = conn.execute("SELECT comparison_id FROM operator_changes WHERE id=?", (operator_id,)).fetchone()
            conn.execute(
                """INSERT INTO events (comparison_id, operator_id, event_type, message, created_at)
                   VALUES (?, ?, 'jira_created', ?, ?)""",
                (operator["comparison_id"], operator_id, f"Mock Jira {issue_key} 已创建", utc_now()),
            )
            row = conn.execute("SELECT * FROM jira_issues WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row), True

    def list_events(self, comparison_id, operator_id=None, limit=100):
        query = """SELECT e.*, o.name AS operator_name FROM events e
                   LEFT JOIN operator_changes o ON o.id=e.operator_id
                   WHERE e.comparison_id=?"""
        params = [comparison_id]
        if operator_id is not None:
            query += " AND (e.operator_id=? OR e.operator_id IS NULL)"
            params.append(operator_id)
        query += " ORDER BY e.id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_jira(self, issue_key):
        with self.connect() as conn:
            row = conn.execute(
                """SELECT j.*, o.name, o.kind, o.change_type, o.path,
                          c.base_version, c.target_version, d.content AS document_content
                   FROM jira_issues j
                   JOIN operator_changes o ON o.id=j.operator_id
                   JOIN comparisons c ON c.id=o.comparison_id
                   LEFT JOIN documents d ON d.operator_id=o.id
                   WHERE j.issue_key=?""",
                (issue_key,),
            ).fetchone()
        return self.dict_row(row)


    def list_documents(self):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT d.id, d.operator_id, d.generator, d.status, d.created_at, d.approved_at,
                          o.name, o.kind, o.path, o.change_type, o.comparison_id,
                          c.base_version, c.target_version, j.issue_key, j.url AS jira_url
                   FROM documents d
                   JOIN operator_changes o ON o.id=d.operator_id
                   JOIN comparisons c ON c.id=o.comparison_id
                   LEFT JOIN jira_issues j ON j.operator_id=o.id
                   ORDER BY d.id DESC"""
            ).fetchall()
        return [with_classification(row) for row in rows]

    def get_document(self, document_id):
        with self.connect() as conn:
            row = conn.execute(
                """SELECT d.*, o.name, o.kind, o.path, o.line, o.change_type, o.comparison_id,
                          c.base_version, c.target_version, c.repo, c.target_sha,
                          j.issue_key, j.url AS jira_url
                   FROM documents d
                   JOIN operator_changes o ON o.id=d.operator_id
                   JOIN comparisons c ON c.id=o.comparison_id
                   LEFT JOIN jira_issues j ON j.operator_id=o.id
                   WHERE d.id=?""",
                (document_id,),
            ).fetchone()
        return with_classification(row) if row else None

    def list_jiras(self):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT j.*, o.id AS operator_id, o.name, o.kind, o.path, o.change_type,
                          c.base_version, c.target_version, d.id AS document_id,
                          d.status AS document_status
                   FROM jira_issues j
                   JOIN operator_changes o ON o.id=j.operator_id
                   JOIN comparisons c ON c.id=o.comparison_id
                   LEFT JOIN documents d ON d.operator_id=o.id
                   ORDER BY j.id DESC"""
            ).fetchall()
        return [with_classification(row) for row in rows]

    def all_events(self, limit=500):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT e.*, o.name AS operator_name, o.kind, o.path,
                          c.base_version, c.target_version
                   FROM events e
                   LEFT JOIN operator_changes o ON o.id=e.operator_id
                   LEFT JOIN comparisons c ON c.id=e.comparison_id
                   ORDER BY e.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
