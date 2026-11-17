#!/usr/bin/env python3
"""Mirror complete upstream Git history without replacing snapshot branches."""

import argparse
import json
import os
import pathlib
import re
import subprocess
import tempfile
import time
import urllib.parse


def command(args, cwd=None, timeout=1800):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:].strip() or result.stdout[-2000:].strip())
    return result.stdout.strip()


def git(repo, *args):
    return command(['git', '-C', str(repo), *args])


def api(endpoint, method='GET', body=None):
    args = ['gh', 'api', endpoint, '--method', method]
    if body is not None:
        args += ['--input', '-']
        result = subprocess.run(args, input=json.dumps(body), capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:].strip())
        return json.loads(result.stdout) if result.stdout else None
    out = command(args, timeout=120)
    return json.loads(out) if out else None


def validate_config(config, owner):
    if config.get('schema') != 1 or not isinstance(config.get('mirrors'), list):
        raise ValueError('Unsupported history mirror configuration')
    keys, targets = set(), set()
    for mirror in config['mirrors']:
        target = mirror['target']
        if not re.fullmatch(re.escape(owner) + r'/opensource-[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+', target):
            raise ValueError('Invalid target repository')
        if mirror['key'] in keys or target in targets:
            raise ValueError('Duplicate mirror key or target')
        keys.add(mirror['key']); targets.add(target)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', mirror['branch']) or mirror['branch'] == 'HEAD':
            raise ValueError('Invalid target branch')
        if not isinstance(mirror.get('mirror_tags', True), bool):
            raise ValueError('mirror_tags must be boolean')
        for url in (mirror['upstream_url'], mirror.get('verify_head_url')):
            if url is None:
                continue
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Upstream must be a credential-free HTTPS Git URL')
    return config['mirrors']


def upstream_head(repo):
    branch_ref = git(repo, 'symbolic-ref', 'refs/remotes/origin/HEAD')
    if not branch_ref.startswith('refs/remotes/origin/'):
        raise ValueError('No upstream default branch')
    branch = branch_ref.removeprefix('refs/remotes/origin/')
    return branch, git(repo, 'rev-parse', branch_ref)


def verify_official_refs(repo, url):
    _, cloned_sha = upstream_head(repo)
    lines = command(['git', 'ls-remote', url, 'HEAD'], timeout=120).splitlines()
    if len(lines) != 1 or lines[0].split('\t', 1)[0] != cloned_sha:
        raise ValueError('Git mirror HEAD differs from canonical upstream; refusing publication')
    official_tags = dict(line.split('\t', 1)[::-1] for line in command(['git', 'ls-remote', '--tags', '--refs', url], timeout=120).splitlines())
    cloned_tags = {}
    for line in git(repo, 'for-each-ref', '--format=%(objectname) %(refname)', 'refs/tags').splitlines():
        sha, ref = line.split(' ', 1)
        cloned_tags[ref] = sha
    if official_tags != cloned_tags:
        raise ValueError('Git mirror tags differ from canonical upstream; refusing publication')


def checked_tags(repo):
    """Return local tag refs and reject ref changes in the target before pushing."""
    local = {}
    for line in git(repo, 'for-each-ref', '--format=%(objectname) %(refname)', 'refs/tags').splitlines():
        sha, ref = line.split(' ', 1)
        local[ref] = sha
    remote = {}
    for line in git(repo, 'ls-remote', '--tags', '--refs', 'target').splitlines():
        sha, ref = line.split('\t', 1)
        remote[ref] = sha
    for ref, sha in remote.items():
        if ref in local and local[ref] != sha:
            raise ValueError('Target tag differs from upstream: ' + ref)
    return sorted(ref for ref in local if ref not in remote)


def push_with_retry(repo, *args):
    for attempt in range(3):
        try:
            return git(repo, *args)
        except RuntimeError as error:
            if attempt == 2 or not any(marker in str(error).lower() for marker in ('http 500', 'http 502', 'http 503', 'http 504', 'remote end hung up', 'unexpected disconnect')):
                raise
            time.sleep(2 ** attempt)


