import argparse
import json
from collections import Counter
from pathlib import Path

from workbench.analyzer import EXTRACTOR_VERSION, REPO_URL, VllmRepository, analyze
from workbench.database import Database


ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".data"
BASE_VERSION = "v0.30.0"
TARGET_VERSION = "v0.34.0"


def build_services():
    database = Database(DATA / "workbench.db")
    repository = VllmRepository(DATA / "vllm.git")
    return database, repository


def run_analysis(database, repository, base_version=BASE_VERSION, target_version=TARGET_VERSION, verbose=False):
    repository.ensure(base_version, target_version)
    base_sha = repository.revision(base_version)
    target_sha = repository.revision(target_version)
    existing = database.find_comparison(REPO_URL, base_sha, target_sha, EXTRACTOR_VERSION)
    if existing and existing["status"] == "completed":
        return existing, True
    if existing and existing["status"] == "running":
        return existing, False
    comparison, created = database.create_comparison(
        REPO_URL, base_version, target_version, base_sha, target_sha, EXTRACTOR_VERSION
    )
    if not created:
        return comparison, True

    def progress(current, total):
        if verbose:
            print(f"扫描文件 {current}/{total}", flush=True)

    try:
        operators = analyze(repository, base_sha, target_sha, progress)
        database.replace_operators(comparison["id"], operators)
        database.finish_comparison(comparison["id"], "completed")
    except Exception as error:
        database.finish_comparison(comparison["id"], "failed", str(error))
        raise
    return database.get_comparison(comparison["id"]), False


def main():
    parser = argparse.ArgumentParser(description="vLLM 算子适配工作台 Demo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="分析并缓存两个 vLLM 版本")
    analyze_parser.add_argument("--base", default=BASE_VERSION)
    analyze_parser.add_argument("--target", default=TARGET_VERSION)
    serve_parser = subparsers.add_parser("serve", help="启动本地 Web 工作台")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    database, repository = build_services()
    if args.command == "analyze":
        comparison, reused = run_analysis(database, repository, args.base, args.target, verbose=True)
        summary = {
            "comparison_id": comparison["id"],
            "status": comparison["status"],
            "reused": reused,
            "stats": comparison.get("stats", {}),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        from workbench.server import serve
        serve(database, repository, args.host, args.port)


if __name__ == "__main__":
    main()
