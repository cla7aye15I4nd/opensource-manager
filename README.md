# Open-source manager

This public repository discovers and updates every public fork owned by
`cla7aye15I4nd` whose name starts with `opensource-`. It runs every 30 minutes.

It has only two files:

- `.github/workflows/sync.yml`: the scheduled sync
- `README.md`: this explanation

The workflow uses GitHub's official `gh repo sync` command. It does not force
updates, so a conflicting fork fails instead of losing commits.

Authentication uses a private GitHub App with **Contents: read and write** and
**Workflows: read and write** permissions. The Workflows permission is needed
when an upstream update changes files in `.github/workflows`. Its App ID is
stored in the `SYNC_APP_ID` repository variable and its private key in the
`SYNC_APP_PRIVATE_KEY` repository secret.

GitHub does not offer an installation option for all public repositories only,
so the App is installed for all repositories. Each workflow run first discovers
matching public forks with its read-only built-in token, then creates a short-lived
App token scoped only to those repositories. Private repositories and non-forks
are never selected for synchronization.

You can also run the workflow manually from the repository's **Actions** page.
