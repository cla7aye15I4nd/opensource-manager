#!/usr/bin/env python3
"""Publish current-source snapshots of explicitly configured public upstreams.

No upstream history, branches, tags or build scripts are imported/executed.
Only ordinary fast-forward pushes are used. Local changes in a managed mirror
stop synchronization instead of being overwritten.
"""
import argparse,hashlib,json,os,pathlib,re,shutil,subprocess,tarfile,tempfile,urllib.parse,urllib.request

META='.upstream-snapshot.json'
NOTICE='UPSTREAM-MIRROR.md'

def command(args,cwd=None,timeout=180,input=None):
    r=subprocess.run(args,cwd=cwd,input=input,capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(r.stderr[-1200:].strip())
    return r.stdout.strip()

def git(repo,*args):return command(['git','-C',str(repo),*args])

def api(endpoint,method='GET',body=None):
    args=['gh','api',endpoint,'--method',method]
    if body is not None:args+=['--input','-']
    out=command(args,input=json.dumps(body)if body is not None else None)
    return json.loads(out)if out else None

def validate_config(config,owner):
    if config.get('schema')!=1:raise ValueError('Unsupported mirror configuration schema')
    seen=set()
    for m in config['mirrors']:
        if not re.fullmatch(re.escape(owner)+r'/opensource-[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+',m['target']):raise ValueError('Target must be an explicitly named opensource repository of the configured owner')
        if m['target']in seen:raise ValueError('Duplicate target')
        seen.add(m['target'])
        if m['strategy']not in {'git','release'}:raise ValueError('Unknown source strategy')
        if m['strategy']=='git':
            p=urllib.parse.urlsplit(m['upstream_url'])
            if p.scheme!='https'or p.username or p.password or not p.hostname:raise ValueError('Public upstream must use an HTTPS URL without credentials')
        elif not re.fullmatch(r'[a-z0-9][a-z0-9@+_.-]*',m['formula']):raise ValueError('Invalid release registry package name')
    return config['mirrors']

def tracked_files(repo):
    r=subprocess.run(['git','-C',str(repo),'ls-files','-z'],capture_output=True,check=True)
    return [pathlib.Path(p.decode())for p in r.stdout.split(b'\0')if p]

def tree_hash(root,files=None):
    root=pathlib.Path(root);h=hashlib.sha256()
    files=files if files is not None else [p.relative_to(root)for p in root.rglob('*')if(p.is_file()or p.is_symlink())and '.git'not in p.relative_to(root).parts]
    for rel in sorted(files,key=str):
        if str(rel)==META:continue
        p=root/rel
        if p.is_symlink():kind=b'link';data=os.readlink(p).encode()
        elif p.is_file():kind=b'exec'if p.stat().st_mode&0o111 else b'file';data=p.read_bytes()
        else:continue
        h.update(str(rel).encode()+b'\0'+kind+b'\0'+hashlib.sha256(data).digest())
    return h.hexdigest()

def review_tree(root):
    licenses=[];notices=[];flags=[];fixtures=[]
    for p in pathlib.Path(root).rglob('*'):
        if not p.is_file()or p.is_symlink()or '.git'in p.relative_to(root).parts:continue
        rel=str(p.relative_to(root));name=p.name.upper()
        if name.startswith(('COPYING','LICENSE','LICENCE')):licenses.append(rel)
        if name.startswith(('NOTICE','COPYRIGHT','AUTHORS')):notices.append(rel)
        if p.stat().st_size>95*1024*1024:flags.append({'file':rel,'reason':'oversized-file'});continue
        if p.stat().st_size>2*1024*1024:continue
        secret=p.name in {'.env','.netrc','.pypirc','id_rsa','id_ed25519'}or bool(re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}\b',p.read_bytes()))
        if secret:
            if re.search(r'(?i)(test|fixture|example|sample|doc)',rel):fixtures.append(rel)
            else:flags.append({'file':rel,'reason':'credential-marker'})
    if not licenses:raise ValueError('No license/copying file found in current source snapshot')
    if flags:raise ValueError('Publication check needs review: '+json.dumps(flags))
    return {'license_files':sorted(licenses),'notice_files':sorted(notices),'public_fixture_key_markers':sorted(fixtures)}

def extract_archive(archive,destination):
    destination=pathlib.Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive)as t:t.extractall(destination,filter='data')
    children=list(destination.iterdir())
    return children[0]if len(children)==1 and children[0].is_dir()else destination

