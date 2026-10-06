"""Offline observed grammar/bundle and declared-source notice reconciliation.

Does not extract archives to disk, download parsers, change caches or attest
binary reproducibility. Zstandard archives require Python 3.14 tarfile support.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile

from scripts.reconcile_python_notice import git_blob


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def reconcile(cache, bundle, platform, definitions_path, notices_path):
    manifest = json.loads((cache / 'manifest.json').read_text())
    expected = manifest['platforms'][platform]['sha256']
    if sha256(bundle) != expected:
        raise ValueError('Bundle differs from manifest SHA256')
    definitions = json.loads(definitions_path.read_text())
    notices = json.loads(notices_path.read_text())
    if sha256(definitions_path) != notices['definitions_sha256']:
        raise ValueError('Release definitions differ from notice evidence')
    requested, rows = {}, []
    for item in notices['grammars']:
        language = item['language']
        if not re.fullmatch(r'[a-z0-9_]+', language) or language in requested:
            raise ValueError('Invalid or duplicate grammar selection')
        entry = definitions[language]
        if entry['repo'] != item['repository'] or entry['rev'] != item['declared_revision']:
            raise ValueError('Declared grammar source differs from release definitions')
        if not re.fullmatch(r'[0-9a-f]{40}', entry['rev']) or not item['files']:
            raise ValueError('Require pinned source revision and notices')
        for notice in item['files']:
            path = (notices_path.parent / notice['local_file']).resolve()
            if not path.is_relative_to(notices_path.parent.resolve()):
                raise ValueError('Notice path escapes evidence directory')
            if sha256(path) != notice['sha256'] or git_blob(path.read_bytes()) != notice['git_blob_sha1']:
                raise ValueError('Notice content differs from retained source evidence')
        filename = f'libtree_sitter_{language}.dylib'
        requested[filename] = item
    matched = set()
    bundle_files, bundle_notices = 0, []
    with tarfile.open(bundle, 'r:*') as archive:
        for member in archive:
            if not member.isfile():
                continue
            bundle_files += 1
            name = Path(member.name).name
            if name.lower().startswith(('license', 'licence', 'notice', 'copying')):
                bundle_notices.append(member.name)
            if name not in requested:
                continue
            if name in matched or len(Path(member.name).parts) != 1:
                raise ValueError('Ambiguous or nested selected grammar member')
            stream = archive.extractfile(member)
            data = stream.read()
            digest = hashlib.sha256(data).hexdigest()
            cached = cache / 'libs' / name
            if sha256(cached) != digest:
                raise ValueError('Cached grammar does not match verified bundle member')
            matched.add(name)
            rows.append(dict(requested[name], binary=name, binary_sha256=digest, binary_bytes=len(data)))
    if matched != set(requested):
        raise ValueError('Selected grammar is missing from bundle')
    return {'version': manifest['version'], 'platform': platform,
            'bundle_sha256': expected, 'bundle_file_count': bundle_files,
            'bundle_notice_files': bundle_notices, 'release_commit': notices['release_commit'],
            'definitions_sha256': notices['definitions_sha256'],
            'grammars': sorted(rows, key=lambda value: value['language']),
            'limitations': ['Observed binaries match bundle; their source build is not independently reproduced.',
                            'Notices match declared source revisions, not an attestation of every linked native dependency.',
                            'Selected grammar coverage does not cover the complete cached bundle or other platforms.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('cache', 'bundle', 'definitions', 'notices', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--platform', default='macos-arm64')
    args = parser.parse_args()
    result = reconcile(args.cache, args.bundle, args.platform, args.definitions, args.notices)
    with args.output.open('x') as target:
        target.write(json.dumps(result, indent=2) + '\n')
