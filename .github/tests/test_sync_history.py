import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts/sync_history.py'
spec = importlib.util.spec_from_file_location('history', SCRIPT)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target.git'
        history.command(['git', 'init', '-b', 'main', str(self.source)])
        history.command(['git', 'init', '--bare', str(self.target)])
        for number in (1, 2):
            (self.source / 'source.c').write_text(f'int source = {number};\n')
            history.git(self.source, 'add', '.')
            history.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', f'upstream {number}')
        history.git(self.source, 'tag', 'v1')
        self.checkout = self.root / 'checkout'
        history.command(['git', 'clone', str(self.source), str(self.checkout)])

    def test_publishes_complete_ancestry_and_tags(self):
        self.assertTrue(history.publish_history(self.checkout, str(self.target), 'upstream-history'))
        self.assertEqual(history.command(['git', '--git-dir', str(self.target), 'rev-list', '--count', 'refs/heads/upstream-history']), '2')
        self.assertEqual(history.command(['git', '--git-dir', str(self.target), 'rev-parse', 'refs/tags/v1']), history.git(self.source, 'rev-parse', 'v1'))

    def test_dry_run_does_not_publish(self):
        self.assertTrue(history.publish_history(self.checkout, str(self.target), 'upstream-history', True))
        self.assertNotEqual(subprocess.run(['git', '--git-dir', str(self.target), 'show-ref', '--heads'], capture_output=True).returncode, 0)

    def test_can_preserve_branch_history_without_rejected_tags(self):
        self.assertTrue(history.publish_history(self.checkout, str(self.target), 'upstream-history', mirror_tags=False))
        self.assertEqual(history.command(['git', '--git-dir', str(self.target), 'rev-list', '--count', 'refs/heads/upstream-history']), '2')
        self.assertNotEqual(subprocess.run(['git', '--git-dir', str(self.target), 'show-ref', '--tags'], capture_output=True).returncode, 0)

    def test_existing_snapshot_branch_is_preserved(self):
        # A local snapshot branch can be unrelated to the upstream graph.
        snapshot = self.root / 'snapshot'
        history.command(['git', 'init', '-b', 'main', str(snapshot)])
        (snapshot / 'snapshot.txt').write_text('old snapshot\n')
        history.git(snapshot, 'add', '.')
        history.git(snapshot, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'snapshot')
        snapshot_sha = history.git(snapshot, 'rev-parse', 'HEAD')
        history.git(snapshot, 'push', str(self.target), 'HEAD:refs/heads/main')
        self.assertTrue(history.publish_history(self.checkout, str(self.target), 'upstream-history'))
        self.assertEqual(history.command(['git', '--git-dir', str(self.target), 'rev-parse', 'refs/heads/main']), snapshot_sha)

    def test_rejects_divergent_history_branch(self):
        history.publish_history(self.checkout, str(self.target), 'upstream-history')
        history.git(self.source, 'reset', '--hard', 'HEAD~1')
        (self.source / 'source.c').write_text('diverged\n')
        history.git(self.source, 'add', '.')
        history.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'diverged')
        other = self.root / 'other'
        history.command(['git', 'clone', str(self.source), str(other)])
        with self.assertRaisesRegex(ValueError, 'diverged'):
            history.publish_history(other, str(self.target), 'upstream-history')

    def test_verifies_mirror_refs_against_canonical_repo(self):
        history.verify_official_refs(self.checkout, str(self.source))
        history.git(self.source, 'tag', 'v2')
        with self.assertRaisesRegex(ValueError, 'tags differ'):
            history.verify_official_refs(self.checkout, str(self.source))
        history.git(self.source, 'tag', '-d', 'v2')
        (self.source / 'source.c').write_text('new upstream head\n')
        history.git(self.source, 'add', '.')
        history.git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'new')
        with self.assertRaisesRegex(ValueError, 'differs'):
            history.verify_official_refs(self.checkout, str(self.source))

    def test_config_rejects_credentials_and_duplicates(self):
        mirror = {'key': 'x', 'target': 'example/opensource-upstream__x', 'upstream_url': 'https://example.org/x.git', 'branch': 'upstream-history'}
        history.validate_config({'schema': 1, 'mirrors': [mirror]}, 'example')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            history.validate_config({'schema': 1, 'mirrors': [mirror, mirror]}, 'example')
        with self.assertRaisesRegex(ValueError, 'credential-free'):
            history.validate_config({'schema': 1, 'mirrors': [{**mirror, 'upstream_url': 'https://user:password@example.org/x.git'}]}, 'example')
        with self.assertRaisesRegex(ValueError, 'credential-free'):
            history.validate_config({'schema': 1, 'mirrors': [{**mirror, 'verify_head_url': 'http://example.org/x.git'}]}, 'example')


if __name__ == '__main__':
    unittest.main()
