"""Offline wheel/source-tree/notice binding; never extracts or executes packages."""
import hashlib
import zipfile


def git_blob(data: bytes) -> str:
    """Git SHA-1 blob identity, used solely to compare the supplied Git tree."""
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def reconcile(wheel, expected_sha256, tree, source_prefix, package_prefix, notice,
              installed_root=None):
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('Wheel SHA256 mismatch')
    if tree.get('truncated'):
        raise ValueError('Incomplete source tree')
    blobs = {item['path']: item['sha'] for item in tree['tree'] if item['type'] == 'blob'}
    if blobs.get('LICENSE') != git_blob(notice.read_bytes()):
        raise ValueError('Notice does not match source-tree LICENSE blob')
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if not name.startswith(package_prefix + '/') or name.endswith('/'):
                continue
            content = archive.read(name)
            if blobs.get(source_prefix + '/' + name) != git_blob(content):
                raise ValueError('Package file does not match supplied release tree')
            if installed_root is not None:
                path = (installed_root / name).resolve()
                if not path.is_relative_to(installed_root.resolve()) or path.read_bytes() != content:
                    raise ValueError('Installed package file does not match wheel')
            count += 1
    if not count:
        raise ValueError('No package files matched')
    return {'package_files_bound_to_source_tree': count,
            'installed_files_match_wheel': installed_root is not None,
            'notice_sha256': hashlib.sha256(notice.read_bytes()).hexdigest(),
            'scope': 'Specified package subtree and root LICENSE; metadata and other subtrees excluded'}
