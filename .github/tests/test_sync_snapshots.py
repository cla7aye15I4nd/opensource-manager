import importlib.util,io,json,pathlib,subprocess,tarfile,tempfile,unittest
from unittest.mock import patch

SCRIPT=pathlib.Path(__file__).resolve().parents[1]/'scripts/sync_snapshots.py'
spec=importlib.util.spec_from_file_location('snapshots',SCRIPT)
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.source=self.root/'source';self.source.mkdir();(self.source/'COPYING').write_text('Original upstream license and attribution\n');(self.source/'hello.c').write_text('int hello(void) { return 1; }\n')
        self.target=self.root/'target';self.target.mkdir();s.command(['git','init','-b','main',str(self.target)])
        self.m={'key':'example','target':'example/opensource-upstream__library','canonical_upstream':'https://example.org/upstream/library','strategy':'git','upstream_url':'https://example.org/upstream/library.git'}
        self.rev={'kind':'git','upstream_url':self.m['upstream_url'],'revision':'a'*40,'branch':'main'}
    def tearDown(self):self.tmp.cleanup()
    def initial(self):
        self.assertTrue(s.materialize(self.target,self.m,self.source,self.rev));s.commit_snapshot(self.target,self.rev)
        return s.check_existing(self.target,self.m)
    def test_initial_commit_preserves_license_and_provenance(self):
        old=self.initial();self.assertEqual((self.target/'COPYING').read_text(),(self.source/'COPYING').read_text());self.assertEqual(old['source']['revision'],self.rev['revision']);self.assertEqual(s.git(self.target,'rev-list','--count','HEAD'),'1')
    def test_changed_source_creates_one_normal_followup_commit(self):
        old=self.initial();(self.source/'hello.c').write_text('int hello(void) { return 2; }\n');rev={**self.rev,'revision':'b'*40}
        self.assertTrue(s.materialize(self.target,self.m,self.source,rev,old));s.commit_snapshot(self.target,rev);s.check_existing(self.target,self.m);self.assertEqual(s.git(self.target,'rev-list','--count','HEAD'),'2')
    def test_identical_source_tree_does_not_make_duplicate_commit(self):
        old=self.initial();self.assertFalse(s.materialize(self.target,self.m,self.source,{**self.rev,'revision':'b'*40},old));self.assertEqual(s.git(self.target,'status','--porcelain'),'')
    def test_manual_change_is_preserved_and_blocks_sync(self):
        old=self.initial();(self.target/'hello.c').write_text('my change\n')
        with self.assertRaisesRegex(ValueError,'manual'):s.materialize(self.target,self.m,self.source,{**self.rev,'revision':'b'*40},old)
        self.assertEqual((self.target/'hello.c').read_text(),'my change\n')
    def test_unknown_existing_repository_is_not_overwritten(self):
        (self.target/'user.txt').write_text('important\n')
        with self.assertRaisesRegex(ValueError,'no snapshot marker'):s.check_existing(self.target,self.m)
        self.assertTrue((self.target/'user.txt').exists())
    def test_missing_license_blocks_publication(self):
        (self.source/'COPYING').unlink()
        with self.assertRaisesRegex(ValueError,'license'):s.review_tree(self.source)
    def test_current_tree_credential_marker_is_not_published(self):
        (self.source/'.env').write_text('example=placeholder\n')
        with self.assertRaisesRegex(ValueError,'credential-marker'):s.materialize(self.target,self.m,self.source,self.rev)
        self.assertFalse((self.target/'hello.c').exists())
    def test_reserved_metadata_does_not_replace_upstream_file(self):
        (self.source/s.META).write_text('{}')
        with self.assertRaisesRegex(ValueError,'reserved'):s.materialize(self.target,self.m,self.source,self.rev)
    def test_git_export_excludes_history_and_deleted_files(self):
        upstream=self.root/'upstream';upstream.mkdir();s.command(['git','init','-b','main',str(upstream)])
        (upstream/'old.txt').write_text('old source content\n');s.git(upstream,'add','--all');s.commit_snapshot(upstream,self.rev)
        (upstream/'old.txt').unlink();(upstream/'current.c').write_text('int current;\n');s.git(upstream,'add','--all');s.commit_snapshot(upstream,self.rev)
        dest=s.export_git_checkout(upstream,self.root/'exported');self.assertTrue((dest/'current.c').is_file());self.assertFalse((dest/'old.txt').exists());self.assertFalse((dest/'.git').exists())
    def test_archive_path_escape_is_rejected(self):
        archive=self.root/'bad.tar'
        with tarfile.open(archive,'w')as t:
            entry=tarfile.TarInfo('../escape.c');entry.size=1;t.addfile(entry,io.BytesIO(b'x'))
        with self.assertRaises(tarfile.FilterError):s.extract_archive(archive,self.root/'unpack')
        self.assertFalse((self.root/'escape.c').exists())
    def test_config_limits_target_owner_and_credentialless_https(self):
        s.validate_config({'schema':1,'mirrors':[self.m]},'example')
        with self.assertRaises(ValueError):s.validate_config({'schema':1,'mirrors':[self.m]},'someone-else')
        with self.assertRaises(ValueError):s.validate_config({'schema':1,'mirrors':[{**self.m,'upstream_url':'https://user:secret@example.org/repo'}]},'example')
    def test_push_never_uses_mirror_or_force(self):
        with patch.object(s,'git')as mocked:s.push_snapshot(self.target,self.m['target'])
        args=mocked.call_args.args;self.assertIn('push',args);self.assertNotIn('--mirror',args);self.assertNotIn('--force',args);self.assertIn('HEAD:refs/heads/main',args)

if __name__=='__main__':unittest.main()
