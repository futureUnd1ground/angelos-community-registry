#!/usr/bin/env python3
"""Telegram inbox and moderation bot for the AngelOS community registry.

Required environment: TELEGRAM_BOT_TOKEN.
Required for moderation actions: GITHUB_TOKEN with contents write, pull request write, and issues write access.
"""
import json
import base64
import html
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = "futureUnd1ground/angelos-community-registry"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = Path(os.environ.get("ANGELOS_TELEGRAM_STATE", "~/.local/state/angelos-community-registry/telegram.json")).expanduser()
POLL_SECONDS = max(10, int(os.environ.get("TELEGRAM_POLL_SECONDS", "30")))
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILES = 2000
MAX_OUTBOX = 5000
MAX_DELIVERY_ERRORS = 50
CHAT_SEND_INTERVAL = 1.05
BOT_STATE = None
LAST_CHAT_SEND = {}
GITHUB_LOGIN = None
CONDITION_PRESETS = {
    "screenshots": "Добавьте скриншоты работы плагина и основных состояний интерфейса.",
    "description": "Дополните описание PR: что изменено, как проверить и какие ограничения есть.",
    "archive": "Исправьте ZIP-архив и manifest.json: проверьте структуру, ID, версию и обязательные файлы.",
    "testing": "Добавьте результаты проверки плагина в AngelOS и укажите окружение тестирования.",
}