def publish_history(upstream, target_url, branch, dry_run=False, mirror_tags=True):
    _, source_sha = upstream_head(upstream)
    git(upstream, 'remote', 'add', 'target', target_url)
    target_ref = 'refs/heads/' + branch
    lines = git(upstream, 'ls-remote', '--heads', 'target', target_ref).splitlines()
    target_sha = lines[0].split('\t', 1)[0] if lines else None
    if target_sha:
        git(upstream, 'fetch', 'target', target_ref)
        if subprocess.run(['git', '-C', str(upstream), 'merge-base', '--is-ancestor', target_sha, source_sha]).returncode:
            raise ValueError('History branch diverged; refusing to replace commits')
    new_tags = checked_tags(upstream) if mirror_tags else []
    if dry_run:
        return target_sha != source_sha or bool(new_tags)
    # Use gh's short-lived token without embedding it in the remote URL.
    auth = ['-c', 'credential.helper=', '-c', 'credential.https://github.com.helper=!gh auth git-credential']
    if target_sha != source_sha:
        push_with_retry(upstream, *auth, 'push', 'target', source_sha + ':' + target_ref)
    for offset in range(0, len(new_tags), 10):
        push_with_retry(upstream, *auth, 'push', 'target', *new_tags[offset:offset + 10])
    return target_sha != source_sha or bool(new_tags)


def synchronize(mirror, work, dry_run=False):
    target_name = mirror['target']
    metadata = api('repos/' + target_name)
    if metadata['private'] or metadata.get('fork') or metadata['owner']['login'] != target_name.split('/')[0]:
        raise ValueError('Expected a public independent mirror repository')
    settings = api('repos/' + target_name + '/actions/permissions')
    if settings.get('enabled') is not False:
        raise ValueError('Target Actions must be disabled before history publication')

    upstream = pathlib.Path(work) / 'upstream'
    command(['git', 'clone', '--no-single-branch', mirror['upstream_url'], str(upstream)])
    if mirror.get('verify_head_url'):
        verify_official_refs(upstream, mirror['verify_head_url'])
    # Keep the old snapshot branch intact. A separate history branch becomes
    # the default only after its objects and tags are safely published.
    changed = publish_history(upstream, 'https://github.com/' + target_name + '.git', mirror['branch'], dry_run, mirror.get('mirror_tags', True))
    needs_default = metadata.get('default_branch') != mirror['branch']
    if dry_run:
        return 'would-update' if changed or needs_default else 'unchanged'
    if needs_default:
        api('repos/' + target_name, 'PATCH', {'default_branch': mirror['branch']})
    return 'updated' if changed or needs_default else 'unchanged'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--only')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--report', default='history-sync-report.json')
    args = parser.parse_args()
    mirrors = validate_config(json.loads(pathlib.Path(args.config).read_text()), args.owner)
    if args.only:
        mirrors = [mirror for mirror in mirrors if mirror['key'] == args.only]
        if not mirrors:
            parser.error('Unknown configured mirror key')
    results = []
    for mirror in mirrors:
        try:
            with tempfile.TemporaryDirectory(prefix='upstream-history-') as work:
                status = synchronize(mirror, work, args.dry_run)
            result = {'repository': mirror['target'], 'status': status}
        except Exception as error:
            result = {'repository': mirror['target'], 'status': 'error', 'message': str(error)}
        results.append(result)
        print(json.dumps(result), flush=True)
    pathlib.Path(args.report).write_text(json.dumps(results, indent=2) + '\n')
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as output:
            output.write('## Full-history mirrors\n\n| Repository | Result |\n|---|---|\n')
            for result in results:
                output.write('| ' + result['repository'] + ' | ' + result['status'] + ' |\n')
    return int(any(result['status'] == 'error' for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
