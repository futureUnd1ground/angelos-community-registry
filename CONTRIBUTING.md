# Publishing an AngelOS plugin

This guide explains how to create a plugin, test it locally, publish a release
archive, and submit it to the Community Registry.

## 1. Create the plugin

Keep the plugin in its own repository. A minimal plugin can look like this:

```text
my-widget/
├── manifest.json
├── Main.qml
├── Settings.qml
└── README.md
```

Install a development copy into the AngelOS user plugin directory:

```bash
mkdir -p ~/.config/angelos/plugins
cp -a my-widget ~/.config/angelos/plugins/my-widget
```

Use AngelOS's existing QML components and plugin conventions. Do not copy the
AngelOS repository or create a second plugin loader.

## 2. Write `manifest.json`

The manifest is JSON and must contain these fields:

```json
{
  "id": "my-widget",
  "name": "My Widget",
  "version": "1.0.0",
  "author": "Your name",
  "description": "A short description.",
  "icon": "sparkles",
  "enabledByDefault": true,
  "main": "Main.qml",
  "settings": "Settings.qml"
}
```

`id` must be unique, lowercase, and stable after publication. Use letters,
digits, `-`, or `_`; do not rename it between releases. `version` is the
version that the Store compares when offering updates. Use a normal
`MAJOR.MINOR.PATCH` version such as `1.0.0`.

Only declare entry-point fields that the plugin actually provides. Existing
AngelOS plugins use fields such as:

- `main`: the main QML entry point;
- `barWidget`: a panel widget QML file;
- `launcher`: a launcher integration QML file;
- `settings`: a settings page QML file;
- `menu`: declared desktop or application menu items.

Keep all referenced files inside the plugin directory. Do not use absolute
paths or download and execute code at runtime.

## 3. Test locally

Validate the JSON before testing the UI:

```bash
jq empty my-widget/manifest.json
jq -e '.id and .name and .version' my-widget/manifest.json
```

After copying the plugin, reload AngelOS and check **Settings -> Plugins**.
Exercise the plugin's main view, settings, enable/disable behavior, and error
paths. Test on a clean user configuration when possible. Record required
programs, network access, permissions, or login state in the registry entry.

## 4. Build the release ZIP

The archive must contain exactly one `manifest.json`, either at its root or one
directory below it. The manifest ID and version must match the registry entry.

For a root-level archive:

```bash
cd my-widget
zip -r ../my-widget-v1.0.0.zip \
  manifest.json Main.qml Settings.qml README.md
```

Include every QML, JavaScript, image, and helper file required at runtime.
Do not include `.git`, private credentials, build caches, or unrelated files.

Check the archive before uploading:

```bash
unzip -t ../my-widget-v1.0.0.zip
unzip -l ../my-widget-v1.0.0.zip
```

The Community Store rejects malformed JSON, path traversal, symlinks,
archives over its size limit, missing manifests, and ID/version mismatches.

## 5. Publish on GitHub

Create a public repository for the plugin and push the source. Then create a
GitHub Release whose asset is the ZIP:

```bash
git tag v1.0.0
git push origin v1.0.0
gh release create v1.0.0 ../my-widget-v1.0.0.zip \
  --title "My Widget 1.0.0" \
  --notes "Initial release"
```

The registry `source` must point to the release asset, for example:

```text
https://github.com/you/my-widget/releases/download/v1.0.0/my-widget-v1.0.0.zip
```

The source URL must use HTTPS and remain publicly downloadable. Do not require
the installer's GitHub login.

## 6. Submit to the registry

Fork this repository, edit `plugins.json`, and add an entry with
`"status": "pending"`:

```json
{
  "id": "my-widget",
  "name": "My Widget",
  "author": "Your name",
  "version": "1.0.0",
  "description": "What the plugin does.",
  "source": "https://github.com/you/my-widget/releases/download/v1.0.0/my-widget-v1.0.0.zip",
  "repository": "https://github.com/you/my-widget",
  "tags": ["widgets", "utility"],
  "category": "Utilities",
  "license": "MIT",
  "dependencies": [],
  "permissions": [],
  "status": "pending"
}
```

Open a pull request and include:

- what the plugin does and which AngelOS versions were tested;
- the exact release asset URL and repository URL;
- license and attribution for copied code or assets;
- required packages, external commands, network access, and permissions;
- screenshots or a short usage example when the UI is not self-explanatory.

Pending entries are invisible in the Store. The maintainer changes the status
to `approved` only after reviewing the source, manifest, archive, provenance,
license, dependencies, and compatibility.

## 7. Publish updates

For an update, change the plugin version, test it, create a new release asset,
and submit a registry change with the same stable `id` and the new `version`
and `source`. Never reuse an old version number for different contents.

Users can press `r` in the Store TUI to refresh the registry and `Enter` on a
plugin with an available update. The installer validates the downloaded
manifest before replacing the installed copy.

## 8. Removal and moderation

To hide a listing, change its registry status from `approved` to `pending` or
remove the entry. Do not approve code that has not been reviewed. Installed
QML and helper scripts run with the user's account permissions; the registry is
not a security sandbox.

## AngelOS categories

Market 0.8.0 groups filters into Story & games, Appearance, and Plugins & tools. The taxonomy follows AngelOS's existing realms and circles, visual novel, characters, scenes and voices, alongside palettes, skins, wallpapers, cursors, fonts, icons and effects.

| `category` | Store label |
| --- | --- |
| `Story` | Story |
| `Novels` | Visual novels |
| `Quests` | Quests |
| `Characters` | Characters |
| `Realms` | Worlds & realms |
| `Dialogue` | Dialogue & scenes |
| `Minigames` | Minigames |
| `Voices` | Voices |
| `Pets` | Pets |
| `Widgets` | Widgets |
| `Desktop` | Desktop |
| `Bar` | Bar |
| `Themes` | Themes |
| `Skins` | Interface skins |
| `Wallpapers` | Wallpapers |
| `Cursors` | Cursors |
| `Fonts` | Fonts |
| `Icons` | Icons |
| `Effects` | Effects & animations |
| `AI` | AI assistants |
| `DeveloperTools` | Developer tools |
| `Launcher` | Launcher & search |
| `Network` | Network |
| `Audio` | Music & audio |
| `Productivity` | Productivity |
| `Integrations` | Integrations |
| `Accessibility` | Accessibility |
| `System` | System |
| `Utilities` | Utilities |

Use one primary category ID and tags for other applicable filters. The registry uses stable English IDs; Store translates their labels. Legacy categories and tags remain supported, including Worlds, Narrative, Lore and story-packs. Unknown categories are listed automatically under Plugins & tools.

Story collects stories, novels, quests, characters, realms, dialogue and voices. Ordinary pets and minigames require an explicit story tag to enter that filter. Themes collects appearance subcategories. Search supports both Russian and English category labels.

Story example: `"category": "Story", "tags": ["story", "novel", "dialogue", "characters", "heaven"]`. Appearance example: `"category": "Skins", "tags": ["skins", "themes", "fonts", "cursor"]`. Category metadata does not load story content into the engine; plugin authors must implement integration through AngelOS's supported APIs and document compatibility and dependencies.
