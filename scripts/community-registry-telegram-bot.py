#!/usr/bin/env python3
"""Telegram inbox and moderation bot for the AngelOS community registry.

Required environment: TELEGRAM_BOT_TOKEN.
Optional: GITHUB_TOKEN for PR review/merge/close actions.
"""
import json
import base64
import html
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "futureUnd1ground/angelos-community-registry"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ROOT = Path(__file__).resolve().parents[1]
MODERATORS_FILE = ROOT / "telegram-moderators.json"
STATE_FILE = Path(os.environ.get("ANGELos_TELEGRAM_STATE", "~/.local/state/angelos-community-registry/telegram.json")).expanduser()
POLL_SECONDS = max(10, int(os.environ.get("TELEGRAM_POLL_SECONDS", "30")))


def http_json(url, method="GET", payload=None, headers=None, timeout=40):
    body = None
    request_headers = {"User-Agent": "angelos-community-registry-telegram-bot"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))


def github(path, method="GET", payload=None):
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = "Bearer " + GH_TOKEN
    return http_json("https://api.github.com" + path, method, payload, headers)


def telegram(method, payload=None):
    if not TG_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return http_json("https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method), "POST", payload)


def moderators():
    values = json.loads(MODERATORS_FILE.read_text(encoding="utf-8"))
    return {str(item).lstrip("@").casefold() for item in values if str(item).strip()}


def is_moderator(message):
    username = (message.get("from") or {}).get("username", "")
    return username.casefold() in moderators()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "seen": {}, "chats": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pr_items():
    return github("/repos/{}/pulls?state=open&base=main&per_page=100".format(REPO))


def candidate_details(pr):
    try:
        full_name = pr["head"]["repo"]["full_name"]
        sha = pr["head"]["sha"]
        content = github("/repos/{}/contents/plugins.json?ref={}".format(full_name, sha))
        decoded = base64.b64decode(content["content"]).decode("utf-8")
        payload = json.loads(decoded)
        return payload.get("plugins", []) if isinstance(payload, dict) else []
    except Exception:
        return []


def format_pr(pr):
    entries = candidate_details(pr)
    esc = lambda value: html.escape(str(value if value is not None else "-"))
    lines = ["<b>Открытый Pull Request</b>", "<b>PR #{}: {}</b>".format(pr.get("number"), esc(pr.get("title"))),
             "Контрибьютор: @{}".format(esc(pr.get("user", {}).get("login", "-"))),
             "Ссылка: {}".format(esc(pr.get("html_url", "-")))]
    if not entries:
        lines.append("plugins.json не удалось прочитать или он отсутствует.")
    for entry in entries[:10]:
        lines.extend(["", "<b>{}</b> v{}".format(esc(entry.get("name", entry.get("id", "-"))), esc(entry.get("version", "-"))),
                      "ID: {}".format(esc(entry.get("id", "-"))),
                      "Автор: {}".format(esc(entry.get("author", "-"))),
                      "Описание: {}".format(esc(entry.get("description", "-"))),
                      "Теги: {}".format(esc(", ".join(map(str, entry.get("tags", []))) or "-")),
                      "Категория: {}".format(esc(entry.get("category", "-"))),
                      "Source: {}".format(esc(entry.get("source", "-"))),
                      "Repository: {}".format(esc(entry.get("repository", "-"))),
                      "License: {}".format(esc(entry.get("license", "-")))])
    return "\n".join(lines)[:3900]


def send(chat_id, text):
    telegram("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


def pr_keyboard(pr):
    if unavailable_pr_message(pr):
        return inactive_pr_keyboard(pr)
    number = str(pr.get("number"))
    return {"inline_keyboard": [
        [{"text": "Открыть PR", "url": pr.get("html_url", "https://github.com/{}/pulls/{}".format(REPO, number))}],
        [{"text": "Одобрить review", "callback_data": "pr:approve:" + number},
         {"text": "Отклонить", "callback_data": "pr:reject:" + number}],
        [{"text": "Merge", "callback_data": "pr:merge:" + number},
         {"text": "Обновить", "callback_data": "pr:refresh:" + number}],
    ]}


def send_pr(chat_id, pr):
    telegram("sendMessage", {"chat_id": chat_id, "text": format_pr(pr), "parse_mode": "HTML",
                              "disable_web_page_preview": True, "reply_markup": pr_keyboard(pr)})


def help_text():
    return ("Команды: /inbox — открытые заявки; /status — состояние бота; "
            "/approve N — одобрить review PR; /reject N — закрыть PR; "
            "/merge N — слить PR после review. Можно пользоваться кнопками под заявкой. "
            "Модерация доступна только allowlist.")


def unavailable_pr_message(pr):
    number = pr.get("number", "?")
    if pr.get("merged"):
        return "PR #{} уже слит в GitHub. Повторный Merge не нужен. Открой /inbox для актуальных заявок.".format(number)
    if pr.get("state") != "open":
        return "PR #{} закрыт без слияния. Открой /inbox для актуальных заявок.".format(number)
    base = pr.get("base", {}).get("ref")
    if base != "main":
        return "PR #{} направлен в ветку {}, а этот бот работает с main.".format(number, base or "неизвестно")
    return ""


def inactive_pr_keyboard(pr):
    number = pr.get("number", "?")
    return {"inline_keyboard": [[{"text": "Открыть PR", "url": pr.get("html_url") or "https://github.com/{}/pull/{}".format(REPO, number)}]]}


def clear_pr_actions(message, pr):
    # Both the original card and its confirmation can have stale buttons.
    message_ids = {message.get("_message_id"), message.get("_source_message_id")} - {None}
    for message_id in message_ids:
        try:
            telegram("editMessageReplyMarkup", {"chat_id": message["chat"]["id"],
                     "message_id": message_id, "reply_markup": inactive_pr_keyboard(pr)})
        except Exception as exc:
            # A deleted message or failed keyboard edit cannot undo a GitHub action.
            print("Could not clear PR buttons: " + type(exc).__name__, flush=True)


def moderate(message, command, number):
    if not is_moderator(message):
        send(message["chat"]["id"], "Доступ запрещён: ваш Telegram username не в списке модераторов.")
        return
    try:
        pr = github("/repos/{}/pulls/{}".format(REPO, int(number)))
        unavailable = unavailable_pr_message(pr)
        if unavailable:
            clear_pr_actions(message, pr)
            send(message["chat"]["id"], unavailable)
            return
        if command == "approve":
            github("/repos/{}/pulls/{}/reviews".format(REPO, number), "POST", {"event": "APPROVE", "body": "Approved by registry Telegram moderator."})
            result = "PR #{} одобрен review.".format(number)
        elif command == "reject":
            github("/repos/{}/issues/{}/comments".format(REPO, number), "POST", {"body": "Rejected by registry Telegram moderator."})
            github("/repos/{}/pulls/{}".format(REPO, number), "PATCH", {"state": "closed"})
            clear_pr_actions(message, dict(pr, state="closed", merged=False))
            result = "PR #{} закрыт.".format(number)
        elif command == "merge":
            merged = github("/repos/{}/pulls/{}/merge".format(REPO, number), "PUT", {"merge_method": "squash"})
            if merged.get("merged"):
                clear_pr_actions(message, dict(pr, state="closed", merged=True))
            else:
                latest = github("/repos/{}/pulls/{}".format(REPO, int(number)))
                if latest.get("merged"):
                    clear_pr_actions(message, latest)
                    send(message["chat"]["id"], unavailable_pr_message(latest))
                    return
            result = "PR #{}: {}".format(number, "смёржен" if merged.get("merged") else merged.get("message", "merge не выполнен"))
        else:
            result = "Неизвестная команда. " + help_text()
        send(message["chat"]["id"], result)
    except Exception as exc:
        if command == "merge":
            try:
                latest = github("/repos/{}/pulls/{}".format(REPO, int(number)))
                if latest.get("merged"):
                    clear_pr_actions(message, latest)
                    send(message["chat"]["id"], unavailable_pr_message(latest))
                    return
            except Exception:
                pass
        send(message["chat"]["id"], "Ошибка GitHub: {}".format(exc))


def handle_update(update, state):
    message = update.get("message") or {}
    text = str(message.get("text", "")).strip()
    chat_id = message.get("chat", {}).get("id")
    if not chat_id or not text:
        return
    parts = text.split()
    command = parts[0].split("@", 1)[0].casefold()
    if not is_moderator(message):
        send(chat_id, "Доступ запрещён: добавьте свой Telegram username в список модераторов.")
        return
    if command in ("/start", "/help"):
        if chat_id not in state["chats"]:
            state["chats"].append(chat_id)
            send(chat_id, "Этот чат добавлен для уведомлений о новых и изменённых PR.")
        send(chat_id, "AngelOS Community Registry bot\n" + help_text())
    elif command == "/inbox":
        prs = pr_items()
        send(chat_id, "Открытых PR: {}".format(len(prs)))
        for pr in prs:
            send_pr(chat_id, pr)
    elif command == "/status":
        send(chat_id, "Бот работает. Открытых PR: {}".format(len(pr_items())))
    elif command in ("/approve", "/reject", "/merge") and len(parts) == 2 and parts[1].isdigit():
        moderate(message, command[1:], parts[1])
    else:
        send(chat_id, help_text())


def handle_callback(update, state):
    callback = update.get("callback_query") or {}
    data = str(callback.get("data", ""))
    message = callback.get("message") or {}
    actor = {"from": callback.get("from") or {}, "chat": message.get("chat") or {},
             "_message_id": message.get("message_id")}
    callback_id = callback.get("id")
    if callback_id:
        telegram("answerCallbackQuery", {"callback_query_id": callback_id})
    if data.startswith("pr:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = parts[1], parts[2]
    elif data.startswith("confirm:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = "confirm:" + parts[1], parts[2]
    elif data.startswith("cancel:") and data.split(":", 1)[1].isdigit():
        action, number = "cancel", data.split(":", 1)[1]
    else:
        return
    if not is_moderator(actor):
        if actor["chat"].get("id"):
            send(actor["chat"]["id"], "Доступ запрещён: ваш Telegram username не в списке модераторов.")
        return
    if action == "refresh":
        for pr in pr_items():
            if str(pr.get("number")) == number:
                send_pr(actor["chat"].get("id"), pr)
                return
        send(actor["chat"].get("id"), "PR #{} больше не открыт.".format(number))
        return
    if action in ("reject", "merge"):
        try:
            pr = github("/repos/{}/pulls/{}".format(REPO, int(number)))
            unavailable = unavailable_pr_message(pr)
            if unavailable:
                clear_pr_actions(actor, pr)
                send(actor["chat"]["id"], unavailable)
                return
        except Exception:
            send(actor["chat"]["id"], "Не удалось проверить состояние PR. Повтори действие позже.")
            return
        key = "{}:{}:{}".format(actor["chat"]["id"], number, action)
        state.setdefault("action_messages", {})[key] = message.get("message_id")
        prompt = "PR #{}: подтвердить действие {}?".format(number, action)
        markup = {"inline_keyboard": [[
            {"text": "Подтвердить", "callback_data": "confirm:{}:{}".format(action, number)},
            {"text": "Отмена", "callback_data": "cancel:{}".format(number)},
        ]]}
        telegram("sendMessage", {"chat_id": actor["chat"].get("id"), "text": prompt, "reply_markup": markup})
    elif action == "approve":
        moderate(actor, action, number)
    elif action.startswith("confirm:"):
        confirmed_action = action.split(":", 1)[1]
        key = "{}:{}:{}".format(actor["chat"]["id"], number, confirmed_action)
        actor["_source_message_id"] = state.setdefault("action_messages", {}).pop(key, None)
        moderate(actor, confirmed_action, number)
    elif action == "cancel":
        for pending_action in ("merge", "reject"):
            key = "{}:{}:{}".format(actor["chat"]["id"], number, pending_action)
            state.setdefault("action_messages", {}).pop(key, None)
        send(actor["chat"].get("id"), "Действие отменено.")


def notify_new_prs(state):
    prs = pr_items()
    active = {str(pr["number"]): pr for pr in prs}
    for number, pr in active.items():
        signature = "{}:{}".format(pr.get("updated_at", ""), pr.get("head", {}).get("sha", ""))
        if state["seen"].get(number) == signature:
            continue
        state["seen"][number] = signature
        for chat_id in state["chats"]:
            send_pr(chat_id, pr)
    state["seen"] = {number: value for number, value in state["seen"].items() if number in active}


def main():
    if not TG_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot")
    state = load_state()
    telegram("getMe")
    while True:
        try:
            notify_new_prs(state)
            response = telegram("getUpdates", {"offset": state["offset"] + 1, "timeout": POLL_SECONDS})
            for update in response.get("result", []):
                state["offset"] = update["update_id"]
                if update.get("callback_query"):
                    handle_callback(update, state)
                else:
                    handle_update(update, state)
            save_state(state)
        except (urllib.error.URLError, TimeoutError) as exc:
            print("network error: {}".format(exc), flush=True)
            time.sleep(5)
        except Exception as exc:
            print("bot error: {}".format(exc), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
