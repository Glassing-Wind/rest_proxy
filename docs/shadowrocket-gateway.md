# Shadowrocket local gateway — October 10, 2026

System-wide TCP and UDP forwarding passed live IPv4 and IPv6 checks on this Mac's
current Wi-Fi connection. Raw socket probes ignored HTTP proxy libraries and
settings. Ordinary TLS validation passed, public DNS and non-DNS UDP time-server
requests returned valid replies, and local GraphRAG remained reachable. A temporary
gateway-only deny policy made the same public TCP and UDP probes fail in both
families; normal policy restored successful requests. This establishes gateway
attribution beyond merely observing a successful internet connection.

Shadowrocket is left connected to **REST Proxy TCP + UDP**, in Config routing mode.

## Saved settings

| Component | Setting |
| --- | --- |
| Active Shadowrocket server | SOCKS5; 127.0.0.1:18081; UDP Relay enabled; no credentials/plugin |
| HTTP fallback server | REST Proxy Gateway; HTTP/AUTO; 127.0.0.1:18080 |
| Active profile | shadowrocket-rest-proxy.conf; local/private destinations DIRECT, FINAL PROXY |
| Compatibility Mode | Enabled; sampled public IPv4/IPv6 kernel routes use Shadowrocket's tunnel |
| Local app proxy | HTTP on 127.0.0.1:1082; sharing disabled |
| Gateway listeners | HTTP 127.0.0.1:18080 and SOCKS5 127.0.0.1:18081 |
| Gateway egress | Physical Wi-Fi interface en0 for TCP and dual-stack UDP |
| Gateway DNS | Existing Wi-Fi router udp://192.168.1.1:53 |
| LaunchAgent | com.michaelmarler.rest-proxy-forward-gateway; RunAtLoad and KeepAlive |

SOCKS UDP client relays bind to loopback and close with their TCP association.
The UDP outbound socket uses a wildcard dual-stack address bound to en0; this
separate outbound socket does not expose the SOCKS control or relay to the LAN.
SOCKS BIND is disabled. HTTPS decryption, sniffing, capture, traffic logging,
metrics, proxy chains and ingestion are disabled. The gateway forwards network
traffic; it does not give encrypted application contents to an AI model or corpus.

## Implementation and checks

Stock GOST v3.3.0's named-interface UDP association selected the first source
address and skipped macOS interface binding. On this Mac, that failed UDP even
with the tunnel off. An explicit Wi-Fi IPv4 source passed IPv4 but broke IPv6.
The local **3.3.0-restproxy-darwin-udp1** build fixes only the named-interface,
empty-destination UDP path on Darwin: create a dual-stack socket, bind its actual
family to the interface, and close/return on binding errors. Explicit source-IP
and other OS behavior are preserved. TCP keeps the existing interface binding
and explicit Wi-Fi DNS that previously removed routing and DNS loops.

- Four Darwin Go tests passed: actual interface socket option, both loopback
  families, missing interface, post-creation binding failure, and explicit-IP behavior.
- Eleven offline manager tests and focused Ruff passed.
- Independent static review found no required correction to the narrow patch.
- Live SOCKS TCP/TLS, UDP DNS, non-DNS UDP in both families, loopback relay identity
  and association closure passed.
- Raw app TCP/TLS and non-DNS UDP passed in both families with Shadowrocket on.
- A temporary gateway-only deny control confirmed raw-app TCP/UDP route attribution;
  it was removed and is absent from installed policy.
- HTTP proxy chain through Shadowrocket to SOCKS gateway passed with both local
  peers observed; GraphRAG health through the DIRECT rule returned HTTP 200.
- Both private listeners and the running owned LaunchAgent were verified.

One initial CONNECT failed during tunnel startup; after the connection settled,
normal TLS/HTTP and DIRECT health passed. The short tests automatically disconnected
Shadowrocket on failure. The scoped macOS service-stop rollback was rehearsed.
No logout/login, reboot or network change was tested. No commit, push or deployment.

