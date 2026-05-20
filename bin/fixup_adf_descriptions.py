#!/usr/bin/env python3
"""One-shot fixup: re-render ADF dict-repr descriptions as markdown.

Tasks migrated from `tickets` carry their JIRA description in the
`description` column. When the source JIRA was Cloud (which returns
ADF, a JSON tree), the previous `common.tickets._normalise_issue`
stored `str(adf_dict)` -- a Python dict repr -- instead of either
proper JSON or rendered prose.

This script walks every task whose description looks like a Python
dict literal (`"{'type': 'doc', ...}"`), `ast.literal_eval`s it back
into a real dict, and rewrites the column using `_render_adf` from
`common.tickets`. Idempotent: tasks whose description isn't a dict
literal are skipped.

Run from repo root:
    ~/venv312/bin/python bin/fixup_adf_descriptions.py [--dry-run]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "src"))

from common.tickets import _render_adf  # noqa: E402


def open_conn(db_path: str):
    try:
        from pysqlite3 import dbapi2 as sqlite3
    except ImportError:
        import sqlite3  # type: ignore[no-redef]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def looks_like_adf_repr(s: str) -> bool:
    # Quick filter so we don't `literal_eval` every plain-text row.
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    return s.startswith("{'type':") or s.startswith("{\"type\":")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--db", default=str(REPO_ROOT / "data" / "eva.db"),
        help="path to eva.db",
    )
    args = p.parse_args()

    conn = open_conn(args.db)
    rows = conn.execute(
        "SELECT task_id, description FROM tasks "
        "WHERE description LIKE '{%' "
        "  AND description != ''"
    ).fetchall()
    rewritten = skipped = errors = 0
    for r in rows:
        desc = r["description"] or ""
        if not looks_like_adf_repr(desc):
            skipped += 1
            continue
        try:
            adf = ast.literal_eval(desc)
        except (ValueError, SyntaxError) as e:
            errors += 1
            print(f"  parse failed for {r['task_id']}: {e}", file=sys.stderr)
            continue
        if not isinstance(adf, dict):
            skipped += 1
            continue
        new_desc = _render_adf(adf).strip()
        if not new_desc or new_desc == desc:
            skipped += 1
            continue
        print(f"  rewriting {r['task_id']}: {len(desc)} -> {len(new_desc)} chars")
        if not args.dry_run:
            conn.execute(
                "UPDATE tasks SET description=? WHERE task_id=?",
                (new_desc, r["task_id"]),
            )
        rewritten += 1
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"\nrewritten={rewritten} skipped={skipped} errors={errors} "
          f"({'dry run -- nothing persisted' if args.dry_run else 'committed'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
