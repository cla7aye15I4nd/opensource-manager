# Non-GitHub upstream mirrors

The manager tracks 19 Git upstreams in `.github/history-mirrors.json`. This includes libaom, dav1d, libgav1 and libyuv. Each mirror clones the complete Git repository, then publishes the upstream default branch with its complete ancestry and all upstream tags. It never uses a shallow clone, squashes commits or force-pushes. Other upstream branches are not copied. The workflow runs every six hours; manual runs default to dry run and can select one library key.

The 15 previously published Git or GNU source-snapshot repositories keep their existing snapshot `main` branch. The history workflow creates an independent `upstream-history` branch, verifies its fast-forward relationship on subsequent runs, publishes tags, and then makes it the default branch. The old snapshot branch remains available. Four new repositories use the upstream's branch name as their default. The existing native GitHub fork `opensource-lemenkov__libyuv` remains separately managed by the fork workflow; `opensource-chromium__libyuv` follows the canonical Chromium Git upstream.

The three release-source entries in `.github/snapshot-mirrors.json` remain synchronized as source snapshots: gettext, libunistring and LZO. Gettext and libunistring are *also* configured for full-history synchronization from GNU Savannah's `/r/` Git endpoints; the `/git/` endpoints returned HTTP 502 during migration. If the Git service fails, the history workflow reports an error for those entries while their release snapshots continue. An official public LZO Git repository has not been identified, so only the published release sequence can be retained for LZO. Release archives are obtained through Homebrew's formula registry and checked against its SHA256.

Git history contains historical files, including deleted files. These histories were already public at their upstream origins; the manager does not claim to audit every historical blob for credentials. Target repository Actions must be disabled before publication and remain disabled. Upstream build scripts and workflows are copied as Git content, never run by the manager. The manager refuses to update a history branch that diverges from upstream or to replace a conflicting tag. It also refuses to update a source snapshot with local changes. Existing repositories and branches are not deleted.

The `SYNC_APP_CLIENT_ID` variable and `SYNC_APP_PRIVATE_KEY` secret provide a short-lived GitHub App token limited to each configured target. The history workflow needs Contents and Workflows write permissions to push upstream objects and Administration write permission to select the history branch as the repository default. The snapshot workflow needs Contents and Workflows write plus Administration read. Repository creation is a separate bootstrap step; scheduled synchronization requires targets to exist.

## Run synchronization

In the manager's Actions page, run **Sync full upstream Git histories** or **Sync upstream source snapshots**. Manual runs default to dry run. To publish, turn dry run off. Specify a library key such as `libaom` or `pixman` to limit a history run. Each job reports `updated`, `unchanged`, `would-update` or `error`. A failed target does not stop the other history jobs.

## Local validation

```sh
python3 -m unittest discover -s .github/tests -p test_sync_history.py -v
python3 -m unittest discover -s .github/tests -p test_sync_snapshots.py -v
bash .github/tests/sync.sh
```

The snapshot archive safety test requires Python 3.12 or later. Git and GitHub CLI are required for live synchronization.
