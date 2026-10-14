# Итоговый отчёт миссии (RU)

**Миссия:** Telegram-часть AHAWR — уведомление TASK STARTED и control bot.
**Задача T013:** обновить README.md и CHANGELOG.md (бот-контроль и новый патч-скрипт), переписать этот отчёт на русском — только о Telegram-части (TASK STARTED и control bot). Удалить лишний вывод-файл `check_workflows_output.txt` из корня репозитория.

---

## 1. Уведомление TASK STARTED

Узел `Send a text message` в обоих v13-файлах (`AHAWR_v13_ClaudeCode.json` и `AHAWR_v13.json`) переписан. Текст собирается только из полей задачи:

- TASK STARTED (заголовок);
- Worker: модель (и провайдер, если задан);
- `current_task_id` + заголовок задачи + `(attempt N)` при повторе;
- секции `OBJECTIVE`, `ACCEPTANCE CRITERIA`, `VERIFICATION`, `SCOPE`;
- при повторе (attempt > 1 и есть feedback): `PREVIOUS REVIEW (score X):` + reason + `Asked next: <feedback>`.

Шаблон worker-промпта, блок working-directory и контекст из уведомления убраны.
Длинный текст режется по границам секций, а не по «слепым» 3800 символам: если общий
объём превышает лимит, части режутся по секциям; отдельная секция, не влезающая в
лимит, обрезается с маркером `…`. HTML экранируется **после** резки
(`.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')`), чтобы не разрезать
HTML-сущность.

Узлы `Build Worker Run Input` и `Prepare Current Task` в обоих файлах не тронуты:
jsCode узла `Build Worker Run Input` до/после не изменился (sha256 совпадает),
`git diff` обоих v13-файлов показывает, что изменилось только поле `text` узла
`Send a text message`.

---

## 2. Control bot (`AHAWR_Status_Bot.json`)

Сгенерирован `scripts/patch_status_bot_control.py` (39 узлов).

- **Long-polling**: `Poll Telegram` (scheduleTrigger) — раз в 60 s; `Get Updates` —
  timeout запроса **25 s** (long-poll), `limit` 100, `allowed_updates` =
  `["message","callback_query"]`, HTTP-таймаут клиента 30000 ms. ~1440
  executions/day.
- **Polling fix**: runs не пересекаются — статический флаг `poll_in_progress`
  выставляется в `Prepare Poll` перед getUpdates и снимается в `Expand Updates`;
  триггер, нашедший флаг установленным, пропускает run.
- **Allow-list чатов**: `Parse Command` разбирает `Config.telegram_chat_id` по `;`,
  пробелам или запятым; только разрешённые чаты получают ответ, остальные
  считаются молча (счётчик `bot_ignored_count`).
- **Два уровня доступа**: admin (control и edit) и viewer (view).
- **Подтверждение мутирующих команд**: inline Approve/Deny кнопки или одноразовый
  код, TTL 2 минуты; pending привязан к чату (callback/`/approve` из другого чата
  отклоняется), одноразовое использование (повторное использование не меняет
  значение), просроченный код отклоняется ("code expired").
- **Insert-based audit log**: каждое подтверждённое изменение записывается в
  `AHAWR Bot Audit Log` (7 колонок, `id` auto-increment) отдельным insert-узлом;
  проверка записи — перечитывание строки: ответ "было -> стало" только если
  перечитанная строка несёт записанное значение; сбой записи аудится `failed`.
- **Honest queue replies**: бот не запускает runs сам — подтверждённые `/start`,
  `/resume`, `/restart` вставляют строку в `AHAWR Command Queue`, и ответ
  содержит строку запуска оператора (оператор её выполняет); подтверждённые
  `/retry` и `/accept` выставляют флаги Live State, которые основной workflow
  потребляет во время работающей миссии.
