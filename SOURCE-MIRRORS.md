# Non-GitHub upstream mirrors

The manager tracks 19 Git upstreams in `.github/history-mirrors.json`. This includes libaom, dav1d, libgav1 and libyuv. Each mirror clones the complete Git repository, then publishes the upstream default branch with its complete ancestry and, except for cairo, upstream tags. It never uses a shallow clone, squashes commits or force-pushes. Other upstream branches are not copied. GitHub rejected some cairo tag targets because their historical commit objects fail its `missingSpaceBeforeDate` fsck check; cairo's full default-branch history is still accepted, but tag copying is disabled. The workflow runs every six hours; manual runs default to dry run and can select one library key.

The 15 previously published Git or GNU source-snapshot repositories keep their existing snapshot `main` branch. The history workflow creates an independent `upstream-history` branch, verifies its fast-forward relationship on subsequent runs, publishes tags, and then makes it the default branch. The old snapshot branch remains available. Four new repositories use the upstream's branch name as their default. The existing native GitHub fork `opensource-lemenkov__libyuv` remains separately managed by the fork workflow; `opensource-chromium__libyuv` follows the canonical Chromium Git upstream.

The three release-source entries in `.github/snapshot-mirrors.json` remain synchronized as source snapshots: gettext, libunistring and LZO. Gettext and libunistring also have full-history branches. GNU Savannah's `/git/` endpoints returned HTTP 502 during migration. Libunistring clones from Savannah's `/r/` endpoint. Gettext clones from `autotools-mirror/gettext` on GitHub because full Savannah clones failed; before each publication, the manager requires the clone's exact HEAD commit ID to match Savannah's `/r/gettext.git` HEAD. If that verification cannot be made, the update fails and the last verified history remains. An official public LZO Git repository has not been identified, so only the published release sequence can be retained for LZO. Release archives are obtained through Homebrew's formula registry and checked against its SHA256.

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
