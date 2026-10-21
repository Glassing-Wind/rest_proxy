"""Manage only the owned loopback GOST LaunchAgent; never download or load app config.

GOST v3 references: https://v3.gost.run/en/reference/configuration/file/
https://v3.gost.run/en/reference/handlers/http/
https://v3.gost.run/en/reference/handlers/socks5/
https://v3.gost.run/en/concepts/bypass/
https://v3.gost.run/en/tutorials/multi-homed/
https://v3.gost.run/en/concepts/resolver/
Service bypass matches the requested address, not post-DNS private addresses.
The port denies also cover hostname aliases for this gateway and Shadowrocket.
TCP retries addresses on one physical interface; its vendor path ignores binding errors.
The locally patched Darwin UDP association uses a dual-stack interface-bound socket.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import socket
import stat
import subprocess


LABEL = "com.michaelmarler.rest-proxy-forward-gateway"
VERSION = "3.3.0-restproxy-darwin-udp1"
EGRESS_INTERFACE = "en0"
WIFI_DNS = "udp://192.168.1.1:53"
BLOCKED_DESTINATIONS = (
    "*:1082", "*:18080", "*:18081",
    "localhost", "localhost.", ".localhost", "*.localhost", "localhost.localdomain",
    "*.localhost.localdomain", "*.local",
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    "::/96", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64",
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20", "5f00::/16",
    "fc00::/7", "fe80::/10", "ff00::/8",
)


class GatewayError(ValueError):
    """Safe local-management failure; never includes traffic or subprocess output."""


def gateway_config() -> dict:
    return {
        "services": [{
            "name": "rest-proxy-forward-gateway",
            "addr": "127.0.0.1:18080",
            "bypass": "deny-local-and-reentry",
            "metadata": {"interface": EGRESS_INTERFACE},
            "resolver": "existing-wifi-dns",
            "handler": {
                "type": "http",
                "metadata": {"udp": False, "sniffing": False},
            },
            "listener": {"type": "tcp"},
        }, {
            "name": "rest-proxy-socks-gateway",
            "addr": "127.0.0.1:18081",
            "bypass": "deny-local-and-reentry",
            "metadata": {"interface": EGRESS_INTERFACE},
            "resolver": "existing-wifi-dns",
            "handler": {
                "type": "socks5",
                "metadata": {
                    "udp": True, "notls": True, "bind": False,
                    "sniffing": False, "udpBufferSize": 65535,
                },
            },
            "listener": {"type": "tcp"},
        }],
        "bypasses": [{
            "name": "deny-local-and-reentry", "whitelist": False,
            "matchers": list(BLOCKED_DESTINATIONS),
        }],
        "resolvers": [{
            "name": "existing-wifi-dns",
            "nameservers": [{"addr": WIFI_DNS}],
        }],
        "log": {"output": "none", "level": "error"},
    }


def canonical_config(config: dict) -> bytes:
    if config != gateway_config():
        raise GatewayError("Configuration differs from the fixed loopback gateway policy")
    return (json.dumps(config, indent=2, sort_keys=True) + "\n").encode("utf-8")


def safe_path(path: Path):
    """Reject symlinks in every existing component instead of resolving through them."""
    if not path.is_absolute() or ".." in path.parts:
        raise GatewayError("An absolute path without parent traversal is required")
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise GatewayError("Symlink paths are not permitted for gateway artifacts")
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise GatewayError("A gateway parent path is not a directory")


def read_file(path: Path, *, limit: int = 1024 * 1024) -> bytes:
    safe_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise GatewayError("Required gateway artifact is unavailable") from exc
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise GatewayError("Gateway artifact must be a regular owned file without shared writes")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise GatewayError("Gateway artifact exceeds its size limit")
    return data


def private_directory(path: Path):
    safe_path(path)
    if not path.exists():
        if not path.parent.exists():
            private_directory(path.parent)
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise GatewayError("A gateway directory is not owned by the current user")
    # Existing shared parent directories are checked by safe_path, not chmodded.
    if info.st_mode & 0o077:
        raise GatewayError("Owned gateway directories must have private permissions (0700)")


def write_new(path: Path, content: bytes):
    safe_path(path)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise GatewayError("Refusing to replace an existing gateway artifact") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True)
class GatewayPaths:
    repo: Path
    home: Path

    @property
    def runtime(self):
        return self.repo / ".runtime" / "forward-gateway"

    @property
    def binary(self):
        return self.runtime / "bin" / "gost-darwin-udp1"

    @property
    def manifest(self):
        return self.runtime / "udp1-install-manifest.json"

    @property
    def source_config(self):
        return self.repo / "config" / "forward_gateway.json"

    @property
    def runtime_config(self):
        return self.runtime / "gost.json"

    @property
    def plist(self):
        return self.home / "Library" / "LaunchAgents" / (LABEL + ".plist")


class GatewayManager:
    def __init__(self, paths: GatewayPaths, *, runner=subprocess.run):
        self.paths = paths
        self.runner = runner
        safe_path(paths.repo)
        safe_path(paths.home)

    @property
    def service_target(self):
        return f"gui/{os.getuid()}/{LABEL}"

    def _run(self, arguments):
        try:
            return self.runner(arguments, capture_output=True, text=True, timeout=10,
                               env={"PATH": "/usr/bin:/bin"}, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GatewayError("Gateway management command could not complete") from exc

    def _config_bytes(self):
        try:
            config = json.loads(read_file(self.paths.source_config))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GatewayError("Gateway source configuration is invalid") from exc
        return canonical_config(config)

    def plist_content(self) -> dict:
        return {
            "Label": LABEL,
            "Comment": "Owned rest_proxy loopback forward gateway: " + str(self.paths.repo),
            # Empty environment avoids ambient GOST_API/GOST_METRICS/proxy overrides.
            "ProgramArguments": ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
                                 str(self.paths.binary), "-C", str(self.paths.runtime_config)],
            "WorkingDirectory": str(self.paths.runtime),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "Umask": 0o077,
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
        }

    def _plist_bytes(self):
        return plistlib.dumps(self.plist_content(), sort_keys=True)

    def _assert_owned_plist(self):
        if read_file(self.paths.plist) != self._plist_bytes():
            raise GatewayError("Refusing to manage an unrelated or changed LaunchAgent")

    def verify_binary(self):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise GatewayError("This pinned gateway requires macOS arm64")
        try:
            manifest = json.loads(read_file(self.paths.manifest))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GatewayError("Pinned gateway installation manifest is invalid") from exc
        required = {"schema_version", "version", "platform", "architecture", "binary_sha256", "binary_path"}
        if (not isinstance(manifest, dict) or set(manifest) != required or
                type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or
                manifest["version"] != VERSION or manifest["platform"] != "darwin" or
                manifest["architecture"] != "arm64" or manifest["binary_path"] != str(self.paths.binary) or
                not isinstance(manifest["binary_sha256"], str) or
                re.fullmatch(r"[0-9a-f]{64}", manifest["binary_sha256"]) is None):
            raise GatewayError("Pinned gateway installation manifest does not match this installation")
        safe_path(self.paths.binary)
        try:
            descriptor = os.open(self.paths.binary, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise GatewayError("Pinned gateway binary is unavailable") from exc
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    info.st_mode & 0o022 or not info.st_mode & stat.S_IXUSR):
                raise GatewayError("Pinned gateway binary has unsafe ownership or permissions")
            while block := handle.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != manifest["binary_sha256"]:
            raise GatewayError("Pinned gateway binary fingerprint does not match")
        result = self._run([str(self.paths.binary), "-V"])
        banner = r"gost (?:v|version )" + re.escape(VERSION) + r"(?: \([^\r\n]*\))?\s*"
        if result.returncode or re.fullmatch(banner, result.stdout) is None:
            raise GatewayError("Pinned gateway binary reported an unexpected version")

    def verify_interface(self):
        try:
            socket.if_nametoindex(EGRESS_INTERFACE)
        except OSError as exc:
            raise GatewayError("Required physical interface en0 is unavailable; gateway cannot start") from exc

    def install(self):
        self.verify_interface()
        self.verify_binary()
        content = self._config_bytes()
        plist = self._plist_bytes()
        # Validate all existing files before writing either artifact.
        for path, expected in ((self.paths.runtime_config, content), (self.paths.plist, plist)):
            safe_path(path)
            if path.exists() and read_file(path) != expected:
                raise GatewayError("Refusing to replace an unrelated or changed gateway artifact")
        private_directory(self.paths.runtime)
        # LaunchAgents may already be a standard shared-permission user directory.
        safe_path(self.paths.plist.parent)
        if not self.paths.plist.parent.exists():
            private_directory(self.paths.plist.parent)
        directory = self.paths.plist.parent.stat()
        if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
            raise GatewayError("LaunchAgents must be owned by the user without shared writes")
        created = []
        try:
            for path, expected in ((self.paths.runtime_config, content), (self.paths.plist, plist)):
                if not path.exists():
                    write_new(path, expected)
                    created.append((path, expected))
        except BaseException:
            for path, expected in reversed(created):
                if read_file(path) == expected:
                    path.unlink()
            raise
        return {"installed": True, "started": False, "label": LABEL}

    def status(self):
        safe_path(self.paths.plist)
        installed = self.paths.plist.exists()
        if installed:
            self._assert_owned_plist()
        result = self._run(["/bin/launchctl", "print", self.service_target])
        if result.returncode:
            if result.returncode != 113:
                raise GatewayError("LaunchAgent state is unavailable; refusing a lifecycle change")
            return {"installed": installed, "loaded": False, "running": False, "label": LABEL}
        if not installed:
            raise GatewayError("Refusing to manage a loaded service without its owned LaunchAgent")
        expected_path = str(self.paths.plist)
        path_match = re.search(r"^\s*path = (.+)$", result.stdout, re.MULTILINE)
        if path_match is None or path_match.group(1).strip('"') != expected_path:
            raise GatewayError("Refusing to manage a loaded service from a different LaunchAgent")
        state = re.search(r"^\s*state = ([a-zA-Z]+)$", result.stdout, re.MULTILINE)
        pid = re.search(r"^\s*pid = ([0-9]+)$", result.stdout, re.MULTILINE)
        return {"installed": True, "loaded": True,
                "running": bool(state and state.group(1) == "running" and pid),
                "pid": int(pid.group(1)) if pid else None, "label": LABEL}

    def start(self):
        self.verify_interface()
        self._assert_owned_plist()
        self.verify_binary()
        if read_file(self.paths.runtime_config) != self._config_bytes():
            raise GatewayError("Installed gateway configuration differs from the fixed policy")
        current = self.status()
        if current["loaded"]:
            return current
        result = self._run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(self.paths.plist)])
        if result.returncode:
            raise GatewayError("The owned gateway LaunchAgent could not be started")
        return self.status()

    def stop(self):
        current = self.status()
        if current["loaded"]:
            result = self._run(["/bin/launchctl", "bootout", self.service_target])
            if result.returncode:
                raise GatewayError("The owned gateway LaunchAgent could not be stopped")
        return {"installed": current["installed"], "loaded": False, "running": False, "label": LABEL}

    def remove(self):
        safe_path(self.paths.runtime_config)
        if self.paths.runtime_config.exists() and read_file(self.paths.runtime_config) != self._config_bytes():
            raise GatewayError("Refusing to remove an unrelated or changed gateway configuration")
        self.stop()
        if self.paths.plist.exists():
            self._assert_owned_plist()
            self.paths.plist.unlink()
        if self.paths.runtime_config.exists():
            self.paths.runtime_config.unlink()
        # Keep the pinned binary/manifest; remove never deletes installation evidence.
        return {"installed": False, "loaded": False, "running": False, "label": LABEL}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "start", "status", "stop", "remove"))
    args = parser.parse_args()
    paths = GatewayPaths(Path(__file__).absolute().parents[1], Path.home())
    try:
        print(json.dumps(getattr(GatewayManager(paths), args.action)(), sort_keys=True))
    except GatewayError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
