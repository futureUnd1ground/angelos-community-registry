import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/community-registry-telegram-bot.py"
SPEC = importlib.util.spec_from_file_location("community_registry_telegram_bot", SCRIPT)
BOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOT)


class ModeratorFileTests(unittest.TestCase):
    def test_uses_configured_path_when_set(self):
        with patch.dict("os.environ", {"ANGELOS_TELEGRAM_MODERATORS": "~/moderators.json"}):
            self.assertEqual(BOT.moderators_file(), Path("~/moderators.json").expanduser())

    def test_prefers_checkout_file_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moderator_file = root / "telegram-moderators.json"
            moderator_file.touch()
            with patch.object(BOT, "ROOT", root), patch.dict("os.environ", {}, clear=True):
                self.assertEqual(BOT.moderators_file(), moderator_file)

    def test_falls_back_to_installed_shared_file(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            expected = home / ".local/share/telegram-moderators.json"
            with patch.object(BOT, "ROOT", home / "installed-bot"), \
                    patch.object(BOT.Path, "home", return_value=home), \
                    patch.dict("os.environ", {}, clear=True):
                self.assertEqual(BOT.moderators_file(), expected)


class ChangedPluginsTests(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "base": {"repo": {"full_name": "owner/registry"}, "sha": "base-sha"},
            "head": {"repo": {"full_name": "fork/registry"}, "sha": "head-sha"},
        }

    def test_compares_pr_base_to_head(self):
        snapshots = {
            ("owner/registry", "base-sha"): {"widget": {"id": "widget", "version": "1"}},
            ("fork/registry", "head-sha"): {"widget": {"id": "widget", "version": "2"}},
        }
        with patch.object(BOT, "registry_plugins", side_effect=lambda repo, ref: snapshots[(repo, ref)]):
            changes, error = BOT.changed_plugins(self.pr)
        self.assertIsNone(error)
        self.assertEqual([("widget", "updated")], [(item["id"], item["_change"]) for item in changes])

    def test_reports_added_and_removed_entries(self):
        snapshots = {
            ("owner/registry", "base-sha"): {"old": {"id": "old"}},
            ("fork/registry", "head-sha"): {"new": {"id": "new"}},
        }
        with patch.object(BOT, "registry_plugins", side_effect=lambda repo, ref: snapshots[(repo, ref)]):
            changes, error = BOT.changed_plugins(self.pr)
        self.assertIsNone(error)
        self.assertEqual({("new", "added", False), ("old", "removed", True)},
                         {(item["id"], item["_change"], item["_removed"]) for item in changes})

    def test_reports_missing_head_repository(self):
        pr = {"base": {"sha": "base-sha"}, "head": {"sha": "head-sha"}}
        changes, error = BOT.changed_plugins(pr)
        self.assertEqual([], changes)
        self.assertIn("source repository", error)


class NotificationTests(unittest.TestCase):
    def test_notifies_once_for_each_changed_pr(self):
        prs = [
            {"number": 11, "updated_at": "2026-10-05T10:00:00Z", "head": {"sha": "a"}, "_base_sha": "base"},
            {"number": 12, "updated_at": "2026-10-05T10:00:00Z", "head": {"sha": "b"}, "_base_sha": "base"},
        ]
        state = {"metadata_cache": {}, "chats": {"123": "moderator"}, "seen": {}}
        sent = []
        with patch.object(BOT, "fetch_prs", return_value=prs), \
                patch.object(BOT, "moderators", return_value={"moderator"}), \
                patch.object(BOT, "send_pr", side_effect=lambda chat, pr, page: sent.append((chat, pr, page))):
            BOT.notify_new_prs(state)
        self.assertEqual(2, len(sent))
        self.assertEqual([11, 12], [item[1]["number"] for item in sent])
        self.assertEqual({"11", "12"}, set(state["seen"]))


class ModerationGuardTests(unittest.TestCase):
    def test_self_approval_is_stopped_before_github_review_request(self):
        message = {"chat": {"id": 5}, "from": {"username": "moderator"}}
        pr = {"state": "open", "base": {"ref": "main"}, "user": {"login": "Owner"}}
        with patch.object(BOT, "is_moderator", return_value=True), \
                patch.object(BOT, "github", return_value=pr) as github, \
                patch.object(BOT, "github_login", return_value="owner"), \
                patch.object(BOT, "send") as send:
            BOT.moderate(message, "approve", "42")
        self.assertEqual(1, github.call_count)
        self.assertIn("нельзя одобрить", send.call_args.args[1])

    def test_condition_posts_comment_to_pr(self):
        message = {"chat": {"id": 5}, "from": {"username": "moderator"},
                   "_condition": "Добавить скриншот"}
        pr = {"state": "open", "base": {"ref": "main"}}
        with patch.object(BOT, "is_moderator", return_value=True), \
                patch.object(BOT, "github", side_effect=[pr, {"id": 9}]) as github, \
                patch.object(BOT, "send") as send:
            BOT.moderate(message, "conditions", "42")
        self.assertEqual("POST", github.call_args.args[1])
        self.assertEqual("Добавить скриншот", github.call_args.args[2]["body"].split("\n\n", 1)[1])
        self.assertIn("опубликовано на GitHub", send.call_args.args[1])

    def test_common_github_api_errors_are_user_friendly(self):
        cases = {401: "токен", 403: "запретил", 404: "не найден", 409: "конфликт", 422: "отклонил"}
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertIn(expected, BOT.github_error_message(BOT.ApiError(code, "raw API detail")))

    def test_condition_button_opens_inline_menu(self):
        state = {"pending_conditions": {}}
        update = {"callback_query": {"id": "callback", "data": "conditions:42",
                                      "from": {"username": "moderator"},
                                      "message": {"chat": {"id": 5, "type": "private"}}}}
        queued = []
        with patch.object(BOT, "telegram"), patch.object(BOT, "is_moderator", return_value=True), \
                patch.object(BOT, "queue_message", side_effect=lambda *args, **kwargs: queued.append((args, kwargs))):
            BOT.handle_callback(update, state)
        markup = queued[0][1]["reply_markup"]["inline_keyboard"]
        callbacks = [button["callback_data"] for row in markup for button in row]
        self.assertIn("condition:screenshots:42", callbacks)
        self.assertIn("condition:custom:42", callbacks)


if __name__ == "__main__":
    unittest.main()
