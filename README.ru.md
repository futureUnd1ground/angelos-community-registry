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

## Telegram-бот модерации

В репозитории есть `scripts/community-registry-telegram-bot.py`. Он отправляет
модераторам новые и изменённые открытые PR с названием, описанием, версией,
ID, контрибьютором, тегами, source, repository и license. Список Telegram
username находится в [`telegram-moderators.json`](telegram-moderators.json):
сейчас там `@futureoffc` и `@psxgld`.

1. Создайте бота через `@BotFather` и получите токен.
2. Скопируйте `telegram-bot.env.example` в защищённый файл и заполните
   `TELEGRAM_BOT_TOKEN` и `GITHUB_TOKEN`.
3. Запустите из корня registry:

```bash
set -a; source ./telegram-bot.env; set +a
python3 scripts/community-registry-telegram-bot.py
```

Модератор должен один раз открыть бота и отправить `/start`; после этого чат
получит уведомления о новых PR. Под каждой заявкой есть кнопки `Открыть PR`,
`Одобрить review`, `Отклонить`, `Merge` и `Обновить`. Текстовые команды также
доступны: `/inbox`, `/status`, `/approve N`, `/reject N`, `/merge N`. Бот
повторно проверяет username и для кнопок, и для команд; токены в Git не
добавляются.

## Отдельное GUI-приложение

Для локальной модерации без TUI установите приложение из клона registry:

```fish
fish install-registry-gui.fish
```

После установки оно появляется в меню приложений как `AngelOS Community
Registry`, а также запускается командой `community-registry-gui`. Приложение
использует текущую авторизацию `gh`, показывает открытые PR, поиск, описание,
автора, версию, теги, source и license. Действия `Одобрить review`, `Отклонить`
и `Merge` требуют подтверждения. Интерфейс использует единую тёмную палитру,
чтобы системные светлые кнопки и поля не смешивались с тёмным окном.

## Встроенные плагины AngelOS

Registry содержит зеркала плагинов из AngelOS-Dotfiles: `cat`,
`claude-companion`, `codex-companion`, `nightlight`, `osu-mini`,
`quick-actions`, `speedtest`, `stream-stats` и `web-search`. Это зеркала
исходников upstream, а не независимые переписывания. Проверяйте лицензию и
атрибуцию upstream перед дальнейшим распространением.

`claude-companion` и `codex-companion` требуют соответствующие CLI и вход в
учётную запись; для измерений в `speedtest` нужна программа `speedtest-cli`.

### Вход и диагностика бота

Команда `/github` показывает аккаунт GitHub и доступ к репозиторию. Ошибка JSON содержит репозиторий, версию файла, строку и столбец: повторный вход не исправляет сломанный `plugins.json`. Бот блокирует Merge некорректных метаданных. Технические PR с кодом или документацией можно слить без архива плагина. Чтение повторяется при временных обрывах сети; операции записи автоматически не повторяются. При запуске бот ждёт восстановления сети. Проверки реестра и тесты бота запускаются в GitHub Actions для PR и main.