def export_git_checkout(checkout,destination):
    archive=pathlib.Path(destination).parent/'current-source.tar'
    git(checkout,'archive','--format=tar','--output='+str(archive),'HEAD')
    pathlib.Path(destination).mkdir(parents=True,exist_ok=True)
    # Git export is already rooted at the repository root; don't strip a directory.
    with tarfile.open(archive)as t:t.extractall(destination,filter='data')
    return pathlib.Path(destination)

def fetch_snapshot(m,work):
    work=pathlib.Path(work);work.mkdir(parents=True,exist_ok=True)
    if m['strategy']=='git':
        checkout=work/'upstream'
        try:command(['git','clone','--depth','1','--single-branch','--no-tags',m['upstream_url'],str(checkout)],timeout=240)
        except RuntimeError as e:
            if 'dumb http transport'not in str(e).lower():raise
            if checkout.exists():shutil.rmtree(checkout)
            command(['git','clone','--single-branch','--no-tags',m['upstream_url'],str(checkout)],timeout=240)
        source={'kind':'git','upstream_url':m['upstream_url'],'branch':git(checkout,'branch','--show-current'),'revision':git(checkout,'rev-parse','HEAD')}
        return export_git_checkout(checkout,work/'snapshot'),source
    request=urllib.request.Request('https://formulae.brew.sh/api/formula/'+m['formula']+'.json',headers={'User-Agent':'opensource-manager-source-mirror/1'})
    with urllib.request.urlopen(request,timeout=60)as r:release=json.load(r)
    info=release['urls']['stable'];url=info['url'];parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https'or parsed.hostname not in m['allowed_archive_hosts']or parsed.username or parsed.password:raise ValueError('Release registry returned an unapproved source host')
    expected=info['checksum']
    if not re.fullmatch(r'[a-f0-9]{64}',expected):raise ValueError('Missing source archive SHA256')
    archive=work/'release.archive';h=hashlib.sha256();total=0
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'opensource-manager-source-mirror/1'}),timeout=120)as response,archive.open('wb')as f:
        if urllib.parse.urlsplit(response.url).scheme!='https':raise ValueError('Archive redirected outside HTTPS')
        while chunk:=response.read(1024*1024):
            total+=len(chunk)
            if total>250*1024*1024:raise ValueError('Source archive exceeded the size limit')
            h.update(chunk);f.write(chunk)
    if h.hexdigest()!=expected:raise ValueError('Source archive checksum mismatch')
    root=extract_archive(archive,work/'unpacked')
    return root,{'kind':'release','upstream_url':m['canonical_upstream'],'formula':m['formula'],'version':release['versions']['stable'],'archive_url':url,'revision':expected}

def check_existing(repo,m):
    marker=pathlib.Path(repo)/META
    if not marker.exists():raise ValueError('Existing nonempty repository has no snapshot marker; left untouched')
    old=json.loads(marker.read_text())
    if old.get('target')!=m['target']or old.get('canonical_upstream')!=m['canonical_upstream']:raise ValueError('Existing mirror provenance differs; left untouched')
    if old.get('tree_sha256')!=tree_hash(repo,tracked_files(repo)):raise ValueError('Mirror contains local/manual source changes; left untouched')
    return old

