from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sync_forks import (
    ApiError,
    JsonObject,
    Project,
    load_projects,
    render_summary,
    sync_project,
)


class SyncForksTest(unittest.TestCase):
    def test_load_projects_validates_and_returns_immutable_projects(self) -> None:
        manifest = {
            "schema_version": 1,
            "projects": [
                {
                    "upstream": "apache/airflow",
                    "fork": "owner/opensource-apache__airflow",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            projects = load_projects(path)

        self.assertEqual(
            projects,
            (
                Project(
                    upstream="apache/airflow",
                    fork="owner/opensource-apache__airflow",
                ),
            ),
        )

    def test_load_projects_rejects_duplicate_upstreams(self) -> None:
        manifest = {
            "schema_version": 1,
            "projects": [
                {
                    "upstream": "apache/airflow",
                    "fork": "owner/opensource-apache__airflow",
                },
                {
                    "upstream": "apache/AIRFLOW",
                    "fork": "owner/opensource-apache__another-airflow",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate upstream"):
                load_projects(path)

    def test_sync_project_uses_destination_default_branch(self) -> None:
        calls: list[tuple[str, str, JsonObject | None]] = []

        def requester(
            _token: str,
            method: str,
            path: str,
            payload: JsonObject | None,
        ) -> JsonObject:
            calls.append((method, path, payload))
            if method == "GET":
                return {
                    "fork": True,
                    "default_branch": "trunk",
                    "parent": {"full_name": "apache/example"},
                }
            return {"merge_type": "fast-forward", "message": "synced"}

        project = Project(
            upstream="apache/example",
            fork="owner/opensource-apache__example",
        )
        result = sync_project(
            project, token="token", dry_run=False, requester=requester
        )

        self.assertEqual(result.status, "synced")
        self.assertEqual(
            calls,
            [
                ("GET", "/repos/owner/opensource-apache__example", None),
                (
                    "POST",
                    "/repos/owner/opensource-apache__example/merge-upstream",
                    {"branch": "trunk"},
                ),
            ],
        )

    def test_sync_project_refuses_parent_mismatch(self) -> None:
        def requester(
            _token: str,
            _method: str,
            _path: str,
            _payload: JsonObject | None,
        ) -> JsonObject:
            return {
                "fork": True,
                "default_branch": "main",
                "parent": {"full_name": "other/example"},
            }

        result = sync_project(
            Project(
                upstream="apache/example",
                fork="owner/opensource-apache__example",
            ),
            token="token",
            dry_run=False,
            requester=requester,
        )

        self.assertEqual(result.status, "error")
        self.assertIn("parent mismatch", result.detail)

    def test_sync_project_reports_merge_conflict(self) -> None:
        def requester(
            _token: str,
            method: str,
            _path: str,
            _payload: JsonObject | None,
        ) -> JsonObject:
            if method == "GET":
                return {
                    "fork": True,
                    "default_branch": "main",
                    "parent": {"full_name": "apache/example"},
                }
            raise ApiError(status=409, detail="Merge conflict")

        result = sync_project(
            Project(
                upstream="apache/example",
                fork="owner/opensource-apache__example",
            ),
            token="token",
            dry_run=False,
            requester=requester,
        )

        self.assertEqual(result.status, "conflict")
        self.assertIn("409", result.detail)

    def test_render_summary_escapes_table_content(self) -> None:
        project = Project(
            upstream="apache/example",
            fork="owner/opensource-apache__example",
        )

        def requester(
            _token: str,
            _method: str,
            _path: str,
            _payload: JsonObject | None,
        ) -> JsonObject:
            return {
                "fork": True,
                "default_branch": "main",
                "parent": {"full_name": "apache/example"},
            }

        result = sync_project(
            project,
            token="token",
            dry_run=True,
            requester=requester,
        )

        summary = render_summary((result,), dry_run=True)

        self.assertIn("Fork synchronization dry run", summary)
        self.assertIn("checked: 1", summary)


if __name__ == "__main__":
    unittest.main()
