#!/usr/bin/env python3
"""Small TUI for reviewing and moderating AngelOS registry entries."""
import base64
import curses
import json
import os
import shutil
import subprocess
import sys
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


def gh_json(args):
    if not GH:
        raise RuntimeError("GitHub CLI is required; install gh and run gh auth login")
    result = subprocess.run([GH, "api"] + args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub API request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub API returned invalid JSON") from exc


def fetch_open_prs(base_payload):
    """Return PR inbox rows, including entries that are not merged yet."""
    prs = gh_json(["repos/{}/pulls?state=open&base=main&per_page=100".format(REPO)])
    if not isinstance(prs, list):
        raise RuntimeError("GitHub returned an invalid pull request list")
    base_by_id = {str(item.get("id")): item for item in base_payload.get("plugins", [])}
    rows = []
    for pr in prs:
        number = pr.get("number")
        try:
            branch_payload = gh_json(["repos/{}/contents/plugins.json?ref={}".format(
                pr["head"]["repo"]["full_name"], pr["head"]["sha"]
            )])
            decoded = base64.b64decode(branch_payload["content"]).decode("utf-8")
            candidate_payload = json.loads(decoded)
        except Exception as exc:
            rows.append({"kind": "pull_request", "pr": pr, "entry": {
                "id": "pr-{}".format(number), "name": pr.get("title", "Invalid PR"),
                "description": "Cannot read plugins.json: {}".format(exc),
                "status": "pending", "author": pr.get("user", {}).get("login", "?")
            }})
            continue
        candidates = candidate_payload.get("plugins", []) if isinstance(candidate_payload, dict) else []
        for candidate in candidates:
            plugin_id = str(candidate.get("id", ""))
            # A PR is relevant if it adds a listing or changes an existing one.
            if plugin_id and candidate != base_by_id.get(plugin_id):
                entry = dict(candidate)
                entry["status"] = "pending"
                entry.setdefault("author", pr.get("user", {}).get("login", "?"))
                rows.append({"kind": "pull_request", "pr": pr, "entry": entry})
        if not any(row["pr"].get("number") == number for row in rows):
            rows.append({"kind": "pull_request", "pr": pr, "entry": {
                "id": "pr-{}".format(number), "name": pr.get("title", "PR without registry changes"),
                "description": "No changed plugin entry detected. Review the complete PR before acting.", "status": "pending",
                "author": pr.get("user", {}).get("login", "?")
            }})
    return rows


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


def pr_action(pr, action, reason=""):
    number = str(pr.get("number"))
    if action == "merge":
        args = ["pr", "merge", number, "--repo", REPO, "--squash", "--delete-branch"]
        result = subprocess.run([GH] + args, capture_output=True, text=True)
    elif action == "close":
        result = subprocess.run([GH, "pr", "close", number, "--repo", REPO, "--comment", reason or "Rejected by registry moderator"], capture_output=True, text=True)
    elif action == "approve":
        result = subprocess.run([GH, "pr", "review", number, "--repo", REPO, "--approve", "--body", reason or "Registry review passed"], capture_output=True, text=True)
    else:
        raise RuntimeError("Unknown PR action")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "GitHub PR action failed")
    return "PR #{} {}d".format(number, action)


