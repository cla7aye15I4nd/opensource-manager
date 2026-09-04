#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import cast

API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
FORK_NAME_PREFIX = "opensource-apache__"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
type Requester = Callable[[str, str, str, JsonObject | None], JsonObject]


@dataclass(frozen=True, slots=True, kw_only=True)
class Project:
    upstream: str
    fork: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncResult:
    project: Project
    status: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Arguments:
    manifest: Path
    dry_run: bool
    workers: int


class ApiError(RuntimeError):
    def __init__(self, *, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _validate_repository(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not REPOSITORY_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must use the OWNER/REPOSITORY format")
    return value


def load_projects(path: Path) -> tuple[Project, ...]:
    raw_value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw_value, dict):
        raise ValueError("manifest must be a JSON object")
    raw = cast(JsonObject, raw_value)
    if raw.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")

    entries = raw.get("projects")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest projects must be a non-empty list")

    projects: list[Project] = []
    upstreams: set[str] = set()
    forks: set[str] = set()
    for index, entry_value in enumerate(entries):
        if not isinstance(entry_value, dict):
            raise ValueError(f"projects[{index}] must be a JSON object")
        entry = cast(JsonObject, entry_value)
        if set(entry) != {"upstream", "fork"}:
            raise ValueError(f"projects[{index}] must contain only upstream and fork")

        upstream = _validate_repository(
            entry["upstream"], field=f"projects[{index}].upstream"
        )
        fork = _validate_repository(entry["fork"], field=f"projects[{index}].fork")
        upstream_owner, _ = upstream.split("/", maxsplit=1)
        _, fork_name = fork.split("/", maxsplit=1)
        if upstream_owner.casefold() != "apache":
            raise ValueError(f"projects[{index}].upstream must belong to apache")
        if not fork_name.startswith(FORK_NAME_PREFIX):
            raise ValueError(
                f"projects[{index}].fork must start with {FORK_NAME_PREFIX}"
            )

        upstream_key = upstream.casefold()
        fork_key = fork.casefold()
        if upstream_key in upstreams:
            raise ValueError(f"duplicate upstream: {upstream}")
        if fork_key in forks:
            raise ValueError(f"duplicate fork: {fork}")
        upstreams.add(upstream_key)
        forks.add(fork_key)
        projects.append(Project(upstream=upstream, fork=fork))

    return tuple(projects)


def request_json(
    token: str, method: str, path: str, payload: JsonObject | None = None
) -> JsonObject:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "opensource-manager",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            parsed_error_value = cast(object, json.loads(error_body))
            if isinstance(parsed_error_value, dict):
                parsed_error = cast(JsonObject, parsed_error_value)
                detail = str(parsed_error.get("message", error_body))
            else:
                detail = error_body
        except json.JSONDecodeError:
            detail = error_body
        raise ApiError(status=error.code, detail=detail) from error

    parsed_value = cast(object, json.loads(response_body))
    if not isinstance(parsed_value, dict):
        raise ApiError(status=500, detail="GitHub returned a non-object response")
    return cast(JsonObject, parsed_value)


def sync_project(
    project: Project,
    *,
    token: str,
    dry_run: bool,
    requester: Requester = request_json,
) -> SyncResult:
    try:
        repository = requester(token, "GET", f"/repos/{project.fork}", None)
        parent_value = repository.get("parent")
        parent = (
            cast(JsonObject, parent_value) if isinstance(parent_value, dict) else None
        )
        actual_parent = parent.get("full_name") if parent is not None else None
        if repository.get("fork") is not True:
            return SyncResult(
                project=project,
                status="error",
                detail="destination is not a GitHub fork",
            )
        if (
            not isinstance(actual_parent, str)
            or actual_parent.casefold() != project.upstream.casefold()
        ):
            return SyncResult(
                project=project,
                status="error",
                detail=f"parent mismatch: expected {project.upstream}, got {actual_parent}",
            )

        branch = repository.get("default_branch")
        if not isinstance(branch, str) or not branch:
            return SyncResult(
                project=project,
                status="error",
                detail="destination has no default branch",
            )
        if dry_run:
            return SyncResult(
                project=project,
                status="checked",
                detail=f"would sync default branch {branch}",
            )

        response = requester(
            token,
            "POST",
            f"/repos/{project.fork}/merge-upstream",
            {"branch": branch},
        )
        merge_type = response.get("merge_type")
        message = str(response.get("message", "sync completed"))
        status = "up-to-date" if merge_type == "none" else "synced"
        return SyncResult(project=project, status=status, detail=message)
    except ApiError as error:
        status = "conflict" if error.status == 409 else "error"
        return SyncResult(
            project=project,
            status=status,
            detail=f"GitHub API {error.status}: {error.detail}",
        )
    except (OSError, json.JSONDecodeError) as error:
        return SyncResult(project=project, status="error", detail=str(error))


def render_summary(results: tuple[SyncResult, ...], *, dry_run: bool) -> str:
    heading = "Fork synchronization dry run" if dry_run else "Fork synchronization"
    lines = [
        f"## {heading}",
        "",
        "| Fork | Upstream | Status | Detail |",
        "|---|---|---|---|",
    ]
    for result in results:
        detail = result.detail.replace("|", r"\|").replace("\n", " ")
        lines.append(
            f"| `{result.project.fork}` | `{result.project.upstream}` | "
            f"{result.status} | {detail} |"
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    totals = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
    lines.extend(["", f"**Total:** {len(results)} ({totals})"])
    return "\n".join(lines)


def parse_args(argv: list[str]) -> Arguments:
    parser = argparse.ArgumentParser(
        description="Safely sync managed GitHub forks with their upstream defaults."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("projects.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    namespace = parser.parse_args(argv)
    manifest = cast(Path, namespace.manifest)
    dry_run = cast(bool, namespace.dry_run)
    workers = cast(int, namespace.workers)
    if not 1 <= workers <= 8:
        parser.error("--workers must be between 1 and 8")
    return Arguments(manifest=manifest, dry_run=dry_run, workers=workers)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        projects = load_projects(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Invalid manifest: {error}", file=sys.stderr)
        return 2

    token = os.environ.get("GH_TOKEN")
    if not token:
        print("GH_TOKEN is required", file=sys.stderr)
        return 2

    def sync(project: Project) -> SyncResult:
        return sync_project(project, token=token, dry_run=args.dry_run)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = tuple(executor.map(sync, projects))

    summary = render_summary(results, dry_run=args.dry_run)
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with Path(step_summary).open("a", encoding="utf-8") as output:
            output.write(f"{summary}\n")

    return 1 if any(result.status in {"conflict", "error"} for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
