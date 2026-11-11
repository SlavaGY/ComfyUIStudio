"""Benchmark a read-only backup of a real PromptVault library (no prompt export).

Run from the repository with python -m tools.benchmark_promptvault. The source
database is opened read-only; schema changes and timing queries use the snapshot.
Keep the same snapshot for before/after runs. Directory scans are read-only too.
"""

import argparse
import json
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from comfyui_studio.promptvault.core.directory_index import DirectoryIndex
from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.core.repository import GenerationRepository
from comfyui_studio.promptvault.core.sort_options import SortMode


def measure(action, repeats=7):
    action()  # warm filesystem/SQLite caches
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return {"median_ms": round(statistics.median(samples), 3), "samples_ms": [round(x, 3) for x in samples]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.source.resolve() == args.snapshot.resolve():
        parser.error("Snapshot must differ from source")
    if args.output.resolve() in {args.source.resolve(), args.snapshot.resolve()}:
        parser.error("Report must differ from both databases")
    if not args.snapshot.exists() or args.snapshot.stat().st_size == 0:
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(args.snapshot)) as destination:
                source.backup(destination)
    repository = GenerationRepository(args.snapshot)
    conn = repository._conn
    try:
        folders = conn.execute("SELECT folder, COUNT(*) FROM generations GROUP BY folder ORDER BY COUNT(*) DESC").fetchall()
        folder = next((path for path, _ in folders if path and Path(path).is_dir()), None)
        if not folder:
            raise RuntimeError("No indexed directory is accessible")
        total = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        model = conn.execute("SELECT model FROM generations GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
        lora = conn.execute("SELECT filename FROM loras GROUP BY filename ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        count = repository.count_generations(folder)
        scenarios = {
            "count": lambda: repository.count_filtered(folder, FilterOptions()),
            "first_100": lambda: repository.load_filtered_page(folder, FilterOptions(), SortMode.NEWEST, limit=100),
            "deep_100": lambda: repository.load_filtered_page(folder, FilterOptions(), SortMode.NEWEST, offset=max(0, count - 100), limit=100),
            "model_100": lambda: repository.load_filtered_page(folder, FilterOptions(model=model), SortMode.NEWEST, limit=100),
            "search_100": lambda: repository.load_filtered_page(folder, FilterOptions(search="girl"), SortMode.NEWEST, limit=100),
            "available_models": lambda: repository.available_models(folder),
            "statistics": repository.get_statistics,
        }
        if lora:
            scenarios["lora_100"] = lambda: repository.load_filtered_page(folder, FilterOptions(loras=[lora[0]]), SortMode.NEWEST, limit=100)
        report = {"rows": total, "folder_rows": count, "folder": folder, "queries": {}}
        for name, action in scenarios.items():
            result = measure(action)
            queries = []
            conn.set_trace_callback(queries.append)
            action()
            conn.set_trace_callback(None)
            result["plans"] = [
                [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]
                for query in queries if query.lstrip().upper().startswith("SELECT")
            ]
            report["queries"][name] = result
        inventory = DirectoryIndex(folder)
        started = time.perf_counter()
        initial = inventory.scan()
        report["scan"] = {"initial_ms": round((time.perf_counter() - started) * 1000, 3), "files": len(initial.files), "directories": len(initial.directories), "idle": measure(inventory.scan)}
        original = Path.stat
        calls = []

        def stat(path, *positional, **keywords):
            calls.append(path)
            return original(path, *positional, **keywords)

        with patch.object(Path, "stat", stat):
            inventory.scan()
        report["scan"]["idle_stat_calls"] = len(calls)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
