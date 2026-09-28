<div align="center">

# ObsidianLLMWIKI

**Полное управление NotebookLM из Claude Code / Codex, импорт .docx с Zettelkasten-атомизацией и скомпилированный LLM Wiki-слой, который растёт через явный merge, плюс FTS5-полнотекстовая память по всему vault без зависимостей — всё программно, всё в ваш Obsidian vault.**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)
![Codex](https://img.shields.io/badge/Codex-AGENTS.md-green)
![NotebookLM](https://img.shields.io/badge/NotebookLM-API%20Control-orange)
![LLM Wiki](https://img.shields.io/badge/LLM%20Wiki-Compiled%20Knowledge-teal)
![FTS5 Memory](https://img.shields.io/badge/FTS5-Vault%20Memory-9cf)

---

🇬🇧 **[Read this in English → README.en.md](README.en.md)**

</div>

## Локальная память для coding-агентов

Этот форк сохраняет существующие workflows и добавляет cheap-first retrieval:
`repository → memory → official docs`. Локальный код сначала ищется в репозитории;
внешние знания — в памяти. FTS, chunking, ranking, context и freshness не вызывают LLM.

```bash
# После настройки vault_path в config.toml:
python scripts/migrate.py
python scripts/memory_index.py status
python scripts/memory_index.py search "bybit orderbook reconnect" --json
python scripts/memory_index.py read "<block_key>"
python scripts/memory_index.py context "bybit orderbook reconnect" --budget-tokens 1200
python scripts/memory_index.py search "ccxt create order" --version 4 --json
```

Search возвращает компактные snippets/metadata блоков и сохраняет прежние JSON-ключи.
Read получает одну секцию из SQLite. Context удаляет точные дубликаты и ограничивает
**весь вывод** бюджетом по estimator `ceil(UTF-8 bytes / 3)`; это не точный tokenizer
конкретной модели. Большие неделимые блоки пропускаются. После внешних правок заметок
выполните `memory_index.py update`. Индекс остаётся вне vault и полностью rebuildable.

| Scope | Recall |
|---|---|
| auto — default | canonical Wiki; atomic notes при отсутствии Wiki-совпадения |
| wiki / notes | только выбранный слой |
| raw | исходники и source notes |
| all | Wiki, notes и raw с приоритетом Wiki |

System metadata исключены. `--raw` всё ещё означает FTS-синтаксис; исходники требуют
`--scope raw/all`. Сохранены --folder, --tag, --prefix, --limit. Aliases/tags участвуют
в поиске, --debug показывает ranking. Версия 4 соответствует 4.x/4.2, но не 40/5/unknown.
Подробности: [retrieval contract](rules/memory_retrieval.md).

Источники задаются в `<vault>/System/sources.yaml`, формат —
[source registry](rules/source_registry.md). Optional frontmatter: sources,
provider/library, version, applies_to, last_verified. Старые страницы без metadata
работают, version/freshness остаются unknown.

```bash
python scripts/source_watcher.py status --json
python scripts/source_watcher.py check
python scripts/source_watcher.py check example-api-v5 --force --json
python scripts/wiki_ingest.py demo downloaded-api.md --mode documentation --source-id example-api-v5 --version V5
python scripts/memory_index.py search "authentication" --scope raw --json
```

Watcher проверяет ETag/Last-Modified/hash без LLM и автоматических обновлений Wiki.
Изменения дают diff и changed sections. Проверка источника не подтверждает Wiki prose:
изменённые/просроченные источники делают ранее проверенную страницу stale.
Knowledge сохраняет прежний ingestion/compile pipeline. Documentation сохраняет
headings в immutable raw и не запускает compile; promotion только проверенных,
повторно используемых концепций выполняется явно через wiki_update.py.

Optional semantic fallback — локальный provider interface, отключённый по умолчанию
(`enabled=false, provider="none"`). Backend/embeddings не устанавливаются, remote API
и LLM reranking не используются.

Проверки: `python -m pytest -q` и `python benchmarks/retrieval_benchmark.py`.
Реальные тесты в Linux и через Claude не проводились.
Benchmark на синтетическом vault — регрессия pipeline, не общая оценка RAG.
[Результаты](docs/VALIDATION.md), [фазы](TASKS.md), [решения](DECISIONS.md),
[исходный план](docs/MEMORY_PLAN.md).

---

## Что это

ObsidianLLMWIKI превращает Claude Code и Codex в полноценный пульт управления NotebookLM и вашим Obsidian vault. Запускайте deep research, управляйте источниками, вытаскивайте заметки из нотбуков — всё через одну команду на естественном языке. Параллельно импортирует `.docx` из Google Drive и атомизирует их в Zettelkasten-заметки с MOC, тегами и вики-ссылками.

И поверх всего — изолированный **LLM Wiki**-слой: скомпилированная база знаний по Карпати, которая накапливается через явный merge, а не пересчитывается на каждый запрос. Поиск по всему этому — **память FTS5**: локальный полнотекстовый индекс всего vault (заметки, вики, NotebookLM-импорт), обновляется сам после каждой записи.

### Что можно делать с NotebookLM

| Возможность | Команда |
|---|---|
| Запустить deep/fast research в нотбук | `research_notebook.py run <id> "<запрос>"` |
| Безопасный one-shot импорт источников (без дубликатов) | то же, обходит [баг upstream CLI](https://github.com/teng-lin/notebooklm-py/issues/241) |
| Почистить дубли и ошибочные источники | `research_notebook.py dedupe <id>` |
| Извлечь все заметки → атомарные заметки в Obsidian | `process_notebook.py <id>` |
| Извлечь заметки + исходники + mind map | `process_notebook.py <id> --include-sources --include-mindmap` |
| Скачать заметки без атомизации | `fetch_notebook.py <id>` |

Вся работа с NotebookLM идёт через Python API (`notebooklm-py`), а не через CLI — один вызов, без retry-дупликации. Авторизация — файл на диске, без интерактивного браузера при каждом запуске.

### Что ещё умеет

- Импорт `.docx` из Google Drive → атомарные заметки + MOC в vault
- Обогащение и атомизация существующих заметок
- Обработка контактов из сетевых заметок → персональные карточки + Networking MOC
- Дедупликация vault по семантическому сходству
- Автоматическая таксономия тегов и вики-ссылки между заметками
- **LLM Wiki** — изолированный compiled-knowledge слой (project / corpus режимы, RU/EN-шаблоны, guard-сохранение `[[вики-ссылок]]` при merge, см. [секцию ниже](#llm-wiki))
- **Память FTS5** — полнотекстовый поиск по всему vault на stdlib SQLite: `memory_index.py search "запрос" --json`, bm25-ранжирование со сниппетами, инкрементальный индекс вне vault

## Установка

```bash
git clone https://github.com/lanDo403/ObsidianLLMWIKI.git
cd ObsidianLLMWIKI
bash install.sh --vault-path "/путь/к/вашему/vault"
```

Или скопируйте этот промпт в Claude Code или Codex — он сделает всё сам:

```
Клонируй https://github.com/lanDo403/ObsidianLLMWIKI.git и запусти bash install.sh --vault-path "/путь/к/vault" в клонированной директории.
```

Установщик:
- Проверит Python 3.10+ и установит зависимости (`python-docx`, `pyyaml`)
- Создаст `config.toml` с путём к vault
- Зарегистрирует навык глобально в `~/.claude/skills/obsidian-llmwiki/`
- Добавит блок в `~/.claude/CLAUDE.md`

После установки навык работает **из любой директории**.

## Обновление

```bash
cd ObsidianLLMWIKI && git pull && bash install.sh
```

Установщик идемпотентен: симлинки навыка обновятся сами, `migrate.py` допишет
новые секции конфига (например `[memory]`) и развернёт FTS5-индекс памяти.
`config.toml` и vault не перезатираются.

### Режимы установки

| Режим | Флаг | Что делает |
|-------|------|-----------|
| **claude** (по умолчанию) | `--mode claude` | Зависимости + config + глобальный навык в `~/.claude/` |
| **codex** | `--mode codex` | Зависимости + config + проверка `AGENTS.md` |
| **local** | `--mode local` | Только зависимости + config |

## Как использовать

После установки просто говорите Claude Code что нужно:

```
Обработай документ "Архитектура второго мозга.docx"
```

```
Обработай заметку "Мои мысли о продуктивности"
```

```
Скачай мои файлы с Google Drive и разбей на атомарные заметки
```

```
Проверь настройку — запусти doctor
```

Ещё примеры:

| Что сказать | Что произойдёт |
|-------------|---------------|
| `process МойДокумент.docx` | Полный цикл: скачать → разобрать → атомизировать → записать в vault |
| `process МойДокумент.docx --non-interactive --on-conflict skip` | То же, без вопросов (для автоматизации) |
| `обработай заметку "Название"` | Enrich или atomize существующей заметки |
| `обработай контакты "Контакты"` | Разбить заметку с контактами → персональные карточки + Networking MOC |
| `process_note "Note" --mode atomize` | Принудительная атомизация заметки |
| `dedup --dry-run` | Показать дубликаты без изменений |
| `запусти ресерч в ноутбуке "<id>" "<запрос>"` | Deep research в NotebookLM через API |
| `почисти дубли в ноутбуке "<id>"` | Дедупликация источников в нотбуке |
| `создай вики "<slug>"` / `init wiki "<slug>"` | Скелет новой LLM Wiki-space (project/corpus, RU/EN) |
| `залей в вики "<slug>" <путь>` | Положить статью / папку в `raw/<kind>/` |
| `собери вики "<slug>"` | Скомпилировать сырьё в страницы (с guard на `[[wikilinks]]`) |
| `проверь вики "<slug>"` | Lint: frontmatter, ссылки, изоляция контура |
| `найди в заметках "<запрос>"` | FTS5-поиск по всему vault (заметки + вики), bm25 + сниппеты |
| `перестрой индекс памяти` | Полная пересборка FTS5-индекса (`memory_index.py build`) |

## NotebookLM: полное программное управление

ObsidianLLMWIKI даёт Claude Code / Codex полный программный контроль над NotebookLM. Вместо ручной работы в веб-интерфейсе — вы говорите агенту что нужно, и он запускает ресерч, управляет источниками, вытаскивает заметки и атомизирует их в Obsidian. Весь API-слой работает через `notebooklm-py` как библиотеку (не CLI), что гарантирует one-shot поведение без retry-дупликации.

### Первый вход в аккаунт

Сессия Google сохраняется в `~/.notebooklm/storage_state.json` и переиспользуется — вход одноразовый.

1. Создайте venv и установите зависимости:

```bash
python3 -m venv .venv
.venv/bin/python scripts/notebooklm_setup.py --skip-login
```

Скрипт поставит `notebooklm-py[browser]` и браузер Playwright Chromium в активный venv. Флаг `--skip-login` отделяет установку от интерактивного входа — логин делаем вручную в следующем шаге, потому что `notebooklm login` требует настоящий TTY.

> **Arch / Manjaro / Debian:** системный `pip` заблокирован PEP 668, так что venv обязателен. Скрипт автоматически определяет venv и больше не передаёт `--user`. Без venv (на обычный системный Python) установка упадёт с подсказкой создать venv.

2. **Откройте отдельное окно терминала** и выполните:

```bash
cd /путь/к/ObsidianLLMWIKI
.venv/bin/notebooklm login
```

> Не запускайте это через префикс `!` в Claude Code или Codex — у такой сессии нет интерактивного stdin, и `notebooklm login` упадёт с `Aborted!` в момент ожидания `ENTER`.

3. В открывшемся окне Chromium войдите в Google-аккаунт и дождитесь загрузки главной NotebookLM.

4. Вернитесь в терминал и нажмите **ENTER** — `storage_state.json` сохранится.

Проверка: файл `~/.notebooklm/storage_state.json` должен появиться.

### Использование

```bash
.venv/bin/python scripts/process_notebook.py <notebook_id>
```

`<notebook_id>` — последний сегмент URL вашего нотбука: `https://notebooklm.google.com/notebook/<notebook_id>`. Флаги `--include-sources` и `--include-mindmap` добавляют индексированные источники и mind map. Для нескольких аккаунтов используйте `--profile <имя>`.

### Запуск deep research

```bash
.venv/bin/python scripts/research_notebook.py run "<notebook_id>" "<query>"
.venv/bin/python scripts/research_notebook.py run "<notebook_id>" "<query>" --dry-run
```

Опции: `--mode fast|deep` (по умолчанию deep), `--source web|drive`, `--max-sources N`, `--poll-interval` / `--poll-timeout`, `--profile <имя>`, `--dry-run`.

> `research_notebook.py` ходит в `notebooklm-py` как в библиотеку — строго one-shot. Сырой CLI (`notebooklm source add-research --import-all`) при таймауте дуплицирует источники ([upstream-баг #241](https://github.com/teng-lin/notebooklm-py/issues/241)).

Почистить нотбук от дубликатов и error-источников:

```bash
.venv/bin/python scripts/research_notebook.py dedupe "<notebook_id>" --dry-run
.venv/bin/python scripts/research_notebook.py dedupe "<notebook_id>" --include-error --non-interactive
```

`dedupe` группирует источники по URL (fallback — title) и оставляет первую копию; сначала всегда `--dry-run`.

## Что происходит под капотом

**NotebookLM → Obsidian:**
```
NotebookLM API → fetch notes/sources/mindmaps → atomize (Claude) → generate → write → Obsidian vault
```

**Deep Research → NotebookLM:**
```
research_notebook.py → notebooklm-py API (one-shot) → poll → import sources → dedupe
```

**Google Drive → Obsidian:**
```
Google Drive → rclone fetch → parse .docx → atomize (Claude) → generate → write → Obsidian vault
```

**Личные заметки:**
```
Vault note → detect mode → rewrite (Claude) → write back
```

**Память (FTS5):**
```
любая запись в vault → vault_writer → авто-обновление FTS5-индекса (вне vault)
поиск: memory_index.py search "запрос" → bm25 + metadata → targeted read / context с бюджетом
```

- **Enrich** — короткая заметка → добавляет теги, вики-ссылки, расширяет текст (1 → 1)
- **Atomize** — длинная заметка → разбивает на атомарные заметки + MOC (1 → N)
- **Contacts** — заметка с контактами → персональные карточки + Networking MOC (1 → N)

## MOC + Zettelkasten

**MOC (Map of Content)** — навигационный хаб: собирает ссылки на все атомарные заметки из документа. Один MOC на документ.

**Атомарные заметки** — одна идея, 150–600 слов, самодостаточные. Связаны `[[вики-ссылками]]` друг с другом и с MOC.

**Память FTS5** (`scripts/memory_index.py`) — полнотекстовый поиск по всему vault на SQLite FTS5: нулевые зависимости (stdlib), всё локально, индекс живёт вне vault и обновляется автоматически после каждой записи. Поисковый слой для агентов: `python3 scripts/memory_index.py search "запрос" --json`.

**Smart Connections** *(legacy)* — прежний поисковый слой на локальных эмбеддингах. Заменён памятью FTS5: пайплайн его больше не использует и не требует. Плагин можно оставить ради семантических подсказок в UI Obsidian.

## LLM Wiki

Третий слой знаний поверх атомарных заметок — **скомпилированная вики** в стиле Карпати. Не RAG (не пересчитывается при каждом запросе) и не плоский набор заметок (страницы связаны вики-ссылками и имеют типы). Это долгоживущая база, которая **накапливается** через явный merge при новых ингестах: существующие `[[вики-ссылки]]` обязаны сохраняться, иначе compile падает с `WIKI_LINKS_LOST` (exit 5).

Вики живёт в изолированной папке внутри vault — `<vault>/<wiki_folder>/<slug>/` (по умолчанию `LLM Wiki/`). Атомарные заметки никогда не попадают сюда, и `wiki_compile.py` не читает заметки за пределами своей wiki-space.

**Два режима:**

- **project** — фиксированные core-страницы: overview, architecture, components, workflows, goals-and-roadmap, glossary, open-questions. Подходит для документации одной системы.
- **corpus** — только entities/concepts растут по мере ингеста. Подходит для базы знаний по чтению/исследованиям.

**Workflow:**

```bash
# 1. Создать пустую wiki-space (+ опц. --lang ru|en для русских/английских шаблонов)
python3 scripts/wiki_init.py demo --mode project --title "Demo Project"

# 2. Залить сырьё (статьи, доки, транскрипты)
python3 scripts/wiki_ingest.py demo path/to/article.md --kind articles
python3 scripts/wiki_ingest.py demo path/to/notes/ --kind docs

# 3. Скомпилировать (LLM мерджит сырьё в страницы)
python3 scripts/wiki_compile.py demo --since-last-compile

# 4. Проверить целостность
python3 scripts/wiki_lint.py demo --strict

# Инкрементальный апдейт одной страницы из одного нового raw-инпута
python3 scripts/wiki_update.py demo raw/docs/новый-файл.md
```

Все скрипты пишут через единственный writer `vault_writer.py` — атомарные пайплайны и wiki делят одну точку записи.

**Язык шаблонов.** `wiki_init.py` поддерживает `--lang en` и `--lang ru`. Значение по умолчанию берётся из `[wiki].default_lang` в `config.toml` (если не указан — `en`). Влияет только на текст SCHEMA.md, index.md, log.md, raw/_README.md и core-страниц-stub'ов; структура и контракт frontmatter одинаковы для обоих языков. Wiki-space'ы на разных языках сосуществуют в одном vault без конфликтов.

## Шаблоны

Директория `templates/` содержит стартовую структуру vault:

- `Notes/Atomic Note Example.md` — пример атомарной заметки
- `MOCs/Topic Map - MOC.md` — пример MOC

## Конфигурация

Техническое имя проекта — `obsidian-llmwiki`: оно используется для skill и
каталогов cache/staging. Переменная выбора backend — `OBSIDIAN_LLMWIKI_BACKEND`
(`claude` или `codex`). При переходе на новое имя обновите эту переменную и
регистрацию skill. Ранее созданные внешние каталоги не удаляются автоматически;
явно заданные `db_dir`, `state_dir` и `staging_dir` продолжают использоваться.

Файл `config.toml` (создаётся при установке, не коммитится):

```toml
[vault]
vault_path = "/путь/к/вашему/vault"          # обязательно, абсолютный путь
notes_folder = "Research & Insights"          # куда пишутся атомарные заметки
moc_folder = "Guides & Overviews"             # куда пишутся MOC
source_folder = "Sources"                      # ссылки на исходники

[rclone]
remote = "gdrive:"                             # имя rclone remote
staging_dir = "/tmp/obsidian-llmwiki/staging"               # временная директория
```

## Требования

- Python 3.10+ (рекомендуется 3.11+)
- [rclone](https://rclone.org/) с доступом к Google Drive (для импорта `.docx`)
- [Claude Code](https://claude.ai/code) или [Codex](https://github.com/openai/codex)
- `vault_path` в `config.toml` — абсолютный путь к вашему Obsidian vault
- Отдельная СУБД не нужна: SQLite с поддержкой FTS5 встроен в Python (stdlib-модуль `sqlite3`) — на нём работает память FTS5; поддержку проверяет `doctor.py`

**Обязательные плагины Obsidian:**

- [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) — HTTP-интерфейс для чтения/записи vault. Нужен для MCP Obsidian
- [MCP Obsidian](https://github.com/MarkusPfundstein/mcp-obsidian) — MCP-сервер, соединяющий Claude Code с Obsidian через Local REST API. Зависит от Local REST API

**Legacy (больше не требуется):**

- [Smart Connections](https://github.com/brianpetro/obsidian-smart-connections) — прежний слой семантического поиска (локальные эмбеддинги). Заменён памятью FTS5 (`scripts/memory_index.py`); старый конфиг в `templates/.smart-env/` оставлен для существующих установок

**Смежные проекты:**

- [NotebookLM++](https://github.com/howdeploy/notebooklmplusplus) — Chrome-расширение для [NotebookLM](https://notebooklm.google.com), которое добавляет массовый импорт: веб-страницы, YouTube-видео, Shorts, плейлисты, каналы целиком, комментарии и PDF-снимки страниц. Если вы используете NotebookLM как второй мозг наряду с Obsidian — это расширение закрывает ту же задачу на стороне Google: быстро собрать источники в нотбук для AI-анализа

## Структура проекта

```
ObsidianLLMWIKI/
├── scripts/
│   ├── process.py            # Главный пайплайн (.docx → vault)
│   ├── process_note.py       # Обработка личных заметок (enrich/atomize)
│   ├── process_contacts.py   # Контакты → персональные карточки + Networking MOC
│   ├── process_notebook.py   # NotebookLM нотбук → атомарные заметки (полный пайплайн)
│   ├── fetch_notebook.py     # Извлечение заметок из NotebookLM (без атомизации)
│   ├── research_notebook.py  # Deep/fast research + дедупликация источников в NotebookLM
│   ├── notebooklm_setup.py   # Установка notebooklm-py + Playwright (one-shot)
│   ├── fetch_docx.sh         # Скачивание с Google Drive
│   ├── parse_docx.py         # .docx → JSON
│   ├── atomize.py            # JSON → план атомизации (через Claude)
│   ├── generate_notes.py     # План → .md файлы
│   ├── vault_writer.py       # Staging → vault (с дедупликацией)
│   ├── dedup_vault.py        # Поиск и мерж дубликатов
│   ├── scan_vault.py         # Сканирование существующих заметок
│   ├── rewrite_backend.py    # Бэкенд семантической перезаписи (Claude CLI)
│   ├── config.py             # Загрузчик конфигурации
│   ├── doctor.py             # Проверка окружения
│   ├── memory_index.py       # Память FTS5 — индекс/поиск по всему vault (build/update/search)
│   ├── migrate.py            # Идемпотентный апгрейд установки (config + FTS5-индекс)
│   ├── wiki_init.py          # LLM Wiki — создать пустую wiki-space
│   ├── wiki_ingest.py        # LLM Wiki — приём сырья (articles/docs/transcripts/assets)
│   ├── wiki_compile.py       # LLM Wiki — главный пайплайн компиляции
│   ├── wiki_update.py        # LLM Wiki — инкрементальный апдейт одной страницы
│   ├── wiki_lint.py          # LLM Wiki — read-only проверка целостности
│   └── wiki_models.py        # LLM Wiki — ChangeSet / WikiPage / валидация
├── rules/
│   ├── atomization.md        # Правила атомизации
│   ├── taxonomy.md           # Правила таксономии тегов
│   ├── personal_notes.md     # Правила обработки личных заметок
│   ├── contacts.md           # Правила обработки контактов
│   ├── wiki_schema.md        # LLM Wiki — on-disk контракт
│   ├── wiki_compile.md       # LLM Wiki — контракт LLM для compile
│   └── wiki_update.md        # LLM Wiki — семантика инкрементального merge
├── templates/                # Стартовая структура vault (+ templates/wiki/ для LLM Wiki)
├── tests/                    # Регрессионные тесты
├── docs/                     # Документация для агентов
├── AGENTS.md                 # Контракт агента (Claude Code + Codex)
├── SKILL.md                  # Claude-адаптер
├── SKILL_PERSONAL.md         # Промпт для обработки личных заметок
├── SKILL_CONTACTS.md         # Промпт для обработки контактов
├── tags.yaml                 # Каноничный список тегов
├── config.example.toml       # Шаблон конфигурации
├── install.sh                # Установщик с глобальной регистрацией
└── requirements.txt          # Python-зависимости
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
