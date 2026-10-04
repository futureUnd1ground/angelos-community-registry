# AngelOS Community Registry

[English version](README.md) | Русская версия

Community Store загружает `plugins.json` из этого репозитория. Пользователям
показываются только записи со статусом `approved`. Новые заявки должны иметь
статус `pending` и проходят проверку сопровождающего репозитория.

## Разработчикам

Полная инструкция по созданию, тестированию, упаковке и публикации плагина
находится в [CONTRIBUTING.ru.md](CONTRIBUTING.ru.md).

Кратко:

1. Создайте плагин AngelOS со своим `manifest.json` и QML-файлами. В manifest
   должны быть уникальные `id`, `name` и `version`.
2. Храните исходники в отдельном GitHub-репозитории и опубликуйте ZIP как asset
   GitHub Release. В архиве должен быть ровно один `manifest.json` на верхнем
   уровне или на один каталог глубже.
3. Добавьте запись в `plugins.json` со статусом `pending` и откройте Pull Request.
   Укажите релиз, репозиторий, лицензию, описание, теги, зависимости и
   необходимые разрешения.
4. После проверки сопровождающий меняет статус на `approved`. Только тогда
   запись появится в Community Store.

## Модераторам

Для установки команды в Fish скачайте и запустите установщик:

```fish
curl -fsSL -o /tmp/install-moderator.fish https://raw.githubusercontent.com/futureUnd1ground/angelos-community-registry/main/install-moderator.fish
and fish /tmp/install-moderator.fish
fish_add_path ~/.local/bin
```

Установщик выводит заметный баннер «ТОЛЬКО ДЛЯ МОДЕРАТОРОВ», устанавливает
команду `community-registry-moderator` для запуска из любой папки и при
необходимости сообщает, как настроить GitHub CLI. Для модерации нужны
`gh auth login` и GitHub-аккаунт из [`moderators.json`](moderators.json).
В allowlist находятся `futureUnd1ground` и `MixaDoDs` — владелец форка
`MixaDoDs/angelos-community-store`.

Проверить доступ, не запуская TUI, можно командой
`community-registry-moderator --check`. Она проверяет вход через GitHub CLI и
сверяет аккаунт с [`moderators.json`](moderators.json). Только прошедшие эту
проверку могут выполнять действия модерации.

TUI показывает единый inbox: записи registry и все открытые Pull Request,
включая PR с ошибочным или отсутствующим `plugins.json`. Клавиши: `p`, `a`,
`R`, `l` — pending, approved, rejected и все записи; `g` — только открытые
PR; `/` — поиск по ID, названию, автору, описанию, тегам и заголовку PR; `t`
— проверка ZIP и manifest; `y`/`n` — одобрение или отклонение записи в registry;
`v` — отправить GitHub review; `m` — проверка и merge PR; `c` — закрытие PR; `o` — открыть ссылку; `r` —
обновить inbox; `j`/`k` или стрелки — перемещение; `q` — выход. Для любого
изменения нужно ввести `YES`. Merge блокируется, если проверка архива не прошла.

Проверка пакета не запускает QML. Плагины выполняются с правами пользователя,
поэтому код необходимо проверять до одобрения. Registry не является песочницей.

## Встроенные плагины AngelOS

Registry содержит зеркала плагинов из AngelOS-Dotfiles: `cat`,
`claude-companion`, `codex-companion`, `nightlight`, `osu-mini`,
`quick-actions`, `speedtest`, `stream-stats` и `web-search`. Это зеркала
исходников upstream, а не независимые переписывания. Проверяйте лицензию и
атрибуцию upstream перед дальнейшим распространением.

`claude-companion` и `codex-companion` требуют соответствующие CLI и вход в
учётную запись; для измерений в `speedtest` нужна программа `speedtest-cli`.
