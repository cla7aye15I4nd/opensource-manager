# Open-source manager

This public repository updates the default branches of the forks listed in
`projects.txt` every 30 minutes.

It has only three files:

- `projects.txt`: one fork name per line
- `.github/workflows/sync.yml`: the scheduled sync
- `README.md`: this explanation

The workflow uses GitHub's official `gh repo sync` command. It does not force
updates, so a conflicting fork fails instead of losing commits.

Authentication uses a private GitHub App with **Contents: read and write** and
**Workflows: read and write** permissions. The Workflows permission is needed
when an upstream update changes files in `.github/workflows`. Its App ID is
stored in the `SYNC_APP_ID` repository variable and its private key in the
`SYNC_APP_PRIVATE_KEY` repository secret.

To add or remove a project, edit `projects.txt`. You can also run the workflow
manually from the repository's **Actions** page.
