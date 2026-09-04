# Open-source manager

This private repository keeps a curated set of GitHub forks synchronized with
their upstream default branches. The managed repositories are declared in
`projects.json`; the scheduled workflow runs every day and can also be started
manually.

The synchronization is intentionally conservative:

- It uses GitHub's fork `merge-upstream` API.
- It updates only the fork's default branch.
- It never force-resets a branch or deletes refs.
- It fails visibly on merge conflicts, parent mismatches, or API errors.
- It validates that every destination is still a fork of the declared upstream.

## Authentication

The workflow needs cross-repository write access. The manager repository's
`GITHUB_TOKEN` cannot write to the managed forks, so use a GitHub App:

1. Create a private GitHub App owned by the account that owns the forks.
2. Grant repository **Contents: read and write** and **Metadata: read** only.
3. Install it on this repository and every repository listed in
   `projects.json`.
4. Add the App ID as the repository variable `SYNC_APP_ID`.
5. Add the complete generated private key as the repository secret
   `SYNC_APP_PRIVATE_KEY`.

Do not use a broad classic personal access token. The workflow requests a
short-lived installation token for each run.

## Local validation

Python 3.12 or newer is required.

```bash
python3 -m unittest discover -p '*_test.py'
python3 sync_forks.py --dry-run
```

The dry run still calls GitHub to verify repository identity and parent
relationships, so set `GH_TOKEN` first.

## Adding or removing projects

Edit `projects.json` and submit a normal pull request. Each entry must identify
an `apache/*` upstream and a destination repository whose name starts with
`opensource-apache__`. Duplicate sources and destinations are rejected.

## Schedule

The workflow runs daily at 04:17 UTC. GitHub schedules can be delayed during
periods of high Actions load; use **Run workflow** for an immediate sync.
