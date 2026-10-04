#!/usr/bin/env python3
"""Small TUI for reviewing and moderating AngelOS registry entries."""
import base64
import curses
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "futureUnd1ground/angelos-community-registry"
REGISTRY_URL = "https://raw.githubusercontent.com/{}/main/plugins.json".format(REPO)
MODERATORS_URL = "https://raw.githubusercontent.com/{}/main/moderators.json".format(REPO)
GH = shutil.which("gh")
MAX_ARCHIVE = 64 * 1024 * 1024
ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_-")


def fetch_registry():
    request = urllib.request.Request(REGISTRY_URL, headers={"User-Agent": "angelos-community-registry-moderator"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("plugins"), list):
        raise RuntimeError("Unsupported registry format")
    return payload


def current_github_user():
    if not GH:
        raise RuntimeError("GitHub CLI is required; install gh and run gh auth login")
    result = subprocess.run([GH, "api", "user", "--jq", ".login"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("GitHub login required: run gh auth login")
    return result.stdout.strip()


def require_moderator():
    user = current_github_user()
    request = urllib.request.Request(MODERATORS_URL, headers={"User-Agent": "angelos-registry-moderator"})
    with urllib.request.urlopen(request, timeout=20) as response:
        allowed = json.loads(response.read(64 * 1024).decode("utf-8"))
    if not isinstance(allowed, list) or user not in allowed:
        raise RuntimeError("GitHub user @{} is not an approved registry moderator".format(user))
    return user


def validate_archive(entry):
    source = str(entry.get("source", ""))
    if not source.startswith("https://"):
        raise RuntimeError("Source must use HTTPS")
    request = urllib.request.Request(source, headers={"User-Agent": "angelos-registry-moderator"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise RuntimeError("Archive exceeds 64 MiB")
    with tempfile.TemporaryDirectory(prefix="angelos-review-") as temp:
        archive = Path(temp) / "plugin.zip"
        root = Path(temp) / "unpacked"
        archive.write_bytes(data)
        root.mkdir()
        with zipfile.ZipFile(archive) as zf:
            if len(zf.infolist()) > 2000:
                raise RuntimeError("Archive has too many files")
            if sum(item.file_size for item in zf.infolist()) > MAX_ARCHIVE:
                raise RuntimeError("Expanded archive exceeds 64 MiB")
            for item in zf.infolist():
                if item.is_dir():
                    continue
                target = (root / item.filename).resolve()
                if not str(target).startswith(str(root.resolve()) + os.sep):
                    raise RuntimeError("Archive contains an unsafe path")
            zf.extractall(root)
        manifests = list(root.glob("manifest.json")) + list(root.glob("*/manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError("Archive must contain exactly one manifest.json")
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        expected_id = str(entry.get("id", ""))
        if not expected_id or any(char not in ID_CHARS for char in expected_id):
            raise RuntimeError("Invalid registry id")
        if manifest.get("id") != expected_id:
            raise RuntimeError("Manifest ID does not match registry")
        if str(manifest.get("version", "")) != str(entry.get("version", "")):
            raise RuntimeError("Manifest version does not match registry")
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            raise RuntimeError("Manifest name is missing")
        return "archive OK: {} files, manifest {} {}".format(len(list(root.rglob("*"))), manifest["id"], manifest["version"])


def update_status(plugin_id, status):
    result = subprocess.run([GH, "api", "repos/{}/contents/plugins.json?ref=main".format(REPO)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub API read failed")
    remote = json.loads(result.stdout)
    payload = json.loads(base64.b64decode(remote["content"]).decode("utf-8"))
    entry = next((item for item in payload["plugins"] if item.get("id") == plugin_id), None)
    if not entry:
        raise RuntimeError("Plugin is no longer in registry")
    entry["status"] = status
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    encoded = base64.b64encode(content.encode()).decode()
    message = "{} {} in registry".format("Approve" if status == "approved" else "Reject", plugin_id)
    put = subprocess.run([
        GH, "api", "repos/{}/contents/plugins.json".format(REPO), "-X", "PUT",
        "-f", "message=" + message, "-f", "content=" + encoded,
        "-f", "sha=" + remote["sha"], "-f", "branch=main",
    ], capture_output=True, text=True)
    if put.returncode:
        raise RuntimeError(put.stderr.strip() or "GitHub API write failed")
    return "{}: {} committed to main".format(plugin_id, status)


class ModeratorUI:
    def __init__(self, screen):
        self.screen = screen
        self.entries = []
        self.selected = 0
        self.filter = "pending"
        self.message = "Loading registry..."
        self.error = False
        self.running = True
        self.refresh()

    def refresh(self):
        try:
            payload = fetch_registry()
            self.entries = payload["plugins"]
            self.message = "Registry refreshed."
            self.error = False
        except Exception as exc:
            self.message, self.error = str(exc), True
        self.selected = min(self.selected, max(0, len(self.visible()) - 1))

    def visible(self):
        return [entry for entry in self.entries if self.filter == "all" or entry.get("status") == self.filter]

    def selected_entry(self):
        rows = self.visible()
        return rows[self.selected] if rows else None

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 12 or width < 60:
            self.screen.addnstr(0, 0, "Resize terminal to at least 60x12", max(1, width - 1))
            self.screen.refresh()
            return
        self.screen.addnstr(0, 0, " ANGEL OS REGISTRY MODERATOR ".ljust(width - 1), width - 1, curses.color_pair(1) | curses.A_BOLD)
        self.screen.addnstr(1, 0, " [P]ending  [A]pproved  [R]ejected  [L]ist all ".ljust(width - 1), width - 1, curses.A_BOLD)
        rows = self.visible()
        bottom = max(5, height - 10)
        count = bottom - 3
        start = max(0, min(self.selected - count + 1, len(rows) - count))
        for index, entry in enumerate(rows[start:start + count], start):
            label = "{:<9} {:<24.24} v{} by {}".format(entry.get("status", "?"), entry.get("name", entry.get("id", "")), entry.get("version", "?"), entry.get("author", "?"))
            self.screen.addnstr(3 + index - start, 0, label.ljust(width - 1), width - 1, curses.A_REVERSE if index == self.selected else curses.A_NORMAL)
        entry = self.selected_entry()
        detail = bottom + 1
        if entry:
            lines = [
                "{} [{}]".format(entry.get("name", entry.get("id")), entry.get("status", "?")),
                entry.get("description", "").replace("\n", " "),
                "ID: {}  Version: {}  Category: {}".format(entry.get("id"), entry.get("version"), entry.get("category", "-")),
                "Tags: {}".format(", ".join(entry.get("tags", [])) or "-"),
                "Dependencies: {}".format(", ".join(map(str, entry.get("dependencies", []))) or "none"),
                "Permissions: {}".format(", ".join(map(str, entry.get("permissions", []))) or "none"),
                "Source: {}".format(entry.get("source", "-")),
            ]
            for offset, line in enumerate(lines):
                if detail + offset < height - 2:
                    self.screen.addnstr(detail + offset, 0, line.ljust(width - 1), width - 1, curses.A_BOLD if offset == 0 else curses.A_NORMAL)
        self.screen.addnstr(height - 2, 0, self.message.ljust(width - 1), width - 1, curses.color_pair(3) if self.error else curses.color_pair(2))
        self.screen.addnstr(height - 1, 0, "j/k move  t validate package  y approve  n reject  r refresh  q quit".ljust(width - 1), width - 1, curses.A_DIM)
        self.screen.refresh()

    def confirm(self, prompt):
        height, width = self.screen.getmaxyx()
        curses.echo(); curses.curs_set(1)
        self.screen.move(height - 2, 0); self.screen.clrtoeol(); self.screen.addnstr(height - 2, 0, prompt, width - 1)
        value = self.screen.getstr(height - 2, min(len(prompt), width - 2), max(1, width - len(prompt) - 2)).decode(errors="replace")
        curses.noecho(); curses.curs_set(0)
        return value.casefold() == "yes"

    def action_test(self):
        entry = self.selected_entry()
        if not entry: return
        self.message, self.error = "Testing {}...".format(entry.get("id")), False; self.draw()
        try:
            self.message = validate_archive(entry)
        except Exception as exc:
            self.message, self.error = str(exc), True

    def action_status(self, status):
        entry = self.selected_entry()
        if not entry: return
        if not self.confirm("Type YES to {} {}: ".format(status, entry.get("id"))):
            self.message, self.error = "Cancelled.", False; return
        try:
            self.message = update_status(entry["id"], status)
            self.refresh()
        except Exception as exc:
            self.message, self.error = str(exc), True

    def run(self):
        curses.curs_set(0); curses.start_color(); curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN); curses.init_pair(2, curses.COLOR_CYAN, -1); curses.init_pair(3, curses.COLOR_RED, -1)
        self.screen.keypad(True)
        while self.running:
            self.draw(); key = self.screen.getch()
            if key in (ord("q"), 27): self.running = False
            elif key in (curses.KEY_UP, ord("k")): self.selected = max(0, self.selected - 1)
            elif key in (curses.KEY_DOWN, ord("j")): self.selected = min(max(0, len(self.visible()) - 1), self.selected + 1)
            elif key == ord("t"): self.action_test()
            elif key == ord("y"): self.action_status("approved")
            elif key == ord("n"): self.action_status("rejected")
            elif key == ord("r"): self.refresh()
            elif key == ord("p"): self.filter = "pending"; self.selected = 0
            elif key == ord("a"): self.filter = "approved"; self.selected = 0
            elif key == ord("R"): self.filter = "rejected"; self.selected = 0
            elif key == ord("l"): self.filter = "all"; self.selected = 0


def main():
    try:
        require_moderator()
        curses.wrapper(lambda screen: ModeratorUI(screen).run())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("Moderator access denied: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
