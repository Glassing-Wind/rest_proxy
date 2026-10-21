"""Bounded runtime/binary fingerprints without environment, credentials or model calls."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys


NATIVE_PACKAGES = ('tree-sitter-language-pack', 'ladybug', 'lancedb', 'pyarrow')
MAX_BINARY_BYTES = 256 * 1024 * 1024


def sha256_file(path: Path, *, limit: int = MAX_BINARY_BYTES) -> str:
    """Hash a bounded file, refusing changes during the read."""
    before = path.stat()
    if before.st_size > limit:
        raise ValueError('File exceeds fingerprint byte budget')
    digest = hashlib.sha256()
    count = 0
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            count += len(block)
            if count > limit:
                raise ValueError('File exceeds fingerprint byte budget')
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino) or count != after.st_size:
        raise ValueError('File changed while fingerprinting')
    return digest.hexdigest()


def binary_architectures(path: Path) -> list[str]:
    """Read ELF/Mach-O headers; this identifies target ISA, not executed SIMD/GPU code."""
    with path.open('rb') as stream:
        header = stream.read(512)
    if header[:4] == b'\x7fELF' and len(header) >= 20 and header[5] in (1, 2):
        machine = struct.unpack('<H' if header[5] == 1 else '>H', header[18:20])[0]
        return [{183: 'arm64', 62: 'x86_64', 40: 'arm', 3: 'x86'}.get(machine, f'elf-{machine}')]
    magic = header[:4]
    thin = {b'\xcf\xfa\xed\xfe': '<', b'\xfe\xed\xfa\xcf': '>',
            b'\xce\xfa\xed\xfe': '<', b'\xfe\xed\xfa\xce': '>'}
    cpu_names = {0x0100000c: 'arm64', 0x01000007: 'x86_64', 12: 'arm', 7: 'x86'}
    if magic in thin and len(header) >= 8:
        cpu = struct.unpack(thin[magic] + 'I', header[4:8])[0]
        return [cpu_names.get(cpu, f'macho-{cpu}')]
    fat = {b'\xca\xfe\xba\xbe': ('>', 20), b'\xbe\xba\xfe\xca': ('<', 20),
           b'\xca\xfe\xba\xbf': ('>', 32), b'\xbf\xba\xfe\xca': ('<', 32)}
    if magic in fat and len(header) >= 8:
        endian, width = fat[magic]
        count = struct.unpack(endian + 'I', header[4:8])[0]
        if not 1 <= count <= 8 or 8 + count * width > len(header):
            return ['unrecognized']
        return sorted({cpu_names.get(struct.unpack(endian + 'I', header[8 + i * width:12 + i * width])[0],
                                     'unrecognized') for i in range(count)})
    return ['unrecognized']


def _sysctl(key: str) -> str | None:
    try:
        result = subprocess.run(['sysctl', '-n', key], capture_output=True, text=True, timeout=2, check=False)
        return result.stdout.strip()[:160] if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def package_fingerprint(name: str) -> dict:
    """Fingerprint wheel metadata and one principal native extension per package."""
    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return {'status': 'not-installed'}
    wheel = distribution.read_text('WHEEL') or ''
    result = {'version': distribution.version, 'wheel_metadata_sha256': hashlib.sha256(wheel.encode()).hexdigest(),
              'wheel_tags': [line[5:] for line in wheel.splitlines() if line.startswith('Tag: ')]}
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    commit = (direct.get('vcs_info') or {}).get('commit_id')
    if commit:
        result['source_commit'] = commit
    natives = [file for file in distribution.files or [] if str(file).endswith(('.so', '.pyd', '.dylib'))]
    preferred = {'tree-sitter-language-pack': '_native', 'ladybug': '_lbug', 'lancedb': '_lancedb', 'pyarrow': 'lib.'}
    natives.sort(key=lambda file: (not file.name.startswith(preferred[name]), str(file)))
    if natives:
        path = Path(distribution.locate_file(natives[0])).resolve()
        try:
            result['native_binary'] = {'name': natives[0].name, 'bytes': path.stat().st_size,
                                       'architectures': binary_architectures(path), 'sha256': sha256_file(path)}
        except (OSError, ValueError) as error:
            result['native_binary'] = {'name': natives[0].name, 'status': type(error).__name__}
    else:
        result['native_binary'] = {'status': 'not-found'}
    return result


def runtime_hardware(*, wheel: Path | None = None) -> dict:
    """Return safe hardware identity and exact installed native artifacts."""
    machine = platform.machine()
    hardware = {'system': platform.system(), 'machine': machine, 'arm64_process': machine in {'arm64', 'aarch64'},
                'logical_cpus': os.cpu_count()}
    if platform.system() == 'Darwin':
        for field, key in (('cpu_model', 'machdep.cpu.brand_string'), ('memory_bytes', 'hw.memsize'),
                           ('performance_cores', 'hw.perflevel0.physicalcpu'),
                           ('efficiency_cores', 'hw.perflevel1.physicalcpu'), ('rosetta_translated', 'sysctl.proc_translated')):
            value = _sysctl(key)
            hardware[field] = int(value) if value is not None and value.isdecimal() else value
    result = {'hardware': hardware, 'python': {'version': platform.python_version(),
              'implementation': platform.python_implementation(),
              'executable_architectures': binary_architectures(Path(sys.executable))},
              'packages': {name: package_fingerprint(name) for name in NATIVE_PACKAGES},
              'acceleration': {'gpu_backend': 'not-probed', 'inference_runtime': 'not-probed',
                               'simd_execution': 'not-measured', 'ane_execution': 'not-measured'}}
    if wheel is not None:
        result['built_wheel'] = {'name': wheel.name, 'sha256': sha256_file(wheel), 'bytes': wheel.stat().st_size}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, help='Optional exact built wheel to fingerprint')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rendered = json.dumps(runtime_hardware(wheel=args.wheel), indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')


if __name__ == '__main__':
    main()
