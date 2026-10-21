"""Offline ownership/pinning/lifecycle checks; interfaces, launchctl and GOST are mocked."""

import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.manage_forward_gateway import (
    GatewayError, GatewayManager, GatewayPaths, LABEL, VERSION, canonical_config, gateway_config,
)


class GatewayManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.paths = GatewayPaths(root / "repo", root / "home")
        self.paths.runtime.mkdir(parents=True, mode=0o700)
        self.paths.binary.parent.mkdir(mode=0o700)
        self.paths.source_config.parent.mkdir(mode=0o700)
        (self.paths.home / "Library").mkdir(parents=True, mode=0o700)
        self.paths.binary.write_bytes(b"synthetic pinned executable; never invoked")
        self.paths.binary.chmod(0o700)
        self.paths.source_config.write_text(json.dumps(gateway_config()))
        self.manifest = {
            "schema_version": 1, "version": VERSION, "platform": "darwin", "architecture": "arm64",
            "binary_sha256": hashlib.sha256(self.paths.binary.read_bytes()).hexdigest(),
            "binary_path": str(self.paths.binary),
        }
        self.paths.manifest.write_text(json.dumps(self.manifest))
        self.calls = []
        self.loaded = False
        self.loaded_path = str(self.paths.plist)
        self.manager = GatewayManager(self.paths, runner=self.runner)
        self.platform_patch = patch("scripts.manage_forward_gateway.platform.system", return_value="Darwin")
        self.arch_patch = patch("scripts.manage_forward_gateway.platform.machine", return_value="arm64")
        self.interface_patch = patch("scripts.manage_forward_gateway.socket.if_nametoindex", return_value=7)
        self.platform_patch.start()
        self.arch_patch.start()
        self.interface_lookup = self.interface_patch.start()
        self.addCleanup(self.platform_patch.stop)
        self.addCleanup(self.arch_patch.stop)
        self.addCleanup(self.interface_patch.stop)

    def runner(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[-1] == "-V":
            return subprocess.CompletedProcess(args, 0, f"gost v{VERSION} (go1.26.3 darwin/arm64)\n", "")
        if args[1] == "print":
            output = f"job = {{\n path = {self.loaded_path}\n state = running\n pid = 123\n}}"
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113, output, "")
        if args[1] == "bootstrap":
            self.loaded = True
        elif args[1] == "bootout":
            self.loaded = False
        else:
            self.fail("Unexpected subprocess action")
        return subprocess.CompletedProcess(args, 0, "", "")

    def test_install_is_private_idempotent_and_never_starts(self):
        self.assertFalse(self.manager.install()["started"])
        self.assertFalse(self.loaded)
        self.assertEqual(self.paths.plist.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.paths.runtime_config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.paths.plist.parent.stat().st_mode & 0o777, 0o700)
        before = (self.paths.plist.stat().st_ino, self.paths.runtime_config.stat().st_ino)
        self.manager.install()
        self.assertEqual(before, (self.paths.plist.stat().st_ino, self.paths.runtime_config.stat().st_ino))
        plist = plistlib.loads(self.paths.plist.read_bytes())
        self.assertEqual(plist["Label"], LABEL)
        self.assertEqual(plist["ProgramArguments"], ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
                                                   str(self.paths.binary), "-C", str(self.paths.runtime_config)])
        self.assertTrue(plist["KeepAlive"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["StandardErrorPath"], "/dev/null")
        self.assertEqual(plist["ThrottleInterval"], 10)
        runtime = json.loads(self.paths.runtime_config.read_bytes())
        self.assertEqual(runtime["services"][0]["handler"], {
            "type": "http", "metadata": {"udp": False, "sniffing": False},
        })
        self.assertEqual(runtime["services"][0]["metadata"], {"interface": "en0"})
        self.assertEqual(runtime["services"][0]["resolver"], "existing-wifi-dns")
        self.assertEqual(runtime["resolvers"], [{"name": "existing-wifi-dns",
                                               "nameservers": [{"addr": "udp://192.168.1.1:53"}]}])
        self.assertEqual([args[-1] for args, _ in self.calls], ["-V", "-V"])

    def test_lifecycle_targets_only_owned_label_and_keeps_binary_evidence(self):
        self.assertFalse(self.manager.status()["installed"])
        self.manager.install()
        self.assertTrue(self.manager.start()["running"])
        self.manager.start()
        self.assertEqual(sum(args[1] == "bootstrap" for args, _ in self.calls if len(args) > 2), 1)
        self.assertTrue(self.manager.stop()["installed"])
        self.assertFalse(self.loaded)
        self.manager.start()
        self.assertFalse(self.manager.remove()["installed"])
        self.assertFalse(self.loaded)
        self.assertFalse(self.paths.plist.exists())
        self.assertFalse(self.paths.runtime_config.exists())
        self.assertTrue(self.paths.binary.exists())
        self.assertTrue(self.paths.manifest.exists())
        for args, kwargs in self.calls:
            self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin"})
            if len(args) > 2 and args[1] in ("print", "bootout"):
                self.assertEqual(args[2], f"gui/{os.getuid()}/{LABEL}")

    def test_tampered_binary_or_manifest_never_executes_or_installs(self):
        self.paths.binary.write_bytes(b"tampered")
        with self.assertRaisesRegex(GatewayError, "fingerprint"):
            self.manager.install()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.paths.plist.exists())
        self.manifest["binary_path"] = "/tmp/another-program"
        self.paths.manifest.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(GatewayError, "manifest"):
            self.manager.install()
        self.assertEqual(self.calls, [])

    def test_version_mismatch_does_not_create_service(self):
        for banner in ("gost v3.3.0", f"gost v{VERSION}-dev", f"unverified gost v{VERSION}",
                       f"gost v{VERSION}0"):
            self.manager.runner = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, banner, "")
            with self.subTest(banner=banner), self.assertRaisesRegex(GatewayError, "version"):
                self.manager.install()
        self.assertFalse(self.paths.runtime_config.exists())
        self.assertFalse(self.paths.plist.exists())

    def test_unrelated_plist_and_runtime_config_are_preserved(self):
        self.paths.plist.parent.mkdir(mode=0o700)
        unrelated = b"unrelated user LaunchAgent"
        self.paths.plist.write_bytes(unrelated)
        with self.assertRaises(GatewayError):
            self.manager.install()
        with self.assertRaises(GatewayError):
            self.manager.stop()
        self.assertEqual(self.paths.plist.read_bytes(), unrelated)
        self.assertFalse(self.paths.runtime_config.exists())
        self.paths.plist.unlink()
        self.paths.runtime_config.write_bytes(b"unrelated runtime file")
        with self.assertRaises(GatewayError):
            self.manager.install()
        with self.assertRaises(GatewayError):
            self.manager.remove()
        self.assertEqual(self.paths.runtime_config.read_bytes(), b"unrelated runtime file")

    def test_symlinked_file_and_parent_are_refused_without_touching_target(self):
        original = self.paths.binary.read_bytes()
        target = self.paths.runtime / "target"
        self.paths.binary.rename(target)
        self.paths.binary.symlink_to(target)
        with self.assertRaisesRegex(GatewayError, "Symlink"):
            self.manager.install()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.calls, [])
        self.paths.binary.unlink()
        target.rename(self.paths.binary)
        redirected = self.paths.home / "redirected"
        redirected.mkdir()
        self.paths.plist.parent.symlink_to(redirected, target_is_directory=True)
        with self.assertRaisesRegex(GatewayError, "Symlink"):
            self.manager.install()
        self.assertEqual(list(redirected.iterdir()), [])

    def test_changed_config_cannot_enable_capture_or_wildcard_listener(self):
        for change in ("recorders", "listener", "log", "chain", "udp", "interface_missing",
                       "interface_unbound", "interface_wrong", "interface_abort_first_address",
                       "resolver_missing", "resolver_changed"):
            config = copy.deepcopy(gateway_config())
            if change == "recorders":
                config["recorders"] = [{"name": "capture"}]
            elif change == "listener":
                config["services"][0]["addr"] = "0.0.0.0:18080"
            elif change == "log":
                config["log"]["output"] = "stderr"
            elif change == "chain":
                config["services"][0]["handler"]["chain"] = "loop"
            elif change == "udp":
                config["services"][0]["handler"]["metadata"]["udp"] = True
            elif change == "interface_missing":
                del config["services"][0]["metadata"]
            elif change == "resolver_missing":
                del config["services"][0]["resolver"]
            elif change == "resolver_changed":
                config["resolvers"][0]["nameservers"][0]["addr"] = "udp://198.18.0.1:53"
            else:
                replacement = {"interface_unbound": "", "interface_wrong": "utun7",
                               "interface_abort_first_address": "en0!"}[change]
                config["services"][0]["metadata"]["interface"] = replacement
            with self.subTest(change=change), self.assertRaises(GatewayError):
                canonical_config(config)
        self.manager.install()
        self.paths.runtime_config.write_bytes(b"changed policy")
        with self.assertRaisesRegex(GatewayError, "configuration"):
            self.manager.start()
        self.assertFalse(self.loaded)

    def test_socks_policy_refuses_public_relay_bind_or_unbound_egress(self):
        for change in ("listener", "bind", "public_relay", "source_ip", "udp_disabled"):
            config = copy.deepcopy(gateway_config())
            socks = config["services"][1]
            if change == "listener":
                socks["addr"] = "0.0.0.0:18081"
            elif change == "bind":
                socks["handler"]["metadata"]["bind"] = True
            elif change == "public_relay":
                socks["handler"]["metadata"]["udp.publicAddr"] = "0.0.0.0"
            elif change == "source_ip":
                socks["metadata"]["interface"] = "192.168.1.100"
            else:
                socks["handler"]["metadata"]["udp"] = False
            with self.subTest(change=change), self.assertRaises(GatewayError):
                canonical_config(config)

    def test_missing_interface_prevents_install_and_start_even_if_loaded(self):
        self.interface_lookup.side_effect = OSError("synthetic missing interface")
        with self.assertRaisesRegex(GatewayError, "interface en0 is unavailable"):
            self.manager.install()
        self.interface_lookup.assert_called_once_with("en0")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.paths.plist.exists())
        self.assertFalse(self.paths.runtime_config.exists())
        self.interface_lookup.side_effect = None
        self.manager.install()
        self.interface_lookup.side_effect = OSError("synthetic missing interface")
        before = list(self.calls)
        for loaded in (False, True):
            self.loaded = loaded
            with self.subTest(loaded=loaded), self.assertRaisesRegex(GatewayError, "interface en0 is unavailable"):
                self.manager.start()
            self.assertEqual(self.calls, before)
            self.assertEqual(self.loaded, loaded)

    def test_loaded_job_from_another_plist_is_not_stopped(self):
        self.manager.install()
        self.loaded = True
        self.loaded_path = "/tmp/unrelated.plist"
        with self.assertRaisesRegex(GatewayError, "different LaunchAgent"):
            self.manager.stop()
        self.assertTrue(self.loaded)
        self.assertFalse(any(args[1] == "bootout" for args, _ in self.calls if len(args) > 2))

    def test_unknown_launch_state_or_missing_owned_plist_prevents_removal(self):
        self.manager.install()
        self.manager.runner = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "private error")
        with self.assertRaisesRegex(GatewayError, "state is unavailable"):
            self.manager.remove()
        self.assertTrue(self.paths.plist.exists())
        self.assertTrue(self.paths.runtime_config.exists())
        self.manager.runner = self.runner
        self.loaded = True
        self.paths.plist.unlink()
        with self.assertRaisesRegex(GatewayError, "without its owned"):
            self.manager.remove()
        self.assertTrue(self.paths.runtime_config.exists())
        self.assertTrue(self.loaded)


if __name__ == "__main__":
    unittest.main()