class ModeratorUI:
    def __init__(self, screen):
        self.screen = screen
        self.entries = []
        self.selected = 0
        self.filter = "pending"
        self.query = ""
        self.message = "Loading registry..."
        self.error = False
        self.running = True
        self.refresh()

    def refresh(self):
        try:
            payload = fetch_registry()
            self.entries = [{"kind": "registry", "entry": item, "pr": None} for item in payload["plugins"]]
            self.entries.extend(fetch_open_prs(payload))
            self.message = "Inbox refreshed: {} items.".format(len(self.entries))
            self.error = False
        except Exception as exc:
            self.message, self.error = str(exc), True
        self.selected = min(self.selected, max(0, len(self.visible()) - 1))

    def visible(self):
        result = []
        for row in self.entries:
            entry = row["entry"]
            if self.filter == "prs" and row["kind"] != "pull_request":
                continue
            if self.filter in ("pending", "approved", "rejected") and entry.get("status") != self.filter:
                continue
            haystack = " ".join([
                str(entry.get(key, "")) for key in ("id", "name", "author", "description", "category")
            ] + [" ".join(map(str, entry.get("tags", []))), str(row.get("pr", {}).get("title", ""))])
            if self.query and self.query.casefold() not in haystack.casefold():
                continue
            result.append(row)
        return result

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
        self.screen.addnstr(1, 0, " [P]ending [A]pproved [R]ejected [L]ist [G]itHub PRs  / search  ".ljust(width - 1), width - 1, curses.A_BOLD)
        rows = self.visible()
        bottom = max(5, height - 10)
        count = bottom - 3
        start = max(0, min(self.selected - count + 1, len(rows) - count))
        for index, row in enumerate(rows[start:start + count], start):
            entry = row["entry"]
            marker = "PR#{:<5}".format(row["pr"].get("number")) if row["kind"] == "pull_request" else "REG   "
            label = "{} {:<9} {:<24.24} v{} by {}".format(marker, entry.get("status", "?"), entry.get("name", entry.get("id", "")), entry.get("version", "?"), entry.get("author", "?"))
            self.screen.addnstr(3 + index - start, 0, label.ljust(width - 1), width - 1, curses.A_REVERSE if index == self.selected else curses.A_NORMAL)
        row = self.selected_entry()
        entry = row["entry"] if row else None
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
            if row["kind"] == "pull_request":
                pr = row["pr"]
                lines += ["PR #{} by {}: {}".format(pr.get("number"), pr.get("user", {}).get("login", "?"), pr.get("html_url", "-")),
                          "PR title: {}".format(pr.get("title", "-"))]
            for offset, line in enumerate(lines):
                if detail + offset < height - 2:
                    self.screen.addnstr(detail + offset, 0, line.ljust(width - 1), width - 1, curses.A_BOLD if offset == 0 else curses.A_NORMAL)
        self.screen.addnstr(height - 2, 0, self.message.ljust(width - 1), width - 1, curses.color_pair(3) if self.error else curses.color_pair(2))
        self.screen.addnstr(height - 1, 0, "j/k move t test v review y approve n reject m merge c close o open / search r refresh q quit".ljust(width - 1), width - 1, curses.A_DIM)
        self.screen.refresh()

    def confirm(self, prompt):
        height, width = self.screen.getmaxyx()
        curses.echo(); curses.curs_set(1)
        self.screen.move(height - 2, 0); self.screen.clrtoeol(); self.screen.addnstr(height - 2, 0, prompt, width - 1)
        value = self.screen.getstr(height - 2, min(len(prompt), width - 2), max(1, width - len(prompt) - 2)).decode(errors="replace")
        curses.noecho(); curses.curs_set(0)
        return value.casefold() == "yes"

    def action_test(self):
        row = self.selected_entry()
        if not row: return
        entry = row["entry"]
        self.message, self.error = "Testing {}...".format(entry.get("id")), False; self.draw()
        try:
            self.message = validate_archive(entry)
        except Exception as exc:
            self.message, self.error = str(exc), True

    def action_status(self, status):
        row = self.selected_entry()
        if not row: return
        if row["kind"] == "pull_request":
            if status == "rejected":
                self.action_pr("close")
            else:
                self.action_review()
            return
        entry = row["entry"]
        if not self.confirm("Type YES to {} {}: ".format(status, entry.get("id"))):
            self.message, self.error = "Cancelled.", False; return
        try:
            self.message = update_status(entry["id"], status)
            self.refresh()
        except Exception as exc:
            self.message, self.error = str(exc), True

    def action_pr(self, action):
        row = self.selected_entry()
        if not row or row["kind"] != "pull_request":
            self.message, self.error = "Select an open PR first.", True
            return
        pr = row["pr"]
        if action == "merge" and str(row["entry"].get("id", "")).startswith("pr-"):
            self.message, self.error = "Merge blocked: PR has no valid plugin entry.", True
            return
        if action == "merge" and not str(row["entry"].get("id", "")).startswith("pr-"):
            self.message, self.error = "Validating plugin before merge...", False
            self.draw()
            try:
                pr_rows = [item for item in self.entries
                           if item["kind"] == "pull_request"
                           and item["pr"].get("number") == pr.get("number")
                           and not str(item["entry"].get("id", "")).startswith("pr-")]
                for item in pr_rows:
                    validate_archive(item["entry"])
                self.message = "Validated {} plugin entr{}.".format(len(pr_rows), "y" if len(pr_rows) == 1 else "ies")
            except Exception as exc:
                self.message, self.error = "Merge blocked: {}".format(exc), True
                return
        if not self.confirm("Type YES to {} PR #{}: ".format(action, pr.get("number"))):
            self.message, self.error = "Cancelled.", False
            return
        try:
            self.message = pr_action(pr, action, "Reviewed by registry moderator")
            self.refresh()
        except Exception as exc:
            self.message, self.error = str(exc), True

    def action_review(self):
        row = self.selected_entry()
        if not row or row["kind"] != "pull_request":
            self.message, self.error = "Select an open PR first.", True
            return
        pr = row["pr"]
        if not self.confirm("Type YES to approve review for PR #{}: ".format(pr.get("number"))):
            self.message, self.error = "Cancelled.", False
            return
        try:
            self.message = pr_action(pr, "approve", "Registry metadata and archive reviewed")
            self.refresh()
        except Exception as exc:
            self.message, self.error = str(exc), True

    def search(self):
        height, width = self.screen.getmaxyx()
        curses.echo(); curses.curs_set(1)
        prompt = "Search (empty clears): "
        self.screen.move(height - 2, 0); self.screen.clrtoeol(); self.screen.addnstr(height - 2, 0, prompt, width - 1)
        value = self.screen.getstr(height - 2, len(prompt), max(1, width - len(prompt) - 2)).decode(errors="replace")
        curses.noecho(); curses.curs_set(0)
        self.query = value.strip(); self.selected = 0

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
            elif key == ord("m"): self.action_pr("merge")
            elif key == ord("c"): self.action_pr("close")
            elif key == ord("v"): self.action_review()
            elif key == ord("o"):
                row = self.selected_entry()
                url = row["pr"].get("html_url") if row and row["kind"] == "pull_request" else (row["entry"].get("repository") or row["entry"].get("source")) if row else None
                if url:
                    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif key == ord("/"): self.search()
            elif key == ord("r"): self.refresh()
            elif key == ord("p"): self.filter = "pending"; self.selected = 0
            elif key == ord("a"): self.filter = "approved"; self.selected = 0
            elif key == ord("R"): self.filter = "rejected"; self.selected = 0
            elif key == ord("l"): self.filter = "all"; self.selected = 0
            elif key == ord("g"): self.filter = "prs"; self.selected = 0


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
