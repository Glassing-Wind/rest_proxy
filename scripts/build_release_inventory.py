#!/usr/bin/env python3
"""Offline installed-environment inventory and shipped notice collection.

Run using the environment being inventoried. Native bundled dependencies,
downloaded grammars and model assets require additional artifact-specific review.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def generate(output: Path, artifacts: list[Path], distributions=None, prefix=None) -> dict:
    """Create a new private evidence directory, with no metadata URL fetches."""
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    prefix = Path(prefix or sys.prefix).resolve()
    components, records, gaps = [], [], []
    distributions = list(metadata.distributions() if distributions is None else distributions)
    distributions.sort(key=lambda item: item.metadata['Name'].lower())
    for distribution in distributions:
        name, version = distribution.metadata['Name'], distribution.version
        normalized = re.sub(r'[-_.]+', '-', name).lower()
        purl = f'pkg:pypi/{quote(normalized, safe="-")}@{quote(version, safe="")}'
        component = {'type': 'library', 'bom-ref': purl, 'name': name, 'version': version, 'purl': purl}
        expression = distribution.metadata.get('License-Expression')
        legacy_license = distribution.metadata.get('License', '').strip()
        if expression:
            component['licenses'] = [{'expression': expression}]
        elif legacy_license and len(legacy_license) <= 200 and '\n' not in legacy_license:
            component['licenses'] = [{'license': {'name': legacy_license}}]
        else:
            gaps.append({'component': purl, 'gap': 'No concise machine-readable license metadata'})
        files, notices = [], []
        for relative in sorted(distribution.files or [], key=str):
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_relative_to(prefix) or not path.is_file():
                gaps.append({'component': purl, 'gap': 'Recorded file absent or outside environment',
                             'file': str(relative)})
                continue
            row = {'path': str(relative), 'sha256': digest(path), 'bytes': path.stat().st_size}
            files.append(row)
            if re.match(r'^(license|licence|notice|copying|copyright)([._-]|$)', path.name, re.I):
                # Content-addressed copy names cannot escape the new output directory.
                destination = output / 'notices' / normalized / (row['sha256'] + '.txt')
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                destination.write_bytes(path.read_bytes())
                destination.chmod(0o600)
                notices.append(dict(row, copied_to=str(destination.relative_to(output))))
        if not notices:
            gaps.append({'component': purl, 'gap': 'No shipped license/notice files discovered'})
        if not distribution.files:
            gaps.append({'component': purl, 'gap': 'No installed file manifest'})
        records.append({'component': purl, 'files': files, 'notices': notices})
        manifest_digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        component['properties'] = [{'name': 'rest-proxy:installed-file-manifest-sha256',
                                    'value': manifest_digest}]
        components.append(component)
    artifact_rows = [{'name': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size}
                     for path in artifacts]
    bom = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'version': 1, 'components': components,
           'compositions': [{'aggregate': 'incomplete'}]}
    inventory = {'version': 1, 'scope': 'Installed Python distributions and discoverable shipped notices',
                 'artifacts': artifact_rows, 'components': records, 'gaps': gaps,
                 'limitations': ['Native bundled dependency closure not resolved from wheel metadata.',
                                 'Downloaded grammars and model assets are outside this inventory.',
                                 'License metadata and copied notices are evidence, not legal approval.',
                                 'Installed-file hashes are not original download artifact hashes.']}
    for name, value in [('bom.cdx.json', bom), ('inventory.json', inventory)]:
        path = output / name
        path.write_text(json.dumps(value, indent=2) + '\n')
        path.chmod(0o600)
    return {'components': len(components), 'notices': sum(len(row['notices']) for row in records),
            'gaps': gaps, 'artifacts': artifact_rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--artifact', action='append', default=[], type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.artifact), indent=2))