def materialize(repo,m,source_tree,source,old=None):
    review=review_tree(source_tree)
    for name in [META,NOTICE]:
        if (pathlib.Path(source_tree)/name).exists():raise ValueError('Upstream contains a reserved mirror metadata filename')
    if old is not None:
        # Check before touching any target source file.
        check_existing(repo,m)
        if old['source'].get('revision')==source.get('revision')or old.get('upstream_tree_sha256')==tree_hash(source_tree):return False
    repo=pathlib.Path(repo)
    for p in repo.iterdir():
        if p.name=='.git':continue
        if p.is_dir()and not p.is_symlink():shutil.rmtree(p)
        else:p.unlink()
    for p in pathlib.Path(source_tree).iterdir():
        if p.name=='.git':continue
        dest=repo/p.name
        if p.is_dir()and not p.is_symlink():shutil.copytree(p,dest,symlinks=True)
        elif p.is_symlink():dest.symlink_to(os.readlink(p))
        else:shutil.copy2(p,dest)
    (repo/NOTICE).write_text('# Upstream source snapshot\n\nThis is a current-source snapshot maintained by '+m['target'].split('/')[0]+'.\n\nCanonical upstream: '+m['canonical_upstream']+'\n\nSource revision/version: '+source.get('version',source['revision'])+'\n\nThe original license, copyright and attribution files are retained. No upstream Git history, tags or branches are republished. Upstream build scripts and workflows are mirrored as source data; synchronization runs in opensource-manager.\n')
    record={'schema':1,'target':m['target'],'canonical_upstream':m['canonical_upstream'],'source':source,'publication_review':review,'upstream_tree_sha256':tree_hash(source_tree),'tree_sha256':tree_hash(repo)}
    (repo/META).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    git(repo,'add','--force','--all')
    return bool(git(repo,'diff','--cached','--name-only'))

def commit_snapshot(repo,source):
    git(repo,'-c','user.name=OpenSource Manager','-c','user.email=opensource-manager@users.noreply.github.com','commit','-m','Import upstream source snapshot '+source.get('version',source['revision'][:12]))

def push_snapshot(repo,target,branch='main'):
    # Authentication is supplied by gh from its environment/keyring, never URLs.
    git(repo,'-c','credential.helper=','-c','credential.https://github.com.helper=!gh auth git-credential','push','https://github.com/'+target+'.git','HEAD:refs/heads/'+branch)

def synchronize(m,work,dry_run=False):
    d=api('repos/'+m['target'])
    if d['private']or d['owner']['login']!=m['target'].split('/')[0]or d.get('fork'):raise ValueError('Expected a public, independently created source-snapshot repository')
    settings=api('repos/'+m['target']+'/actions/permissions')
    if settings.get('enabled')is not False:raise ValueError('Target mirror Actions must be disabled; synchronization belongs in opensource-manager')
    target=pathlib.Path(work)/'target'
    command(['git','clone','--depth','1','--single-branch','--no-tags','https://github.com/'+m['target']+'.git',str(target)])
    old=check_existing(target,m)
    source_tree,source=fetch_snapshot(m,pathlib.Path(work)/'source')
    review_tree(source_tree)
    if old['source'].get('revision')==source['revision']or old.get('upstream_tree_sha256')==tree_hash(source_tree):return 'unchanged'
    if dry_run:return 'would-update'
    if materialize(target,m,source_tree,source,old):
        commit_snapshot(target,source)
        push_snapshot(target,m['target'],d['default_branch'])
        return 'updated'
    return 'unchanged'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True);p.add_argument('--owner',required=True);p.add_argument('--only');p.add_argument('--dry-run',action='store_true');p.add_argument('--report',default='snapshot-sync-report.json')
    args=p.parse_args();mirrors=validate_config(json.loads(pathlib.Path(args.config).read_text()),args.owner)
    if args.only:
        mirrors=[m for m in mirrors if m['key']==args.only]
        if not mirrors:p.error('Unknown configured mirror key')
    results=[]
    for m in mirrors:
        try:
            with tempfile.TemporaryDirectory(prefix='source-snapshot-')as work:status=synchronize(m,work,args.dry_run)
            item={'repository':m['target'],'status':status}
        except Exception as e:item={'repository':m['target'],'status':'error','message':str(e)}
        results.append(item);print(json.dumps(item),flush=True)
    pathlib.Path(args.report).write_text(json.dumps(results,indent=2))
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a')as f:
            f.write('## Current-source snapshot mirrors\n\n| Repository | Result |\n|---|---|\n')
            for r in results:f.write('| '+r['repository']+' | '+r['status']+' |\n')
    return 1 if any(r['status']=='error'for r in results)else 0

if __name__=='__main__':raise SystemExit(main())
