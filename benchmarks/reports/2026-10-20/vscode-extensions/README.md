# Local extension review

The [inventory](inventory.json) contains 70 registered packages across 67 unique
extension IDs, read from the local VS Code registry and their manifests on
October 20, 2026. It excludes credentials, full settings, chat content, logs,
machine IDs and extension installation paths. Bundled Copilot Chat 0.70.0 was
observed separately in the application bundle, not this user-extension registry.

The exact screenshot warning is present in C# Dev Kit 3.40.215's installed
`dist/strings.nls.metadata.json`. The extension-host log for the session beginning
08:03:45 records the error at 09:51:00.270 under `csdevkit.openSolution`;
the screenshot filename is timestamped 09:51:11. Earlier identical errors were
recorded at 09:36:50.975 and 09:36:51.736. Repository source discovery found no
checked-in `.sln`, `.slnx`, `.csproj` or `.cs` file. No raw session logs are copied
into this report.

See the [diagnosis and recommended roles](../../../../docs/vscode-extensions.md).
This is an inventory and concrete command diagnosis, not a security audit or an
attestation that each package is enabled or correctly authenticated. No extension
installation, removal, disabling or global preference changes were performed.
