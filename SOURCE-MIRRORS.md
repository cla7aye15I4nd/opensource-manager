# Non-GitHub upstream source mirrors

The **Sync upstream source snapshots** workflow keeps the 16 independent public repositories in `.github/snapshot-mirrors.json` current. It runs every six hours; GitHub may delay scheduled runs. The existing native-fork synchronization workflow is unchanged and ignores these repositories because they are not GitHub forks.

Thirteen projects import their upstream default branch's current tree. Gettext, LZO and libunistring use complete official source releases discovered through Homebrew's formula API and verified against its SHA256 before extraction. Libunistring's Git checkout is a bootstrap tree; the release contains the actual gnulib-derived library sources. These three mirrors update when the release registry updates, rather than following development commits.

Each initial mirror has one new snapshot commit. Upstream history, tags and branches are not published. Original README, LICENSE/COPYING, NOTICE and attribution files remain intact. `UPSTREAM-MIRROR.md` identifies the upstream; `.upstream-snapshot.json` records the source revision/archive checksum and source-tree hashes. Upstream build scripts and workflows are copied as data, never executed by this manager. Target-repository Actions must be disabled before publication and remain disabled.

Each changed source tree produces an ordinary follow-up commit. There are no force pushes, branch resets, history replacements or tag pushes. A source revision change with identical file contents creates no duplicate commit. Manual changes to a managed mirror cause synchronization to stop for that repository rather than overwrite the change. Concurrent remote changes are rejected by normal Git fast-forward checks. Fork upstreams with large Git histories may need a full **local** clone when their server cannot support shallow HTTP, but only the current exported tree is published.

The existing `SYNC_APP_CLIENT_ID` variable and `SYNC_APP_PRIVATE_KEY` secret are reused. Each run requests a GitHub App token restricted to the 16 configured target repositories, with contents/workflows write and administration read (to check that target Actions are disabled). Workflow write preserves upstream `.github/workflows` files as source data. No new personal access token is needed. Repository creation and the initial imports are bootstrap operations; the scheduled workflow will not create missing repositories.

## Run or inspect synchronization

Open **Actions → Sync upstream source snapshots → Run workflow** on `main`. Manual runs default to **dry run** and accept an optional library key such as `pixman`. Uncheck dry run to publish changed snapshots. Scheduled runs publish automatically. The run's job summary lists the result for every repository. A failed source fetch or publication check appears as `error`; other configured mirrors are still checked.

The current-tree publication check requires a license file and checks common credential markers and oversized files. Already-public test fixture keys are recorded separately. This is a lightweight check, not an exhaustive secret audit or a review of upstream history. Archives are extracted using Python's data filter. The manager never runs an upstream configure/build/bootstrap script.

## Change or pause a mirror

Review changes to `.github/snapshot-mirrors.json` before merging to `main`. Only credential-free public HTTPS Git origins and explicitly allowed release archive hosts are accepted. Release payloads must match the registry SHA256, including when an HTTPS download mirror is used. Remove an entry to stop managing it, or disable this workflow to pause all snapshot synchronization; existing repository contents are retained.

## Local validation

```sh
python3 -m unittest discover -s .github/tests -p test_sync_snapshots.py -v
bash .github/tests/sync.sh
```

Tests use temporary local Git repositories to check initial imports, incremental updates, preservation of manual changes, provenance checks, missing licenses/credential markers, archive path traversal rejection, source-only exports and ordinary pushes. Python 3.12 or later, Git and GitHub CLI are required.
