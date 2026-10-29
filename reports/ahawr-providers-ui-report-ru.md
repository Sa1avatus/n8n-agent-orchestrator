# Отчёт по миссии ahawr-providers-ui

Дата: 2026-10-27. Задачи T028 (факты) и T029 (отчёт) закрыл оператор: после трёх неудачных попыток Worker
на T028 (оценки 35, 10 и отмена на третьей) задача отдана оператору по решению пользователя. Все числа
ниже получены в этой сессии, команды указаны рядом.

## 1. Что сделано

Провайдеры, их модели и ключи настраиваются в веб-интерфейсе, а не в `.env`. Всё за флагами, которые
по умолчанию выключены (`providers_db`, `litellm_sync`); при выключенном флаге поведение прежнее.

| Часть | Файлы |
|---|---|
| Модель и реестр типов API (OpenAI, Anthropic и др.) | `provider_api_types.py`, `provider_templates.json` |
| Реестр провайдеров runner'а (env-блоки плюс база, опрос изменений) | `provider_registry.py` |
| Секреты: файл с правами 0600, атомарная запись, шифрование Fernet при наличии ключа | `provider_secrets.py` |
| Маршруты `/api/board/providers` (список, создание, правка, удаление, модели, проба) | `board_providers_api.py` |
| Синхронизация маршрутов LiteLLM | `provider_litellm.py` |
| Вкладка Providers в доске | `board_providers.js`, `board_providers_form.js`, `board_providers_view.js` |
| Хранилище провайдеров и моделей (память и Postgres) | `board.py`, `board_pg.py`, `docs/board-schema.sql` |
| Настройки `providers_db`, `secrets_file`, `litellm_sync` | `config.py` |
| Документ | `docs/providers.md` |

Решение по секретам: значения ключей не лежат в базе и не возвращаются API, интерфейс показывает только
«задан / не задан». Файл ключей без шифрования, пока не задан `CLAUDE_RUNNER_SECRETS_KEY`; с ключом и пакетом
`cryptography` файл шифруется (`provider_secrets.py`, `_load_fernet`). Зависимость `cryptography` в
`pyproject.toml` не добавлена, поэтому по умолчанию шифрование недоступно.

## 2. Проверки

- `pytest` в контейнере `ahawr-qa` (`tests`, без `e2e`): падают 11 тестов, все не из этой миссии:
  `test_process_group_kill` (3, POSIX), `test_dashboard` (2, в том числе whitelist статики),
  `test_real_queue_board_dry` (2), `test_board_js_static`, `test_guards_api`, `test_qa_stack`
  (`test_seeded_verdicts_match_board_api_glob`), `test_runs`. Тесты провайдеров, host-actions и ролей проходят.
- `node --test tests/*.test.js`: 309 тестов, 309 прошли.
- `ruff` на файлах провайдеров и QA чистый. Остаются 4 замечания не из этой миссии:
  `board_ops_api.py` (SIM108, E501) и `test_board_ops_api.py` (2 раза E501).
- `mypy --python-version 3.12`: в новых модулях провайдеров ошибок нет; ошибки есть в старых файлах
  (`board_pg.py`, `board_order.py`, `board_api.py`, `board_ready.py`, `board_stats.py`) и в тестах.
- QA в контейнере: запрос `db643263126a4f128756d0582895d25f`, 19 из 19 сценариев прошли. Выдуманный ключ
  `sk-qa-marker-…` не найден ни в вердиктах, ни в скриншотах под `/qa-runs`.
- `docker ps -a --filter name=qa-`: только постоянный `qa-postgres` (Up, healthy). Лишних контейнеров нет.
- Хостовый pytest на Windows для хранилища провайдеров даёт ложные падения (одинаковые метки времени из-за
  грубых часов Windows); в контейнере те же тесты проходят.

## 3. Что по дороге исправлено оператором

- QA писал ключ в read-only mount: `qa_stack.py` теперь задаёт `CLAUDE_RUNNER_SECRETS_FILE` во временный файл.
- `qa_runner.py`: DELETE отправлял `expected_version` в теле запроса, маршрут ждёт его в query.
- Два сценария провайдеров (`providers_add_key_marker`, `providers_edit_save`) заканчивались проверкой
  после перезагрузки страницы, которая для окна, открытого кликом, не выполняется; поправлены.
- Правки отслеживаемых файлов миссии несколько раз пропадали из рабочего дерева (причина не найдена, следов
  в журнале git нет). Восстановлено из оставшихся stash-коммитов и результатов слияния; дубли роутов
  `/v1/providers` и монтирования маршрутов в дашборде убраны, `board_api.py` берёт список провайдеров
  из реестра с запасным путём через настройки.

## 4. Что не сделано

- Синхронизация LiteLLM не проверена на настоящем LiteLLM.
- Шифрование ключей по умолчанию недоступно (нет зависимости `cryptography`); решение оператора ещё нужно.
- Кнопка Test не запускалась против платного провайдера.
- `docs/providers.md` (разделы 9, 10, 15) всё ещё написан как проект («none of these stores exists yet»),
  а код уже есть; документ нужно привести в соответствие.
- 11 падающих тестов выше не разобраны в этой миссии.

## 5. Включение (команды не запускались)

1. Применить схему: таблицы `board.providers`, `board.provider_models` из
   `claude-runner/docs/board-schema.sql`.
2. Задать `CLAUDE_RUNNER_PROVIDERS_DB=1` и при желании `CLAUDE_RUNNER_SECRETS_FILE`; для шифрования
   установить `cryptography` и задать `CLAUDE_RUNNER_SECRETS_KEY`.
3. Перенести провайдеров из `.env` через вкладку Providers.
4. `CLAUDE_RUNNER_LITELLM_SYNC=1` только после проверки шагов из раздела 13 `docs/providers.md` на копии стека.

## 6. Версия

Это новая функция вместе с host-actions, очередью из доски и фактами для отчётов: релиз `0.11.0`
(0.10.100 → 0.11.0, средний номер +1, последний 0).