class ApiError(RuntimeError):
    def __init__(self, code, message, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        self.message = message
        super().__init__("HTTP {}{}".format(code, ": " + message if message else ""))


class RegistryValidationError(RuntimeError):
    pass


class TransportError(RuntimeError):
    pass


def github_error_message(error):
    if not isinstance(error, ApiError):
        return safe_error(error)
    messages = {
        401: "GitHub отклонил токен. Проверь GITHUB_TOKEN; /github покажет состояние входа.",
        403: "GitHub запретил действие: проверь права и лимит API через /github.",
        404: "Ресурс GitHub не найден или недоступен этому аккаунту.",
        409: "GitHub сообщает о конфликте состояния PR: обнови список и повтори действие.",
        422: "GitHub отклонил действие: {}".format(safe_error(error.message)),
    }
    return messages.get(error.code, safe_error(error))


def moderation_error_message(error):
    if isinstance(error, ApiError):
        return "GitHub не выполнил действие: " + github_error_message(error)
    if isinstance(error, (TransportError, urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return "Не удалось связаться с сервисом. Повтори действие после восстановления сети: " + safe_error(error)
    return "Действие остановлено: " + safe_error(error)


def http_json(url, method="GET", payload=None, headers=None, timeout=40):
    body = None
    request_headers = {"User-Agent": "angelos-community-registry-telegram-bot"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    parsed = urllib.parse.urlparse(url)
    service = "GitHub" if parsed.hostname == "api.github.com" else "Telegram"
    # Only reads can be retried automatically. A timed-out write might have succeeded.
    read_only = method == "GET" or (parsed.hostname == "api.telegram.org" and parsed.path.rsplit("/", 1)[-1] in ("getMe", "getUpdates"))
    attempts = 3 if read_only else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                details = json.loads(exc.read(64 * 1024).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                details = {}
            finally:
                exc.close()
            if not isinstance(details, dict): details = {}
            message = str(details.get("message") or details.get("description") or "")
            errors = details.get("errors")
            if isinstance(errors, list) and errors:
                message += "; " + "; ".join(str(item.get("message", item)) if isinstance(item, dict) else str(item) for item in errors)
            parameters = details.get("parameters") or {}
            retry_after = parameters.get("retry_after", 0) if isinstance(parameters, dict) else 0
            if exc.code in (502, 503, 504) and attempt + 1 < attempts:
                time.sleep(attempt + 1)
                continue
            raise ApiError(exc.code, message, retry_after) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt + 1 < attempts:
                time.sleep(attempt + 1)
                continue
            raise TransportError("{}: {}".format(service, safe_error(exc))) from None


def github(path, method="GET", payload=None):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GH_TOKEN:
        headers["Authorization"] = "Bearer " + GH_TOKEN
    return http_json("https://api.github.com" + path, method, payload, headers, timeout=12 if method == "GET" else 40)


def github_login():
    global GITHUB_LOGIN
    if GITHUB_LOGIN is None:
        profile = github("/user")
        login = str(profile.get("login") or "").strip().casefold()
        if not login:
            raise RuntimeError("GitHub token identity is unavailable")
        GITHUB_LOGIN = login
    return GITHUB_LOGIN


def github_status():
    if not GH_TOKEN:
        return "GitHub: вход не настроен (GITHUB_TOKEN)."
    try:
        login = github_login()
        repo = github("/repos/" + REPO)
        permission = repo.get("permissions") or {}
        write = bool(permission.get("push") or permission.get("admin") or permission.get("maintain"))
        return "GitHub: вход выполнен как {}. Репозиторий: {}. Право записи: {}.".format(login, REPO, "есть" if write else "нет; проверь доступ аккаунта и права токена")
    except Exception as exc:
        return "Проверка входа GitHub: " + github_error_message(exc)


def telegram(method, payload=None):
    if not TG_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    timeout = max(40, int((payload or {}).get("timeout", 0)) + 10)
    response = http_json("https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method), "POST", payload, timeout=timeout)
    if not response.get("ok"):
        parameters = response.get("parameters") or {}
        raise ApiError(response.get("error_code", 0), response.get("description", "Telegram API request failed"),
                       parameters.get("retry_after", 0))
    return response


def safe_error(error):
    text = str(error)
    for secret in (TG_TOKEN, GH_TOKEN):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def is_stale_callback_error(error):
    return isinstance(error, ApiError) and error.code == 400 and "query is too old" in str(error).casefold()


def moderators_file():
    configured = os.environ.get("ANGELOS_TELEGRAM_MODERATORS")
    if configured:
        return Path(configured).expanduser()
    repository_file = ROOT / "telegram-moderators.json"
    if repository_file.is_file():
        return repository_file
    return Path.home() / ".local/share/telegram-moderators.json"


def moderators():
    values = json.loads(moderators_file().read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise RuntimeError("telegram-moderators.json must contain a JSON array")
    return {str(item).lstrip("@").casefold() for item in values if str(item).strip()}


def is_moderator(message):
    username = str((message.get("from") or {}).get("username") or "")
    return username.casefold() in moderators()


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "seen": {}, "chats": {}, "metadata_cache": {}, "outbox": [], "pending_conditions": {}}
    if not isinstance(state, dict):
        return {"offset": 0, "seen": {}, "chats": {}, "metadata_cache": {}, "outbox": [], "pending_conditions": {}}
    # Old versions stored chat IDs without their owner. Discard these so only
    # explicitly re-subscribed moderators receive registry content.
    if not isinstance(state.get("chats"), dict):
        state["chats"] = {}
    if not isinstance(state.get("seen"), dict):
        state["seen"] = {}
    if not isinstance(state.get("metadata_cache"), dict):
        state["metadata_cache"] = {}
    if not isinstance(state.get("outbox"), list):
        state["outbox"] = []
    state["outbox"] = [item for item in state["outbox"] if isinstance(item, dict)][:MAX_OUTBOX]
    if not isinstance(state.get("delivery_errors"), list):
        state["delivery_errors"] = []
    state["delivery_errors"] = [item for item in state["delivery_errors"] if isinstance(item, dict)][-MAX_DELIVERY_ERRORS:]
    if not isinstance(state.get("pending_conditions"), dict):
        state["pending_conditions"] = {}
    state.setdefault("offset", 0)
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, STATE_FILE)


def pr_items():
    result = []
    page = 1
    while True:
        items = github("/repos/{}/pulls?state=open&base=main&per_page=100&page={}".format(REPO, page))
        result.extend(items)
        if len(items) < 100:
            return result
        page += 1


def registry_snapshot(repository, ref):
    content = github("/repos/{}/contents/plugins.json?ref={}".format(repository, ref))
    decoded = base64.b64decode(content["content"]).decode("utf-8")
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise RegistryValidationError("Некорректный plugins.json в {} ({}): строка {}, столбец {}. {}. Исправь JSON в этой версии PR; повторный вход в GitHub не нужен.".format(repository, ref[:12], exc.lineno, exc.colno, exc.msg)) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
        raise RuntimeError("Invalid plugins.json registry format")
    result = {}
    for item in payload["plugins"]:
        if not isinstance(item, dict) or not item.get("id"):
            raise RuntimeError("Registry contains a plugin without an ID")
        plugin_id = str(item["id"])
        if plugin_id in result:
            raise RuntimeError("Registry contains duplicate plugin ID: {}".format(plugin_id))
        result[plugin_id] = item
    return result, content.get("sha", "")


def registry_plugins(repository, ref):
    return registry_snapshot(repository, ref)[0]


def changed_plugins(pr, base_plugins=None):
    try:
        head = pr.get("head", {})
        base = pr.get("base", {})
        head_repository = head.get("repo", {}).get("full_name")
        base_repository = base.get("repo", {}).get("full_name") or REPO
        base_ref = base.get("sha") or "main"
        head_ref = head.get("sha")
        if not head_repository or not head_ref:
            raise RuntimeError("PR source repository is unavailable")
        if base_plugins is None:
            base_plugins = registry_plugins(base_repository, base_ref)
        candidate = registry_plugins(head_repository, head_ref)
        changes = []
        for plugin_id in sorted(set(base_plugins) | set(candidate)):
            before, after = base_plugins.get(plugin_id), candidate.get(plugin_id)
            if before == after:
                continue
            plugin = dict(after or before)
            plugin["_change"] = "added" if before is None else "removed" if after is None else "updated"
            plugin["_removed"] = after is None
            changes.append(plugin)
        return changes, None
    except Exception as exc:
        return [], str(exc)


def fetch_prs(state=None):
    prs = pr_items()
    rows = []
    cache = state.setdefault("metadata_cache", {}) if state is not None else {}
    active = set()
    for pr in prs:
        number = str(pr.get("number"))
        active.add(number)
        head_sha = pr.get("head", {}).get("sha", "")
        cached = cache.get(number, {})
        if not isinstance(cached, dict):
            cached = {}
        base_sha = pr.get("base", {}).get("sha", "")
        if not cached.get("error") and cached.get("head_sha") == head_sha and cached.get("base_sha") == base_sha:
            changes, error = cached.get("changes", []), cached.get("error")
        else:
            changes, error = changed_plugins(pr)
            cache[number] = {"head_sha": head_sha, "base_sha": base_sha,
                             "changes": changes, "error": error}
        pr["_plugin_changes"] = changes
        pr["_registry_error"] = error
        pr["_base_sha"] = base_sha
        rows.append(pr)
    for number in list(cache):
        if number not in active:
            del cache[number]
    return sorted(rows, key=lambda pr: pr.get("created_at", ""))


def validate_archive(entry):
    source = str(entry.get("source", ""))
    parsed_source = urllib.parse.urlparse(source)
    if parsed_source.scheme != "https" or parsed_source.hostname != "github.com":
        raise RuntimeError("Plugin source must be a GitHub HTTPS release URL")
    request = urllib.request.Request(source, headers={"User-Agent": "angelos-registry-telegram-moderator"})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive_data = response.read(MAX_ARCHIVE + 1)
        final_url = urllib.parse.urlparse(response.geturl())
    final_host = final_url.hostname or ""
    if final_url.scheme != "https" or not (final_host == "github.com" or final_host.endswith(".githubusercontent.com")):
        raise RuntimeError("Plugin download redirected to an untrusted host")
    if len(archive_data) > MAX_ARCHIVE:
        raise RuntimeError("Archive exceeds 64 MiB")
    with TemporaryDirectory(prefix="angelos-telegram-review-") as temp:
        root = Path(temp) / "unpacked"
        root.mkdir()
        archive_path = Path(temp) / "plugin.zip"
        archive_path.write_bytes(archive_data)
        with zipfile.ZipFile(archive_path) as archive:
            items = archive.infolist()
            if len(items) > MAX_FILES:
                raise RuntimeError("Archive has too many files")
            if sum(item.file_size for item in items) > MAX_ARCHIVE:
                raise RuntimeError("Expanded archive exceeds 64 MiB")
            for item in items:
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RuntimeError("Archive contains a symbolic link")
                target = (root / item.filename).resolve()
                if not str(target).startswith(str(root.resolve()) + os.sep):
                    raise RuntimeError("Archive contains an unsafe path")
            archive.extractall(root)
        manifests = list(root.rglob("manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError("Archive must contain exactly one manifest.json")
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        if manifest.get("id") != entry.get("id"):
            raise RuntimeError("Manifest ID does not match registry entry")
        if str(manifest.get("version", "")) != str(entry.get("version", "")):
            raise RuntimeError("Manifest version does not match registry entry")
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            raise RuntimeError("Manifest name is missing")


def approve_registry_entries(plugin_ids, pr_number):
    response = github("/repos/{}/contents/plugins.json?ref=main".format(REPO))
    payload = json.loads(base64.b64decode(response["content"]).decode("utf-8"))
    found = set()
    changed = False
    for entry in payload.get("plugins", []):
        if entry.get("id") in plugin_ids:
            found.add(entry["id"])
            if entry.get("status") != "approved":
                entry["status"] = "approved"
                changed = True
    missing = set(plugin_ids) - found
    if missing:
        raise RuntimeError("Merged registry is missing plugin entries: {}".format(", ".join(sorted(missing))))
    if not changed:
        return ""
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    result = github("/repos/{}/contents/plugins.json".format(REPO), "PUT", {
        "message": "Approve plugin listings from PR #{}".format(pr_number),
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": response["sha"],
        "branch": "main"
    })
    return result.get("commit", {}).get("sha", "")


def format_pr(pr):
    esc = lambda value, limit: html.escape(str(value if value is not None else "-")[:limit])
    changes = pr.get("_plugin_changes", [])
    error = pr.get("_registry_error")
    if error:
        review = "plugins.json не удалось прочитать: {}".format(error)
    elif not changes:
        review = "Изменений в plugins.json нет; это PR без заявки на плагин."
    else:
        review = "Изменения записей плагинов: {}".format(len(changes))
    lines = ["<b>Открытый Pull Request #{}: {}</b>".format(pr.get("number"), esc(pr.get("title"), 100)),
             "Контрибьютор: @{}".format(esc(pr.get("user", {}).get("login", "-"), 50)),
             "Ветка: {}".format(esc(pr.get("head", {}).get("label", "-"), 100)),
             "Состояние: {}".format(esc(review, 220)),
             "Описание PR: {}".format(esc(pr.get("body") or "-", 400))]
    return "\n".join(lines)


def format_plugin(entry):
    esc = lambda value, limit: html.escape(str(value if value is not None else "-")[:limit])
    def list_value(key):
        value = entry.get(key)
        if isinstance(value, list):
            return ", ".join(map(str, value)) or "-"
        return str(value) if value else "-"

    change = {"added": "Добавлен", "updated": "Обновлён", "removed": "Удаляется"}.get(entry.get("_change"), "Изменён")
    lines = ["<b>{}: {} v{}</b>".format(change, esc(entry.get("name", entry.get("id", "-")), 50), esc(entry.get("version", "-"), 15)),
             "ID: {}".format(esc(entry.get("id", "-"), 40)),
             "Автор плагина: {}".format(esc(entry.get("author", "-"), 35)),
             "Описание: {}".format(esc(entry.get("description", "-") or "-", 140)),
             "Теги: {}".format(esc(list_value("tags"), 70)),
             "Категория: {}".format(esc(entry.get("category", "-"), 25)),
             "Source: {}".format(esc(entry.get("source", "-") or "-", 90)),
             "Repository: {}".format(esc(entry.get("repository", "-") or "-", 70)),
             "License: {}".format(esc(entry.get("license", "-"), 40)),
             "Dependencies: {}".format(esc(list_value("dependencies"), 50)),
             "Permissions: {}".format(esc(list_value("permissions"), 50))]
    return "\n".join(lines)


def send(chat_id, text):
    queue_message(chat_id, html.escape(str(text)), parse_mode="HTML", disable_web_page_preview=True)


def queue_message(chat_id, text, **options):
    if BOT_STATE is None:
        telegram("sendMessage", {"chat_id": chat_id, "text": text, **options})
        return
    outbox = BOT_STATE.setdefault("outbox", [])
    if len(outbox) >= MAX_OUTBOX:
        raise RuntimeError("Outgoing message queue is full; use /inbox after it drains")
    outbox.append({"chat_id": chat_id, "text": text, **options})


def flush_outbox(state):
    while state["outbox"]:
        item = state["outbox"][0]
        chat_key = str(item.get("chat_id"))
        delay = CHAT_SEND_INTERVAL - (time.monotonic() - LAST_CHAT_SEND.get(chat_key, 0))
        if delay > 0:
            time.sleep(delay)
        try:
            telegram("sendMessage", item)
        except ApiError as exc:
            if exc.code == 429 and exc.retry_after:
                time.sleep(min(max(float(exc.retry_after), 1), 300) + 0.25)
                telegram("sendMessage", item)
            elif 400 <= exc.code < 500:
                errors = state.setdefault("delivery_errors", [])
                if not isinstance(errors, list):
                    errors = state["delivery_errors"] = []
                errors.append({"chat_id": chat_key, "error": safe_error(exc), "time": int(time.time())})
                del errors[:-MAX_DELIVERY_ERRORS]
                if exc.code == 403:
                    state["chats"].pop(chat_key, None)
                    state["outbox"] = [queued for queued in state["outbox"]
                                        if str(queued.get("chat_id")) != chat_key]
                else:
                    state["outbox"].pop(0)
                save_state(state)
                print("Telegram delivery rejected for chat {}: {}".format(chat_key, safe_error(exc)), flush=True)
                continue
            else:
                raise
        LAST_CHAT_SEND[chat_key] = time.monotonic()
        state["outbox"].pop(0)
        save_state(state)


def pr_keyboard(pr, page=None):
    if unavailable_pr_message(pr):
        return inactive_pr_keyboard(pr)
    number = str(pr.get("number"))
    rows = [
        [{"text": "Открыть PR", "url": pr.get("html_url", "https://github.com/{}/pulls/{}".format(REPO, number))}],
        [{"text": "Одобрить review", "callback_data": "pr:approve:" + number},
         {"text": "Отклонить", "callback_data": "pr:reject:" + number}],
        [{"text": "Merge", "callback_data": "pr:merge:" + number},
         {"text": "Обновить", "callback_data": "pr:refresh:" + number}],
        [{"text": "Условие автору", "callback_data": "conditions:" + number}],
    ]
    if page:
        navigation = []
        if page["index"] > 0:
            navigation.append({"text": "← Назад", "callback_data": "page:prev:" + number})
        if page["index"] + 1 < page["total"]:
            navigation.append({"text": "Дальше →", "callback_data": "page:next:" + number})
        if navigation:
            rows.append(navigation)
    return {"inline_keyboard": rows}


def send_pr(chat_id, pr, page=None, message_id=None):
    parts = [format_pr(pr)]
    omitted = False
    for entry in pr.get("_plugin_changes", []):
        detail = format_plugin(entry)
        if sum(map(len, parts)) + len(detail) + 200 > 3900:
            omitted = True
            break
        parts.append(detail)
    if pr.get("_registry_error"):
        parts.append("<b>Ошибка проверки:</b> Не удалось проверить метаданные плагина. Перед merge исправь plugins.json.")
    if omitted:
        parts.append("<i>Остальные записи не поместились в одно сообщение.</i>")
    if page:
        parts.insert(0, "<b>Открытые заявки: {} / {}</b>".format(page["index"] + 1, page["total"]))
    text = "\n\n".join(parts)
    payload = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
               "disable_web_page_preview": True, "reply_markup": pr_keyboard(pr, page)}
    if message_id:
        payload["message_id"] = message_id
        telegram("editMessageText", payload)
    else:
        queue_message(chat_id, payload.pop("text"), **{key: value for key, value in payload.items() if key != "chat_id"})


def show_inbox_page(chat_id, prs, index, message_id=None):
    if not prs:
        text = "Открытых PR нет."
        if message_id:
            telegram("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})
        else:
            send(chat_id, text)
        return
    index = max(0, min(index, len(prs) - 1))
    send_pr(chat_id, prs[index], {"index": index, "total": len(prs)}, message_id)


def help_text():
    return ("Команды: /inbox — открытые заявки; /status — состояние бота; "
            "/errors — последние ошибки доставки; "
            "/approve N — одобрить review PR; /reject N — закрыть PR; "
            "/github — проверить вход и права GitHub; "
            "/merge N — проверить пакеты и слить PR; "
            "/conditions N текст — добавить условие автору в GitHub; "
            "/stop — отключить уведомления. "
            "Можно пользоваться кнопками под заявкой. "
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
            author = str(pr.get("user", {}).get("login") or "").strip().casefold()
            if not author:
                raise RuntimeError("У PR не указан автор, одобрение невозможно")
            if author == github_login():
                result = ("PR #{} нельзя одобрить: GitHub не разрешает автору "
                          "одобрять собственный pull request.").format(number)
                send(message["chat"]["id"], result)
                return
            github("/repos/{}/pulls/{}/reviews".format(REPO, number), "POST", {
                "event": "APPROVE", "body": "Approved by registry Telegram moderator.",
                "commit_id": pr.get("head", {}).get("sha")
            })
            result = "PR #{} одобрен review.".format(number)
        elif command == "reject":
            github("/repos/{}/issues/{}/comments".format(REPO, number), "POST", {"body": "Rejected by registry Telegram moderator."})
            github("/repos/{}/pulls/{}".format(REPO, number), "PATCH", {"state": "closed"})
            clear_pr_actions(message, dict(pr, state="closed", merged=False))
            result = "PR #{} закрыт.".format(number)
        elif command == "conditions":
            condition = str(message.get("_condition", "")).strip()
            if not condition:
                raise RuntimeError("Укажи текст условия после номера PR")
            github("/repos/{}/issues/{}/comments".format(REPO, number), "POST", {
                "body": "Условие для принятия PR от модератора:\n\n" + condition
            })
            result = "Условие добавлено в PR #{} и опубликовано на GitHub.".format(number)
        elif command == "merge":
            if pr.get("mergeable") is False:
                raise RuntimeError("PR нельзя слить: GitHub сообщает о конфликте или незавершённой проверке")
            changes, registry_error = changed_plugins(pr)
            if registry_error:
                raise RegistryValidationError("Не удалось проверить метаданные PR: {}".format(registry_error))
            if not changes:
                files = github("/repos/{}/pulls/{}/files?per_page=100".format(REPO, number))
                if not isinstance(files, list) or not files or len(files) >= 100 or any(f.get("filename") == "plugins.json" or f.get("previous_filename") == "plugins.json" for f in files):
                    raise RegistryValidationError("Не найдены изменения записей plugins.json для проверки. Обнови PR и повтори Merge.")
                # Code/docs-only maintenance PRs do not contain plugin packages.
            plugin_changes = [entry for entry in changes if not entry.get("_removed")]
            for entry in plugin_changes:
                validate_archive(entry)
            merged = github("/repos/{}/pulls/{}/merge".format(REPO, number), "PUT", {
                "merge_method": "squash", "sha": pr.get("head", {}).get("sha")
            })
            if not merged.get("merged"):
                latest = github("/repos/{}/pulls/{}".format(REPO, int(number)))
                if latest.get("merged"):
                    clear_pr_actions(message, latest)
                    send(message["chat"]["id"], unavailable_pr_message(latest))
                    return
                raise RuntimeError(merged.get("message", "GitHub did not merge the pull request"))
            clear_pr_actions(message, dict(pr, state="closed", merged=True))
            try:
                approved_ids = [entry["id"] for entry in plugin_changes]
                if approved_ids:
                    approve_registry_entries(approved_ids, number)
                removed_count = sum(1 for entry in changes if entry.get("_removed"))
                result = "PR #{} смёржен; {} плагинов активированы, удалено записей: {}.".format(
                    number, len(approved_ids), removed_count)
            except Exception as exc:
                result = "PR #{} смёржен, архивы проверены, но статус остался pending: {}".format(number, exc)
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
        print("moderation {} PR #{}: {}".format(command, number, safe_error(exc)), flush=True)
        send(message["chat"]["id"], moderation_error_message(exc))


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
    if message.get("chat", {}).get("type") != "private":
        send(chat_id, "Команды модерации работают только в личном чате с ботом.")
        return
    pending = state.get("pending_conditions", {}).get(str(chat_id))
    if pending and not command.startswith("/"):
        state["pending_conditions"].pop(str(chat_id), None)
        message["_condition"] = text
        moderate(message, "conditions", str(pending))
        return
    if command == "/cancel" and pending:
        state["pending_conditions"].pop(str(chat_id), None)
        send(chat_id, "Ввод условия отменён.")
        return
    if command == "/start":
        username = str((message.get("from") or {}).get("username") or "")
        key = str(chat_id)
        if state["chats"].get(key) != username:
            state["chats"][key] = username
            send(chat_id, "Этот чат добавлен для уведомлений о новых и изменённых PR.")
        send(chat_id, "AngelOS Community Registry bot\n" + help_text())
    elif command == "/help":
        send(chat_id, help_text())
    elif command == "/github":
        send(chat_id, github_status())
    elif command == "/stop":
        state["chats"].pop(str(chat_id), None)
        send(chat_id, "Уведомления отключены для этого чата.")
    elif command == "/inbox":
        prs = fetch_prs(state)
        show_inbox_page(chat_id, prs, 0)
    elif command == "/status":
        try:
            open_count = str(len(fetch_prs(state)))
        except Exception as exc:
            open_count = "недоступно ({})".format(safe_error(exc))
        send(chat_id, "Бот работает. Открытых PR: {}. В очереди: {}. Ошибок доставки: {}. Уведомления: {}.".format(
            open_count, len(state["outbox"]), len(state.get("delivery_errors", [])),
            "включены" if str(chat_id) in state["chats"] else "выключены"))
    elif command == "/errors":
        errors = state.get("delivery_errors", [])[-10:]
        if not errors:
            send(chat_id, "Ошибок доставки нет.")
        else:
            lines = ["Последние ошибки доставки:"]
            lines.extend("chat {}: {}".format(item.get("chat_id", "?"), item.get("error", "ошибка"))
                         for item in errors)
            send(chat_id, "\n".join(lines))
    elif command in ("/approve", "/reject", "/merge") and len(parts) == 2 and parts[1].isdigit():
        moderate(message, command[1:], parts[1])
    elif command == "/conditions" and len(parts) >= 3 and parts[1].isdigit():
        message["_condition"] = text.split(None, 2)[2]
        moderate(message, "conditions", parts[1])
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
        try:
            telegram("answerCallbackQuery", {"callback_query_id": callback_id})
        except ApiError as exc:
            # Telegram expires callback queries quickly. The button action can
            # no longer be acknowledged, but this must not disrupt polling.
            if not is_stale_callback_error(exc):
                raise
    if data.startswith("pr:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = parts[1], parts[2]
    elif data.startswith("page:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = "page:" + parts[1], parts[2]
    elif data.startswith("confirm:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, number = "confirm:" + parts[1], parts[2]
    elif data.startswith("condition:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, condition_kind, number = "condition", parts[1], parts[2]
    elif data.startswith("conditions:") and data.split(":", 1)[1].isdigit():
        action, number = "conditions", data.split(":", 1)[1]
    elif data.startswith("cancel:") and data.split(":", 1)[1].isdigit():
        action, number = "cancel", data.split(":", 1)[1]
    else:
        return
    if not is_moderator(actor):
        if actor["chat"].get("id"):
            send(actor["chat"]["id"], "Доступ запрещён: ваш Telegram username не в списке модераторов.")
        return
    if actor["chat"].get("type") != "private":
        send(actor["chat"].get("id"), "Кнопки модерации работают только в личном чате с ботом.")
        return
    if action == "refresh":
        for pr in fetch_prs(state):
            if str(pr.get("number")) == number:
                send_pr(actor["chat"].get("id"), pr)
                return
        send(actor["chat"].get("id"), "PR #{} больше не открыт.".format(number))
        return
    if action.startswith("page:"):
        prs = fetch_prs(state)
        current = next((i for i, pr in enumerate(prs) if str(pr.get("number")) == number), 0)
        if action == "page:next":
            current += 1
        elif action == "page:prev":
            current -= 1
        show_inbox_page(actor["chat"].get("id"), prs, current, message.get("message_id"))
        return
    if action == "conditions":
        chat_id = str(actor["chat"].get("id"))
        queue_message(actor["chat"].get("id"), "Выбери условие для автора PR #{}:".format(number), reply_markup={
            "inline_keyboard": [
                [{"text": "Добавить скриншоты", "callback_data": "condition:screenshots:" + number}],
                [{"text": "Уточнить описание", "callback_data": "condition:description:" + number}],
                [{"text": "Исправить архив / manifest", "callback_data": "condition:archive:" + number}],
                [{"text": "Добавить тестирование", "callback_data": "condition:testing:" + number}],
                [{"text": "Другое...", "callback_data": "condition:custom:" + number},
                 {"text": "Отмена", "callback_data": "cancel:" + number}],
            ]
        })
        return
    if action == "condition":
        chat_id = str(actor["chat"].get("id"))
        if condition_kind in CONDITION_PRESETS:
            actor["_condition"] = CONDITION_PRESETS[condition_kind]
            moderate(actor, "conditions", number)
            return
        state.setdefault("pending_conditions", {})[chat_id] = number
        queue_message(actor["chat"].get("id"),
                      "Напиши условие для автора PR #{}. Следующее сообщение будет опубликовано в GitHub. /cancel — отмена.".format(number),
                      reply_markup={"force_reply": True, "input_field_placeholder": "Текст условия"})
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
        queue_message(actor["chat"].get("id"), prompt, reply_markup=markup)
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
    prs = fetch_prs(state)
    allowed = moderators()
    state["chats"] = {chat_id: username for chat_id, username in state["chats"].items()
                       if str(username).casefold() in allowed}
    active = {str(pr["number"]): pr for pr in prs}
    changed = []
    for index, pr in enumerate(prs):
        number = str(pr["number"])
        signature = "{}:{}:{}".format(pr.get("updated_at", ""), pr.get("head", {}).get("sha", ""), pr.get("_base_sha", ""))
        if state["seen"].get(number) == signature:
            continue
        state["seen"][number] = signature
        changed.append((index, pr))
    for index, pr in changed:
        # Every new or updated PR gets its own queued notification. The outbox
        # rate limiter spaces deliveries so bursts remain readable.
        page = {"index": index, "total": len(prs)}
        for chat_id in state["chats"]:
            send_pr(int(chat_id), pr, page)
    state["seen"] = {number: value for number, value in state["seen"].items() if number in active}


def main():
    global BOT_STATE
    if not TG_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot")
    if not GH_TOKEN:
        raise SystemExit("Set GITHUB_TOKEN with contents write, pull request write, and issues write access")
    state = load_state()
    BOT_STATE = state
    startup_verified = False
    while True:
        try:
            if not startup_verified:
                telegram("getMe")
                startup_verified = True
            flush_outbox(state)
            response = telegram("getUpdates", {"offset": state["offset"] + 1, "timeout": POLL_SECONDS})
            for update in response.get("result", []):
                state["offset"] = update["update_id"]
                try:
                    if update.get("callback_query"):
                        handle_callback(update, state)
                    else:
                        handle_update(update, state)
                except Exception as exc:
                    print("update error: {}".format(safe_error(exc)), flush=True)
                save_state(state)
                flush_outbox(state)
            try:
                notify_new_prs(state)
                save_state(state)
                flush_outbox(state)
            except Exception as exc:
                print("registry polling error: {}".format(safe_error(exc)), flush=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            print("network error while contacting Telegram/GitHub: {}".format(safe_error(exc)), flush=True)
            time.sleep(5)
        except Exception as exc:
            print("bot error: {}".format(safe_error(exc)), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
