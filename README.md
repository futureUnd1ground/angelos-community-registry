# AngelOS Community Registry

[English](README.md) | [Русская версия](README.ru.md)

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

To install a direct console command in Fish, run the installer from a local
clone, or download the installer and run it from any directory:

```fish
curl -fsSL -o /tmp/install-moderator.fish https://raw.githubusercontent.com/futureUnd1ground/angelos-community-registry/main/install-moderator.fish
and fish /tmp/install-moderator.fish
fish_add_path ~/.local/bin
```

After installation, run `community-registry-moderator` from any directory.
The installer places the script in `~/.local/share`, adds the command to
`~/.local/bin`, and configures Fish's PATH. On the maintainer's CachyOS setup,
it also copies the previously authenticated temporary `gh` binary into
`~/.local/bin` when no GitHub CLI is already available. If GitHub CLI is not
authenticated in that Fish session, run `gh auth login` once.

The TUI checks the authenticated GitHub username against
[`moderators.json`](moderators.json). The allowlist contains
`futureUnd1ground` and `MixaDoDs`, the owner of the community-store fork. Add
another username through a reviewed Pull Request to grant moderator access.
GitHub repository write permissions remain
the final authorization boundary for changing the registry.

Run `community-registry-moderator --check` to verify GitHub authentication and
allowlist access without starting the TUI. Only an allowlisted account can
perform moderation actions.

The TUI has one inbox containing registry records and every open pull request,
including PRs with an invalid or missing `plugins.json`. Use `p/a/R/l` for
pending, approved, rejected, or all registry records; `g` for open PRs; `/` to
search ID, name, author, description, tags, or PR title; `t` to download and
validate the ZIP and manifest; `y`/`n` to change an existing registry record;
`v` to submit a GitHub review; `m` to validate and merge a PR; `c` to close it; `o` to open its URL; `r` to
refresh; and `j`/`k` or arrow keys to move. Every mutating action requires
typing `YES`; a plugin PR cannot be merged when archive validation fails.
Package validation does not execute QML. Test plugin behavior in a separate
AngelOS test session before approval; running unreviewed plugin code inside the
moderator could execute with the moderator's account permissions.

## Telegram moderation bot

`scripts/community-registry-telegram-bot.py` sends moderators every new or
updated open PR with its title, description, version, ID, contributor, tags,
source, repository, and license. Telegram usernames are stored in
[`telegram-moderators.json`](telegram-moderators.json), currently
`@futureoffc` and `@psxgld`.

1. Create a bot with `@BotFather` and copy its token.
2. Copy `telegram-bot.env.example` to a protected file and set
   `TELEGRAM_BOT_TOKEN` and `GITHUB_TOKEN`.
3. Start from the registry root:

```bash
set -a; source ./telegram-bot.env; set +a
python3 scripts/community-registry-telegram-bot.py
```

A moderator must open the bot and send `/start` once to subscribe the chat.
Every PR message includes buttons for `Open PR`, `Approve review`, `Reject`,
`Merge`, and `Refresh`. Text commands remain available: `/inbox`, `/status`,
`/approve N`, `/reject N`, and `/merge N`. The bot checks the allowlist for both
buttons and text commands; tokens are never stored in Git.

## Desktop GUI

Install the local moderation application from a registry checkout:

```fish
fish install-registry-gui.fish
```

It appears in the application menu as `AngelOS Community Registry` and can also
be launched with `community-registry-gui`. The app uses the current `gh` login,
shows open PRs with search and plugin metadata, and provides confirmed
`Approve review`, `Reject`, `Merge`, `Open PR`, and `Refresh` actions. All
controls use one dark palette so native light ttk buttons do not clash with the
dark window background.

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

### Bot access and validation diagnostics

Use `/github` to check the signed-in GitHub account and repository write access. Invalid registry JSON is reported with repository, revision, line and column; logging in again cannot repair a broken `plugins.json`. The bot does not merge invalid plugin metadata. Code/docs-only maintenance PRs can be merged without a plugin package. Safe reads retry transient network errors; mutations are never replayed automatically. The bot waits for network recovery at startup. Registry and bot tests run in GitHub Actions on PRs and main.
