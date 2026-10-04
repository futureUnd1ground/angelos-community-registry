# AngelOS Community Registry

The Community Store reads `plugins.json` from this repository. Only entries
with `status: "approved"` are shown to users. New submissions must start as
`pending` and are reviewed by the repository maintainer before approval.

## Submit a plugin

The full developer workflow is documented in
[`CONTRIBUTING.md`](CONTRIBUTING.md). It covers local development, manifest
fields, ZIP validation, GitHub Releases, registry submissions, and moderation.

1. Create an AngelOS plugin folder with `manifest.json` and its QML/assets.
   The manifest must contain a simple unique `id`, `name`, and `version`.
2. Put the plugin folder in its own GitHub repository and publish a ZIP file
   as a GitHub Release asset. The ZIP must contain exactly one
   `manifest.json`, at the root or one directory below it. Keep the manifest
   ID and version aligned with the registry entry.
3. Fork this repository and add a registry entry to `plugins.json` with
   `status: "pending"`.
4. Open a pull request. Include the release URL, repository URL, license, a
   short description, relevant tags, and a note that the plugin was tested.
   List requested permissions and dependencies when applicable.

Example entry:

```json
{
  "id": "my-widget",
  "name": "My Widget",
  "author": "Plugin author",
  "version": "1.0.0",
  "description": "What the plugin does.",
  "source": "https://github.com/author/my-widget/releases/download/v1.0.0/my-widget.zip",
  "repository": "https://github.com/author/my-widget",
  "tags": ["widgets", "utility"],
  "category": "Utilities",
  "license": "MIT",
  "dependencies": [],
  "permissions": [],
  "status": "pending"
}
```

## Moderation

Repository maintainers can use the simple moderator TUI after authenticating
with GitHub CLI:

```bash
gh auth login
python3 scripts/community-registry-moderator.py
```

The TUI checks the authenticated GitHub username against
[`moderators.json`](moderators.json). The default list contains only
`futureUnd1ground`; add another username through a reviewed Pull Request to
grant that person moderator access. GitHub repository write permissions remain
the final authorization boundary for changing the registry.

Use `p/a/R/l` to switch pending, approved, rejected, or all entries; `t` to
download and validate a release in an isolated temporary directory; `y` to
approve; `n` to reject; `r` to refresh; and `q` to quit. Approval and rejection
require typing `YES` and commit the status change to `main` through the GitHub
API. The test action validates the archive and performs an isolated install;
it does not execute unreviewed QML inside the running AngelOS shell.

Review the plugin source, manifest, archive contents, release provenance,
license, dependencies, requested permissions, and AngelOS compatibility in
the pull request. Test the ZIP with Community Store when possible. Merge only
after the review is complete, changing `status` from `pending` to `approved`.
The Store ignores pending entries. To remove or suspend a listing, remove it
or change its status back to `pending`, then commit the registry update.

## Bundled AngelOS plugins

This registry also publishes ZIP snapshots of the plugins bundled in the
AngelOS-Dotfiles checkout. They are listed with their upstream repository and
source path for attribution. These packages are mirrors, not independent
rewrites; review the upstream source and its current license terms before
redistributing them.

The mirrored set currently includes `cat`, `claude-companion`,
`codex-companion`, `nightlight`, `osu-mini`, `quick-actions`, `speedtest`,
`stream-stats`, and `web-search`. `claude-companion` and `codex-companion`
need their respective CLI/authentication to provide their full features;
`speedtest` needs `speedtest-cli` for measurements.

Registry entries are not a security sandbox: installed QML and scripts run
with the user's account permissions. Do not approve code you have not
reviewed.