- **Control flags основного workflow** (оба v13, патч `scripts/patch_main_control_flags.py`):
  - Pause Gate читает Live State перед началом каждой новой задачи (не в середине
    попытки);
  - Stop Check после текущего шага выставляет `workflow_status = stopped` и
    очищает owner;
  - `steer_note` добавляется в `review_feedback` следующей попытки, затем
    очищается (`Clear Consumed Flags`);
  - `accept_requested` OR-ится в условие `Review Passed?` (reason в
    `last_control`);
  - `retry_requested` позволяет одну дополнительную попытку сверх
    `max_attempts_per_task`;
  - `Clear Consumed Flags` очищает `steer_note`, `accept_requested`,
    `retry_requested` перед следующей задачей.

Команды по категориям:

| Категория | Команды |
|---|---|
| view (read-only) | `/status [mission]`, `/missions`, `/mission <id>`, `/progress [mission]`, `/agents` (алиас `/tasks`), `/usage [reset]`, `/insights [days]`, `/errors [N]`, `/config`, `/profile`, `/version`, `/whoami`, `/audit [N]` |
| control (нужно подтверждение) | `/start <mission>`, `/resume [mission]`, `/stop`, `/restart`, `/pause [reason \| off]`, `/retry`, `/accept [reason]`, `/steer <text>`, `/queue [add \| list \| rm \| move \| clear]` |
| edit (нужно подтверждение) | `/set <key> <value>`, `/model <role> <model> [--provider name]`, `/mission <id> edit <field> …`, `/undo [N]` |
| confirm | `/approve [session \| always]`, `/deny [all] [reason]` |
| help | `/help [<filter>]`, `/commands [page]`, `/start_greet` |

Токен бота хранится только в n8n-credential `telegramApi`, никогда в
workflow-файле. Если Hermes gateway сам ведёт Telegram-бота, тому нужен
собственный токен: один токен — один потребитель `getUpdates`.

---

## 3. Обновлённые файлы

- **`README.md`** — секция "The Telegram control bot": long-polling (60 s,
  getUpdates timeout 25 s, ~1440 executions/day), polling fix (флаг
  `poll_in_progress`), allow-list, admin/viewer, подтверждение, insert-based
  audit, honest queue replies, control flags основного workflow, команды по
  категориям, two-bot/two-token. Убрана дублирующаяся секция "Operational
  details that are easy to miss".
- **`CHANGELOG.md`** — раздел 2026-10-11 "Telegram control bot": bot writes
  (confirm, insert-based audit), confirmation codes (per-chat, TTL, one-time),
  insert-based audit (7 колонок, re-read verification), honest queue replies
  (`/start`/`/resume`/`/restart` → `AHAWR Command Queue`, launch line; `/retry`
  и `/accept` → флаги Live State), polling fix (`poll_in_progress`),
  main-workflow control flags (pause gate, stop check, steer, accept, retry,
  Clear Consumed Flags), 243 passed, 0 failed, README-секция, `.env.example`
  (4 переменные), `hermes_config.example.csv` (4 колонки).
- **`docs/mission-report-T013-ru.md`** — этот отчёт; переписан на русском,
  только о Telegram-части.
- **`check_workflows_output.txt`** — удалён из корня репозитория (не треккался git).

`.env.example` и `hermes_config.example.csv` не требуют новых полей — четыре
опциональных `TELEGRAM_STATUS_BOT_*` и четыре опциональные
`telegram_status_bot_*` колонки уже задокументированы в предыдущем входе;
секреты не добавляются.

---

## 4. Проверки

- ✅ `git ls-files --error-unmatch check_workflows_output.txt` → ошибка (не
  треккался git).
- ✅ Отчёт содержит только Telegram-часть: поиск-инструмент, слой контекста
  и права Reviewer не упоминаются.
- ✅ `check_workflows_output.txt` удалён из корня.
- ✅ CHANGELOG (раздел 2026-10-11) перечисляет: bot writes, confirmation codes,
  insert-based audit, polling fix, main-workflow control flags, honest queue
  replies.
