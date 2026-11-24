# DeltaPatcher for AI assistants (MCP)

DeltaPatcher runs as an MCP server (the [Model Context Protocol](https://modelcontextprotocol.io)), so an
AI assistant such as Claude Code, Claude Desktop or VS Code can work with it for you: check and update
projects, publish and release them, look at settings and keys, read these guides, and run a self-test.

It is built into the command line, so nothing else needs installing:

```
deltapatcher mcp
```

## Connect an assistant

Give your assistant the command line's full path and the word `mcp`.

**Claude Code**, for every project on this computer:

```
claude mcp add --scope user deltapatcher -- "C:\Tools\deltapatcher-cli-windows-x64.exe" mcp
```

**Claude Desktop**: add it to `claude_desktop_config.json` (Settings > Developer > Edit Config), then restart
Claude Desktop:

```json
{
  "mcpServers": {
    "deltapatcher": { "command": "C:\\Tools\\deltapatcher-cli-windows-x64.exe", "args": ["mcp"] }
  }
}
```

**VS Code**: `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "deltapatcher": { "type": "stdio", "command": "C:\\Tools\\deltapatcher-cli-windows-x64.exe", "args": ["mcp"] }
  }
}
```

**A project of yours**, for everyone who works on it with Claude Code: `.mcp.json` at its root, with the
same `mcpServers` block as Claude Desktop. Ready-made files are in [examples](examples).

On Linux and macOS the command is the unpacked `deltapatcher-cli`. From a clone of this repository, use
`python mcp/server.py` as the command instead.

## What it can do

**Look, changing nothing**

- `list_projects`, `get_settings`: every project, and every setting with its file.
- `check`: what an update would change, for one project or all of them.
- `ssh_status`, `ssh_scan`, `ssh_test`, `ssh_pubkey`: the SSH key, a server's host keys, a test login, the
  line that goes on the server.
- `sign_status`, `sign_pubkey`: the signing key, and the public key players use.
- `guide`, `log`: these guides, and the last lines of DeltaPatcher's log.
- `version`, `check_for_new_version`.

**Change files or servers** (marked as such, so your assistant asks you first)

- `update`, `rollback`: bring a folder up to date (one project or all of them, several at once), or undo
  the last update.
- `release`, `deploy`: send a release to a server over SFTP, or to a folder. A dry run comes first.
- `project_remove`.

**Everything else**

- `publish`: turn a folder into a store, sent nowhere.
- `serve_start`, `serve_stop`: serve a store on this computer, to test updates.
- `set_settings`, `project_new`, `project_add`, `project_rename`, `ssh_trust`.
- `selftest`: the whole cycle (publish, serve, update, publish a change, check, update, roll back) in a
  throwaway folder that touches no project and no setting.

The guides are also there as resources, and three prompts start common jobs: `release_new_version`,
`keep_folder_updated` and `diagnose`.

## What it never does

- **Handle a passphrase or a password.** Signing a release, unlocking an SSH key and putting a key on a
  server need one, and a secret must never pass through an AI assistant. Do those in the DeltaPatcher app or
  on its command line; releases of a project that signs are made there too.
- **Replace DeltaPatcher itself.** `check_for_new_version` says when one is out; updating is yours.
- **Trust a server's host key it has not been told about.** `ssh_trust` keeps a key only when the server
  offers exactly the fingerprints given: use fingerprints read on the server itself.

It works with the same settings and projects as the app and the command line. To give it settings of its
own, set `DELTAPATCHER_CONFIG` to another file in the assistant's configuration.

## Using it in your own projects

Connected to an assistant in your game's or mod's repository, DeltaPatcher becomes part of the work there:

- "Release the `build` folder as version 1.4 to our server, but show me the dry run first."
- "Check every project and tell me which ones need an update."
- "Set up a project that keeps `D:\Games\MyMod` up to date from `https://mods.example.com/`."
- "That update failed: what does the log say?"

## Working on DeltaPatcher with it

`selftest` checks the whole cycle on your computer in a few seconds, which makes it a quick way to see that
a change to the engine still works end to end. The server itself is `deltapatcher/mcp_server.py`: Python's
standard library only, and it runs the engine with `--json` for every tool, exactly as the app does.
`tests/test_mcp.py` drives it over its real standard input and output.
