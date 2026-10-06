"""Publish tested artifacts together; never replace any published release asset."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BINARIES = ('RHYFT.exe', 'RHYFT-windows.zip', 'RHYFT-android.apk')


def version_from_source():
    tree = ast.parse((ROOT / 'nucleo.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'APP_VERSION' for n in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('APP_VERSION missing')


def validate_version(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Expected a stable semantic version')
    if tuple(map(int, version.split('.'))) < (1, 5, 0):
        raise ValueError('Releases before v1.5.0 are frozen')


def validate_assets(directory):
    paths = []
    for name in BINARIES:
        binary = directory / name
        checksum = directory / (name + '.sha256')
        if not binary.is_file() or binary.stat().st_size == 0 or not checksum.is_file():
            raise ValueError(f'Missing artifact or checksum: {name}')
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        if checksum.read_text(encoding='ascii').strip().split()[0].lower() != digest:
            raise ValueError(f'Checksum mismatch: {name}')
        paths.extend([binary, checksum])
    return paths


def gh(*args):
    return subprocess.check_output(['gh', *map(str, args)], text=True).strip()


def main():
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Publishing is restricted to main')
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_SHA']
    version = version_from_source()
    validate_version(version)
    tag = 'v' + version
    notes = ROOT / 'releases' / (tag + '.md')
    if not notes.is_file() or not notes.read_text(encoding='utf-8').strip():
        raise ValueError('Release notes missing')
    assets = validate_assets(ROOT / 'release-assets')
    pages = json.loads(gh('api', f'repos/{repo}/releases', '--paginate', '--slurp'))
    existing = next((release for page in pages for release in page if release['tag_name'] == tag), None)
    if existing and not existing['draft']:
        print(f'{tag} is already published and remains unchanged. Bump the version for a new release.')
        return
    if existing:
        if existing['target_commitish'] != sha:
            raise ValueError('Existing draft belongs to a different commit; refusing to mix builds')
        uploaded = {asset['name']: asset for asset in existing['assets']}
        for path in assets:
            asset = uploaded.get(path.name)
            if asset:
                if asset.get('digest') != 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest():
                    raise ValueError(f'Draft artifact differs: {path.name}')
            else:
                gh('release', 'upload', tag, path, '--repo', repo)
    else:
        # Refuse an existing tag: the validated commit must define this release.
        if gh('api', f'repos/{repo}/git/matching-refs/tags/{tag}') != '[]':
            refs = json.loads(gh('api', f'repos/{repo}/git/matching-refs/tags/{tag}'))
            if any(ref['ref'] == 'refs/tags/' + tag for ref in refs):
                raise ValueError('Release tag already exists; refusing to move it')
        gh('release', 'create', tag, *assets, '--repo', repo, '--target', sha,
           '--title', f'RHYFT {tag} — Your music. No borders.', '--notes-file', notes, '--draft')
    published_assets = json.loads(gh('release', 'view', tag, '--repo', repo, '--json', 'assets'))['assets']
    if {p.name for p in assets} != {a['name'] for a in published_assets}:
        raise ValueError('Draft does not contain exactly the validated artifacts')
    gh('release', 'edit', tag, '--repo', repo, '--draft=false', '--latest')
    print(f'Published {tag} from {sha}: Windows and Android validated in this workflow run.')


if __name__ == '__main__':
    main()