## Recovery

Turn Shadowrocket's Home switch off to restore ordinary networking. Its original
default.conf and HTTP server were preserved. Disconnect before stopping the gateway.

From /Users/michaelmarler/Projects/rest_proxy:

```sh
.venv/bin/python scripts/manage_forward_gateway.py status
.venv/bin/python scripts/manage_forward_gateway.py stop
.venv/bin/python scripts/manage_forward_gateway.py start
.venv/bin/python scripts/manage_forward_gateway.py remove
```

The manager checks ownership, executable fingerprint and exact two-service policy.
It refuses changed files, unrelated launch agents or a missing en0. Remove retains
both build evidence and binaries. Changes require an explained remove/install
sequence; it does not silently overwrite existing service artifacts.

The original vendor executable and manifest are retained in .runtime/forward-gateway.
The active executable is bin/gost-darwin-udp1 with udp1-install-manifest.json.
The original TCP-only configuration and manager have a private rollback snapshot
in this chat's work/gateway-before-udp directory. Restoring that version requires
stopping/removing the current owned service before restoring its matching files,
then install/start and selecting the preserved HTTP server. Binary replacement
alone is not the rollback procedure.

## Scope

The result establishes representative TCP and UDP app traffic on the current
network, including IPv4 and IPv6. It does not certify every app, ICMP, HTTP/3/QUIC,
OS tunnel exemptions or a fail-closed security boundary. Local/LAN destinations
intentionally remain DIRECT. Recheck after Wi-Fi/interface or DNS changes.
Requested-address denies are not post-DNS private-IP validation. Stock TCP binding
still logs but ignores socket-binding errors; the UDP patch propagates its own
binding failures. No privacy-isolation or public-listener security guarantee is made.

## Rebuild identity

The reproducible source inputs and checksum record are in
config/forward_gateway_build.json; the source patch plus Darwin tests are in
scripts/patches/gost-darwin-udp.patch.

- GOST v3.3.0 commit: cb76f63754768c7b5d68895a0d51635b0141b80f
- x v0.16.0 commit: 14fed91c6245289e2e92ea60a97332a62c90fad0
- Toolchain: go1.26.3 darwin/arm64
- Patched executable SHA-256: d6883edd609d425732dd49b14bd7eba7083a71b431c68acc3fcb57babb701e19
- Patch SHA-256: 78a3cf28532478730d43a950c4bc03b24a0b7de60d8f2dd87c324f0eba33defc
- Original vendor archive SHA-256: f170226106844b50ab3435147f35d4300da773efca256b50cb2f8c7d3a151101
- Original vendor executable SHA-256: 30ab217ba7c18df56d1bcc08969b6de5bbcbe0d9bfa351b05f7996908d9b2058

To rebuild in a disposable work directory, clone the official repositories at the
recorded tags, verify both commits, apply the patch to x, and run its Darwin tests.
In the GOST checkout:

```sh
go mod edit -replace github.com/go-gost/x=../gost-x-v0.16.0
go build -trimpath -ldflags '-s -w -X main.version=v3.3.0-restproxy-darwin-udp1' -o ../gost-darwin-udp1 ./cmd/gost
```

Source: [official GOST](https://github.com/go-gost/gost),
[SOCKS5 handler](https://v3.gost.run/en/reference/handlers/socks5/),
[pinned UDP relay](https://raw.githubusercontent.com/go-gost/x/v0.16.0/handler/socks/v5/udp.go),
[pinned dialer](https://raw.githubusercontent.com/go-gost/x/v0.16.0/internal/net/dialer/dialer.go),
[Darwin binding](https://raw.githubusercontent.com/go-gost/x/v0.16.0/internal/net/dialer/dialer_darwin.go),
[Cloudflare's public UDP test endpoints](https://developers.cloudflare.com/time-services/ntp/usage/).
