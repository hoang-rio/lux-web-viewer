"""Regression tests for SQLite schema migration recovery on the SaaS branch.

SaaS normally runs on PostgreSQL (Alembic), but the SQLite runtime path is
still reachable when POSTGRES is not configured. fcm.py opens its own
connection to write notification_history regardless of RUN_WEB_VIEWER, so the
SQLite schema has to exist in every mode.
"""

import ast
import logging
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

import migration


ROOT = Path(__file__).resolve().parent.parent


class TestMigrationNotGatedOnWebViewer(unittest.TestCase):
    def test_run_migration_is_not_nested_in_a_run_web_view_branch(self):
        """fcm.py writes notification_history in every mode via its own
        connection, so the schema must not wait for the web viewer."""
        tree = ast.parse((ROOT / "app.py").read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_migration"
        ]
        self.assertTrue(calls, "app.py no longer calls run_migration")

        for branch in ast.walk(tree):
            if not isinstance(branch, ast.If):
                continue
            conditions = {
                node.id for node in ast.walk(branch.test) if isinstance(node, ast.Name)
            }
            if "run_web_view" not in conditions:
                continue
            for stmt in branch.body:
                for call in calls:
                    if call in ast.walk(stmt):
                        with self.subTest(line=call.lineno):
                            self.fail(
                                f"run_migration at app.py:{call.lineno} is nested "
                                f"inside a run_web_view branch; notification_history "
                                f"would never be created when RUN_WEB_VIEWER is off"
                            )

    def test_db_connection_is_bound_in_every_working_mode(self):
        """process_inverter_data receives db_connection unconditionally now, so
        the name must be bound before those call sites in every branch."""
        tree = ast.parse((ROOT / "app.py").read_text())
        for func in ast.walk(tree):
            if not isinstance(func, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if func.name != "main":
                continue
            bound_at = [
                node.lineno
                for node in ast.walk(func)
                if isinstance(node, ast.Name)
                and node.id == "db_connection"
                and isinstance(node.ctx, ast.Store)
            ]
            self.assertTrue(bound_at, "db_connection is never assigned in main()")
            used_at = [
                node.lineno
                for node in ast.walk(func)
                if isinstance(node, ast.Name)
                and node.id == "db_connection"
                and isinstance(node.ctx, ast.Load)
            ]
            earliest_use = min(used_at) if used_at else None
            for line in used_at:
                self.assertTrue(
                    any(assign < line for assign in bound_at),
                    f"db_connection read at app.py:{line} before any assignment",
                )
            self.assertIsNotNone(earliest_use)


class TestRunMigrationRecovery(unittest.TestCase):
    """run_migration must rebuild from any incomplete state, not silently no-op."""

    TOTAL = len(migration.MIGRATIONS_SQL)
    TABLES = {"hourly_chart", "daily_chart", "notification_history", "settings"}

    def setUp(self):
        # run_migration rebinds the module-level logger; keep test output clean.
        patcher = mock.patch.object(migration, "logger", mock.Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertFullyMigrated(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertEqual(self.TABLES - tables, set(), "tables were never created")
        columns = {
            table: {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for table in ("daily_chart", "notification_history")
        }
        self.assertIn("updated", columns["daily_chart"])
        self.assertIn("read", columns["notification_history"])
        count, highest = conn.execute(
            "SELECT COUNT(*), MAX(id) FROM migration"
        ).fetchone()
        self.assertEqual((count, highest), (self.TOTAL, self.TOTAL))

    def test_empty_database(self):
        conn = sqlite3.connect(":memory:")
        migration.run_migration(conn)
        self.assertFullyMigrated(conn)

    def test_migration_table_exists_but_is_empty(self):
        """DDL auto-commits, so a crash between the first CREATE TABLE and the
        first tracking INSERT leaves this state; it must rebuild the schema."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE migration (id INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.commit()
        migration.run_migration(conn)
        self.assertFullyMigrated(conn)

    def test_half_built_tables_with_empty_tracker(self):
        """Tables created by earlier migrations but no tracker rows at all."""
        conn = sqlite3.connect(":memory:")
        for sql in migration.MIGRATIONS_SQL[:5]:
            conn.execute(sql)
        conn.commit()
        migration.run_migration(conn)
        self.assertFullyMigrated(conn)

    def test_fully_migrated_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        for _ in range(3):
            migration.run_migration(conn)
        self.assertFullyMigrated(conn)

    def test_partially_migrated_resumes_from_missing_rows(self):
        conn = sqlite3.connect(":memory:")
        migration.run_migration(conn)
        conn.execute("DELETE FROM migration WHERE id > 4")
        conn.commit()
        migration.run_migration(conn)
        self.assertFullyMigrated(conn)

    def test_duplicate_column_does_not_abort_sequence(self):
        """Re-running from scratch reaches ADD COLUMN twice; that must be
        tolerated instead of raising sqlite3.OperationalError."""
        conn = sqlite3.connect(":memory:")
        for sql in migration.MIGRATIONS_SQL[:4]:
            conn.execute(sql)
        conn.commit()
        migration.run_migration(conn)
        self.assertFullyMigrated(conn)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
