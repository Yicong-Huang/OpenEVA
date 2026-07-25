#!/usr/bin/env python3
"""One-shot migration: backfill `project` on ticket-backed task rows.

Ticket tasks (rows with a non-empty `ticket_id`) historically got an
empty `project`. That left them out of the shared (project, task_id)
history/PR model exposed in the UI. New rows now get their JIRA prefix
as project (`INTKEY-14736` -> `INTKEY`, `EX-123` -> `EX`); this script
fixes the pre-existing rows the same way.

Only touches rows where:
  - ticket_id is non-empty, AND
  - project is currently empty/NULL, AND
  - the ticket_id yields a non-empty prefix.

History and PRs are keyed by task_id alone, so they are unaffected --
this only changes which project folder the task shows under, which is
what unlocks the ticket card's history/PR UI.

Idempotent: re-running is a no-op once projects are filled.

Usage:
    ~/venv312/bin/python bin/backfill_ticket_task_project.py [--db PATH] [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "src"))

try:
    from pysqlite3 import dbapi2 as sqlite3
except ImportError:
    import sqlite3


def _prefix(key: str) -> str:
    if not key or "-" not in key:
        return ""
    return key.rsplit("-", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "eva.db"),
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT task_id, ticket_id FROM tasks "
        "WHERE ticket_id IS NOT NULL AND ticket_id != '' "
        "AND (project IS NULL OR project = '')"
    ).fetchall()

    updates = []
    for r in rows:
        proj = _prefix(r["ticket_id"])
        if proj:
            updates.append((proj, r["task_id"], r["ticket_id"]))

    print(f"empty-project ticket tasks: {len(rows)}; "
          f"with a resolvable prefix: {len(updates)}")
    for proj, task_id, ticket in updates:
        print(f"  {task_id}  ticket={ticket}  ->  project={proj}")

    if args.dry_run:
        print("[dry-run] no changes written")
        return 0

    for proj, task_id, _ticket in updates:
        conn.execute(
            "UPDATE tasks SET project=? WHERE task_id=?", (proj, task_id)
        )
    conn.commit()
    print(f"updated {len(updates)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
