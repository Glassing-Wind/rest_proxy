# VS Code extensions and the solution warning

Inspected October 20, 2026. The [sanitized inventory](../benchmarks/reports/2026-10-20/vscode-extensions/inventory.json)
records local IDs, names and versions. A registered package is not proof it is
active, authenticated, secure or connected to our task system. No extensions were
installed, removed or disabled for this review.

## Why “No solution file” appeared

The exact screenshot message belongs to **Microsoft C# Dev Kit 3.40.215**.
Its installed `dist/strings.nls.metadata.json` contains the text; the local
extension-host log records the error at **09:51:00** on October 20, with command
`csdevkit.openSolution`. The screenshot was taken at 09:51:11. Earlier occurrences
were recorded at 09:36:50 and 09:36:51. The repository contains Python packaging,
and no checked-in `.sln`, `.slnx`, `.csproj` or `.cs` files.

This is a .NET **Open Solution** action in a Python workspace. It does not indicate
that rest_proxy lacks a backend or that GraphRAG failed. C# Dev Kit had earlier
activated for its NuGet MCP collection; that explains an activation path, not who
invoked the later Open Solution command. The log does not establish that every
extension ran or that an intrusion occurred.

Use **File → Open Folder** for this repository, then the Python tasks and debugger
in [the development guide](vscode-development.md). Do not manufacture a .NET
solution to silence the message. If C# tooling is distracting here, select C# Dev
Kit in Extensions and choose **Disable (Workspace)**, preserving it for real .NET
projects. Microsoft's [C# project guide](https://code.visualstudio.com/docs/csharp/project-management)
explains solutions, and its [extension-management guide](https://code.visualstudio.com/docs/configure/extensions/extension-marketplace#_disable-an-extension)
distinguishes workspace and global disabling.

## Useful roles in this project

| Installed tools | Recommended use |
| --- | --- |
| Python, Pylance, Python Debugger, Ruff | Primary implementation loop: navigate Python, breakpoints, focused script tests, enforced lint. Ruff 2026.88.0 is now registered; the existing CLI remains authoritative for checks. |
| GitLens; GitHub Pull Requests; GitHub Actions | Inspect history and diffs, review PRs, investigate CI failures. GitLens can manage [worktrees](https://help.gitkraken.com/gitlens/gl-worktrees/) for separate branches; use explicit writer ownership for parallel agents. |
| YAML, Markdownlint, Markdown preview, SVG preview, PlantUML | Configuration and architecture documentation. Keep private diagrams local; check renderer settings before using an external diagram server. |
| ESLint, JavaScript snippets, Edge DevTools | Useful as Cockpit grows; its first screen is plain JavaScript/CSS. Installed extensions alone do not establish a configured lint or browser-test gate. |
| Jupyter, Rainbow CSV | Optional exploration of synthetic benchmarks, query measurements and structured evidence. Keep outputs and private datasets outside ordinary repository indexing. No need for Spark or a new runtime for this screen. |
| Containers, Docker, Dev Containers | Later reproducibility and isolated integration checks. Installing these does not require moving the existing native Mac environment. |
| Codex, built-in Copilot Chat, Roo Code, opencode | Alternative client/reviewer roles. Verify each client's credentials, model, effective MCP tools and approval boundary before use. One client can implement while an independently scoped reviewer checks the diff. They do not automatically synchronize chats or register task workers. |
| Azure/Foundry, Azure MCP, Kubernetes | Use when a concrete hosting or model-evaluation requirement exists. No cloud provisioning or API migration is needed for the local Cockpit. |
| C# Dev Kit, C#, .NET runtime, NuGet | Relevant to actual .NET projects; optional in this Python workspace. |
| Java/Gradle/Maven; C/C++/CMake/Makefile tools | Use for their respective repositories and native parser source work. This repository does not require a Java or .NET solution just to run Python. |
| Remote SSH/repositories, WSL, Vim and other utilities | Preserve personal workflows; WSL targets Windows and is not needed for this native Mac loop. |

VS Code's bundled **Copilot Chat 0.70.0** is separate from user-installed extension
directories. The inventory also includes a legacy OpenAI Work-with-VS-Code bridge;
its presence is not evidence it is the active Codex integration. Check actual
activation and the chosen client before removing overlapping packages.

The local registry contains multiple versions of RemoteHub, Edge DevTools and
Remote SSH. This can occur during updates; it is not proof two versions execute
simultaneously. No directory cleanup was performed. A focused Python workspace
profile is a reasonable later preference, but global extension choices and
security/approval settings were preserved.
