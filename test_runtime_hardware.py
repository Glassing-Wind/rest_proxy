"""Offline fingerprint tests; no service/model/environment credentials required."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from scripts.runtime_hardware import binary_architectures, runtime_hardware, sha256_file


class HardwareFingerprint(unittest.TestCase):
    def test_native_arm_and_universal_headers(self):
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        elf[18:20] = struct.pack('<H', 183)
        macho = b'\xcf\xfa\xed\xfe' + struct.pack('<I', 0x0100000c)
        fat = b'\xca\xfe\xba\xbe' + struct.pack('>I', 2)
        fat += struct.pack('>IIIII', 0x0100000c, 0, 0, 0, 0)
        fat += struct.pack('>IIIII', 0x01000007, 0, 0, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'binary'
            for raw, expected in ((elf, ['arm64']), (macho, ['arm64']),
                                  (fat, ['arm64', 'x86_64']), (b'unknown', ['unrecognized']),
                                  (fat[:10], ['unrecognized'])):
                with self.subTest(expected=expected, size=len(raw)):
                    path.write_bytes(raw)
                    self.assertEqual(binary_architectures(path), expected)

    def test_hash_budget_and_runtime_do_not_attest_acceleration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'binary'
            path.write_bytes(b'abc')
            self.assertEqual(sha256_file(path), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            with self.assertRaisesRegex(ValueError, 'byte budget'):
                sha256_file(path, limit=2)
        with mock.patch('scripts.runtime_hardware.package_fingerprint', return_value={'status': 'test'}), \
                mock.patch('scripts.runtime_hardware._sysctl', return_value=None):
            result = runtime_hardware()
        self.assertEqual(result['acceleration']['gpu_backend'], 'not-probed')
        self.assertEqual(result['acceleration']['simd_execution'], 'not-measured')
        self.assertNotIn('environment', result)


if __name__ == '__main__':
    unittest.main()
