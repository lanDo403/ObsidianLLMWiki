Ты работаешь с репозиторием ObsidianLLMWIKI:

https://github.com/lanDo403/ObsidianLLMWIKI

Это уже форкнутый проект, нужно улучшить существующий проект, а не переписывать его с нуля.

## Главная цель

Превратить ObsidianLLMWIKI в эффективную долговременную knowledge memory для Codex и других coding-агентов.

Основные приоритеты в порядке важности:

1. минимальное потребление LLM-токенов;
2. минимальное количество LLM-вызовов;
3. быстрый локальный retrieval;
4. высокая релевантность retrieved context;
5. отсутствие дублирования одной и той же информации в контексте;
6. сохранение provenance и freshness внешней документации;
7. обратная совместимость с существующими командами ObsidianLLMWIKI;
8. простота архитектуры.

Не добавляй сложность ради сложности.

Архитектура должна следовать принципу:

`cheap deterministic operation -> targeted retrieval -> optional semantic fallback -> web/external source -> LLM`

а НЕ:

`LLM -> retrieval -> LLM reranker -> LLM query expansion -> LLM`

---

# 1. Сначала изучи существующую реализацию

Перед внесением изменений внимательно изучи:

- `AGENTS.md`
- `SKILL.md`
- `README.md`
- `config.example.toml`
- `scripts/memory_index.py`
- `scripts/vault_writer.py`
- `scripts/wiki_ingest.py`
- `scripts/wiki_compile.py`
- `scripts/wiki_update.py`
- `scripts/wiki_lint.py`
- `scripts/wiki_models.py`, если существует
- `rules/wiki_schema.md`
- `rules/wiki_compile.md`
- `rules/wiki_update.md`
- текущие tests

Не делай предположений о реализации, пока не прочитал соответствующий код.

Сохрани существующие архитектурные гарантии проекта.

Особенно:

- `raw/` остаётся immutable;
- `vault_writer.py` остаётся единой контролируемой точкой записи generated content;
- существующий `WIKI_LINKS_LOST` guard нельзя обходить;
- существующие команды CLI должны продолжать работать;
- FTS index остаётся rebuildable и хранится вне пользовательского Markdown-контента;
- `config.toml` пользователя нельзя перезаписывать;
- существующие vault runtime state и user content нельзя удалять;
- базовая установка должна оставаться максимально лёгкой.

Перед изменениями запусти существующие тесты и зафиксируй baseline.

---

# 2. Введи cheap-first retrieval

Основной runtime retrieval должен работать каскадно:

```text
USER / AGENT QUERY
        |
        v
deterministic FTS5 search
        |
        +--> confident result --> targeted block read --> DONE
        |
        v
optional semantic fallback
        |
        +--> result --> targeted block read --> DONE
        |
        v
external docs / web
```

FTS5 должен оставаться основным поисковым механизмом.

Не используй LLM для:

- query expansion;
- переформулировки поискового запроса;
- reranking результатов;
- определения BM25 relevance;
- составления context pack;
- проверки freshness;
- определения изменений документа;
- стандартного поиска по памяти.

Все эти операции должны быть deterministic/local.

---

# 3. Переведи основной индекс с page-level на block-level

Сейчас поиск ориентирован в основном на Markdown notes.

Переделай индекс так, чтобы единицей retrieval был логический Markdown section/block.

Пример:

```markdown
# Bybit WebSocket

## Authentication
...

## Connection
...

## Heartbeat
...

## Reconnect
...

## Orderbook
...
```

Запрос:

`bybit websocket heartbeat`

не должен заставлять агента читать всю страницу.

Он должен получить только:

`Bybit WebSocket -> Heartbeat`

Разбивай Markdown прежде всего по headings.

Сохраняй heading hierarchy:

```text
# Page
## WebSocket
### Reconnect
```

Для слишком длинных sections разрешается дополнительное deterministic разбиение по абзацам.

Не разрывай без необходимости:

- fenced code blocks;
- таблицы;
- списки;
- связанные короткие абзацы.

Ориентир для обычного retrieval block:

примерно 200–600 слов.

Очень большие sections можно делить на несколько subblocks.

Не используй LLM для chunking Markdown, если структура документа позволяет сделать это детерминированно.

---

# 4. Расширь структуру memory index

Для каждого indexed block храни как минимум:

```text
block_key
path
page_title
heading_path
content
snippet/searchable_content
folder
knowledge_tier
tags
aliases
mtime
content_hash
estimated_tokens
```

Если metadata существует, также поддержи:

```text
source_ids
version
last_verified
freshness
```

`block_key` должен позволять выполнить targeted read найденного блока.

Индекс является производным состоянием и должен полностью rebuild-иться из vault.

Не превращай SQLite в source of truth.

Source of truth остаётся Markdown/Obsidian.

---

# 5. Введи knowledge tiers

Разные части vault не должны иметь одинаковый retrieval priority.

Минимально нужны следующие категории:

```text
canonical_wiki
atomic_notes
raw_sources
system
```

Логика обычного поиска:

```text
canonical_wiki = highest priority
atomic_notes   = fallback
raw_sources    = excluded by default
system         = excluded
```

Причина: одна и та же информация может одновременно существовать как raw source, atomic note и compiled Wiki page.

Codex не должен получать три почти одинаковые версии знания.

По умолчанию:

1. ищи canonical Wiki;
2. если хорошего результата нет — atomic notes;
3. raw открывай только при необходимости проверки первоисточника, provenance, deep research или при явном запросе;
4. System metadata не должно попадать в обычный knowledge context.

Добавь scope, например:

```bash
memory_index.py search "query" --scope auto
memory_index.py search "query" --scope wiki
memory_index.py search "query" --scope notes
memory_index.py search "query" --scope raw
memory_index.py search "query" --scope all
```

`auto` должен быть default.

Не ломай существующие `--folder`, `--tag`, `--prefix`, `--raw`, `--limit`.

---

# 6. Сделай search максимально дешёвым

Команда:

```bash
python3 scripts/memory_index.py search "query" --json
```

по умолчанию НЕ должна возвращать целые страницы или большие blocks.

Возвращай компактную информацию:

```json
{
  "block_key": "...",
  "page": "Bybit Orderbook Synchronization",
  "heading": "Reconnect",
  "score": 14.7,
  "tier": "canonical_wiki",
  "snippet": "...wait for a new snapshot after reconnect...",
  "estimated_tokens": 240,
  "version": "V5",
  "freshness": "fresh"
}
```

Цель:

`search metadata first -> content only if needed`

Количество текста в search result должно быть небольшим.

Добавь отдельный targeted read:

```bash
python3 scripts/memory_index.py read "<block_key>"
```

Он должен вернуть только конкретный block и минимально необходимую metadata.

---

# 7. Реализуй context pack с жёстким token budget

Добавь команду:

```bash
python3 scripts/memory_index.py context "query" --budget-tokens 1200
```

или отдельный скрипт, если архитектурно это чище.

Предпочтительно сохранить всё рядом с `memory_index.py`, если это не превращает файл в монолит.

Context builder должен:

1. выполнить cheap FTS retrieval;
2. выбрать лучшие blocks;
3. удалить obvious duplicates;
4. предпочесть canonical Wiki atomic notes;
5. соблюдать общий token budget;
6. не читать лишние страницы;
7. не использовать LLM;
8. вернуть компактный готовый evidence pack.

Пример:

```text
QUERY: Bybit orderbook reconnect

SOURCE 1
Page: Bybit Orderbook Synchronization
Section: Reconnect
Tier: canonical_wiki
~260 tokens

...

SOURCE 2
Page: WebSocket Reliability
Section: Snapshot Recovery
Tier: canonical_wiki
~310 tokens

...

TOTAL ESTIMATED TOKENS: 570 / 1200
```

Token budget является HARD LIMIT.

Не превышай его, кроме минимальной технической погрешности estimator.

Не добавляй обязательную зависимость только ради token counting.

Если в проекте нет tokenizer dependency, используй дешёвую deterministic approximation и явно документируй её.

---

# 8. Ranking должен оставаться deterministic

Используй FTS5/BM25 как основу.

Разрешены deterministic boosts, например:

```text
canonical wiki boost
exact title boost
alias match boost
heading match boost
tag match boost
version match boost
freshness boost
```

Не используй LLM reranker.

Не делай сложную ML-ranking систему.

Ranking должен быть:

- быстрым;
- объяснимым;
- тестируемым;
- воспроизводимым.

В JSON result желательно уметь показать основные причины ranking:

```json
{
  "bm25": 8.4,
  "tier_boost": 1.5,
  "title_match": true
}
```

Не обязательно возвращать это в обычном human output — можно через `--debug`.

---

# 9. Semantic search только как OPTIONAL fallback

Semantic search потенциально полезен для запросов вроде:

`что делать если стакан после переподключения стал неправильным`

когда Wiki содержит:

`Orderbook Desynchronization After WebSocket Reconnect`.

Но semantic search НЕ должен становиться обязательным dependency.

Требования:

- default installation работает без embeddings;
- основной path остаётся FTS5;
- никаких remote embedding API;
- никаких LLM tokens;
- semantic search вызывается только при низком качестве lexical retrieval;
- feature должна полностью отключаться config-ом.

Добавь архитектурный extension point, например:

```toml
[memory.semantic]
enabled = false
provider = "none"
```

Если реализация локальных embeddings требует тяжёлой новой dependency, не добавляй её в базовую установку.

Можно реализовать provider interface и оставить semantic backend optional extra.

Главная задача текущего fork — хороший cheap lexical/block retrieval, а не создание тяжёлого vector RAG.

---

# 10. Добавь aliases без LLM query expansion

Поддерживай YAML frontmatter:

```yaml
aliases:
  - order book
  - orderbook
  - market depth
  - depth stream
```

Aliases должны индексироваться FTS.

Tags тоже должны участвовать в retrieval.

Таким образом запросы с разной терминологией могут находить страницу без LLM query expansion.

---

# 11. Раздели проектные знания и глобальную Wiki

Обнови `AGENTS.md` так, чтобы Codex понимал source priority.

Для coding tasks:

```text
CURRENT REPOSITORY
    ->
OBSIDIAN MEMORY
    ->
EXTERNAL/OFFICIAL DOCS
```

Но routing зависит от типа вопроса.

Если вопрос относится к текущему коду:

`Где рассчитывается position size?`

сначала читать:

- source code;
- `AGENTS.md`;
- `ARCHITECTURE.md`;
- ADR;
- tests;
- dependency/config files.

Не выполнять бессмысленный Wiki search для каждой локальной code operation.

Если вопрос относится к общему/внешнему знанию:

`Как Bybit синхронизирует WebSocket orderbook?`

использовать:

`memory context/search -> external official docs if missing/stale`

Если вопрос явно time-sensitive:

`Какой rate limit у Bybit сейчас?`

проверять freshness и при необходимости идти в официальный внешний источник.

Добавь этот routing в Agent Contract.

---

# 12. Добавь Source Registry

Нужно отслеживать происхождение и актуальность внешней документации.

Добавь lightweight source registry.

Выбери архитектуру, которая лучше соответствует существующему проекту после изучения кода.

Для каждого external source нужны примерно такие поля:

```text
source_id
name
url
type
provider
version
last_checked
last_changed
content_hash
etag
last_modified
check_interval
status
```

Пример:

```yaml
source_id: bybit-v5
name: Bybit V5 API
url: https://...
type: official_docs
provider: Bybit
version: V5
last_checked: 2026-09-22
last_changed: 2026-09-18
content_hash: ...
etag: ...
last_modified: ...
check_interval: 1d
status: fresh
```

Registry metadata не должна загрязнять обычный memory retrieval.

---

# 13. Добавь provenance в Wiki pages

Compiled knowledge должно по возможности знать, откуда оно появилось.

Расширь Wiki frontmatter аккуратно, сохраняя backward compatibility.

Предпочтительные поля:

```yaml
sources:
  - bybit-v5

applies_to:
  - Bybit API V5

last_verified: 2026-09-22
```

Не требуй этих полей для старых страниц, если это сломает существующие vault.

Migration должна быть backward compatible.

Если metadata отсутствует:

```text
version = unknown
freshness = unknown
```

а не ошибка.

---

# 14. Реализуй дешёвую проверку обновлений документации

Добавь source watcher.

Главное правило:

если документация не изменилась:

`LLM calls = 0`

Алгоритм:

```text
HTTP request
    ->
ETag / Last-Modified / hash
    ->
unchanged?
    -> STOP
```

При возможности используй conditional requests:

```text
If-None-Match
If-Modified-Since
```

Если сервер этого не поддерживает:

```text
download
-> deterministic content hash
```

Только если content изменился:

```text
old
+
new
->
deterministic diff
->
changed sections
```

И только changed content может далее попасть в LLM merge pipeline.

Не отправляй 100 000 токенов документации в LLM, если изменился один section.

Watcher должен уметь работать отдельно от LLM.

Например:

```bash
python3 scripts/source_watcher.py check
python3 scripts/source_watcher.py check bybit-v5
python3 scripts/source_watcher.py status
```

По умолчанию никаких автоматических destructive updates.

---

# 15. Version-aware knowledge

Документация может быть свежей, но неправильной для конкретного проекта.

Пример:

```text
project dependency:
ccxt == 4.x

latest docs:
ccxt 5.x
```

Поэтому memory metadata должна поддерживать:

```text
provider/library
version
applies_to
```

Если агент знает версию dependency из:

- `pyproject.toml`;
- `requirements.txt`;
- `package.json`;
- lock file;
- config;

он должен иметь возможность передать version filter retrieval.

Например:

```bash
memory_index.py search "ccxt create order" --version "4"
```

Не пытайся автоматически парсить все package managers мира на первом этапе.

Сначала создай чистый retrieval/filter contract.

---

# 16. Раздели ingestion для знаний и технической документации

Существующая atomization полезна для:

- статей;
- исследований;
- лекций;
- заметок;
- транскриптов.

Но API documentation часто уже хорошо структурирована headings.

Для technical docs не нужно обязательно пропускать весь текст через LLM atomization.

Добавь/спроектируй два режима:

```text
knowledge
documentation
```

`knowledge`:

```text
parse
-> LLM atomize/enrich
-> Wiki compilation
```

`documentation`:

```text
parse headings deterministically
-> preserve source structure
-> index blocks
-> compile only durable/high-value concepts when explicitly requested
```

Не создавай отдельную canonical Wiki page для каждого REST endpoint.

Подробная API reference может оставаться source/evidence layer.

Canonical Wiki должна содержать полезные долговременные знания:

```text
Authentication
Rate Limits
Order Lifecycle
Orderbook Synchronization
Error Handling
Retry Semantics
WebSocket Reconnect
```

а не копию всей API документации.

---

# 17. Knowledge promotion после research

Добавь в Agent Contract правило:

если Codex не нашёл знание в Wiki, исследовал официальный источник и решил задачу, агент может предложить/выполнить promotion reusable knowledge в Wiki.

Но НЕ записывай автоматически всё найденное в интернете.

Сохранять стоит только знание, которое одновременно:

```text
durable
reusable
verified
```

Например:

`После reconnect Bybit требует восстановить состояние orderbook через новый snapshot`

может быть хорошим Wiki knowledge.

А:

`Bybit был недоступен 10 минут сегодня`

обычно не нужно добавлять в canonical Wiki.

Не запускай LLM compilation после каждой мелкой web operation.

---

# 18. Raw source не должен участвовать в normal recall

Это обязательное изменение.

Обычный вопрос:

`Что мы знаем про Bybit orderbook?`

НЕ должен возвращать одновременно:

```text
raw documentation
atomic note
compiled Wiki page
```

Default context должен предпочитать compiled knowledge.

Raw source открывается только если:

- canonical knowledge отсутствует;
- нужно проверить цитату/деталь;
- нужно проверить provenance;
- пользователь просит первоисточник;
- агент выполняет Wiki recompilation/update;
- нужен deep evidence lookup.

---

# 19. Не превращай Obsidian graph в обязательный retrieval mechanism

Сохрани `[[wikilinks]]`.

Они полезны для:

- навигации человека;
- knowledge organization;
- optional context expansion.

Но не нужно автоматически обходить graph на каждый запрос.

Допустимый pipeline:

```text
search
-> relevant block
-> answer
```

Если информации недостаточно:

```text
relevant block
-> inspect 1–2 strongly related wikilinks
```

Не делай recursive graph traversal по умолчанию.

---

# 20. Минимизируй стартовый контекст Codex

`AGENTS.md` не должен превращаться в огромную энциклопедию.

В always-loaded agent instructions оставляй только:

- routing rules;
- основные команды;
- критические invariants;
- memory protocol;
- правила записи;
- ссылки на детальную документацию.

Подробные инструкции должны жить в отдельных `rules/*.md` или docs и читаться только когда агент реально выполняет соответствующий workflow.

Цель:

не платить context tokens за инструкции, которые не относятся к текущей задаче.

---

# 21. Предпочтительный runtime workflow Codex

После изменений agent workflow должен выглядеть так:

```text
USER TASK
    |
    v
classify task
    |
    +-- code-local ----------------------+
    |                                    |
    | read repo files/code/tests         |
    |                                    |
    +-- external/domain knowledge -------+
             |
             v
        FTS metadata search
             |
       confident match?
          /       \
        yes        no
         |          |
   targeted read   optional semantic
         |          |
         |       still missing?
         |          |
         |      official docs/web
         |          |
         +----------+
             |
             v
        perform task
             |
             v
        tests/validation
             |
             v
      reusable new knowledge?
          /       \
        no         yes
        |           |
       END      Wiki promotion
```

---

# 22. Performance requirements

Основной search path должен:

- работать локально;
- не вызывать LLM;
- не требовать network;
- не читать целые Markdown files после построения index;
- возвращать только компактные snippets/metadata;
- позволять targeted block read;
- иметь ограниченный context budget.

Избегай N+1 чтения множества файлов.

По возможности один SQLite query должен дать metadata для ranking.

Не оптимизируй преждевременно микросекунды, но избегай очевидно дорогих архитектурных решений.

---

# 23. Backward compatibility

Старые команды должны продолжать работать, включая существующий:

```bash
python3 scripts/memory_index.py search "<query>" --json
```

Если меняется JSON schema, сохрани старые ключи либо обеспечь compatibility mode.

Не ломай existing vault.

Добавляй новые config options с безопасными defaults.

Существующий пользователь после `git pull` + migration должен получить рабочую систему без ручной переделки всех заметок.

---

# 24. Migration

Обнови `migrate.py`, если это необходимо.

Migration должна:

- быть идемпотентной;
- не удалять пользовательские данные;
- не изменять content notes без необходимости;
- перестраивать derived index при изменении schema;
- добавлять новые config defaults только если они отсутствуют.

Если старый page-level index несовместим с block-level schema:

безопасно пересоздай derived index.

Не мигрируй его построчно.

---

# 25. Tests

Добавь tests как минимум для следующих случаев:

```text
block-level parsing
heading hierarchy
long-section splitting
code block preservation

canonical Wiki > atomic notes ranking
raw excluded by default
raw available explicitly

aliases searchable
tags searchable

search result contains compact snippet
targeted read returns correct block

context pack respects hard token budget
context pack does not duplicate same content
context pack prefers canonical knowledge

index rebuild works
incremental update works
deleted/renamed note disappears from index

old CLI search remains compatible

source registry parsing
same source hash -> no update
changed source hash -> change detected
ETag/Last-Modified paths where practical

missing freshness metadata remains backward compatible

wiki link guard still works
vault_writer invariants still work
```

Не удаляй существующие regression tests.

---

# 26. Добавь маленький retrieval benchmark

Создай простой reproducible benchmark без LLM.

Например fixture vault с:

```text
raw version of knowledge
atomic note version
canonical Wiki version
unrelated notes
```

Проверяй:

- relevant block попадает в top results;
- canonical version выше duplicate raw/notes;
- raw не попадает в default result;
- context pack не превышает budget;
- количество returned text существенно меньше чтения полной страницы.

Не называй benchmark доказательством общей RAG performance.

Он нужен как regression benchmark для нашего retrieval pipeline.

---

# 27. Documentation

После реализации обнови:

```text
README.md
AGENTS.md
SKILL.md
config.example.toml
```

и необходимые docs/rules.

Обязательно опиши:

```text
cheap-first retrieval
knowledge tiers
block search
targeted read
context pack
raw source behavior
freshness
source watcher
version filters
optional semantic fallback
```

Добавь реальные CLI examples.

---

# 28. Не делать

Не добавляй без убедительной необходимости:

- обязательный vector database;
- обязательные embeddings;
- remote embedding API;
- LLM reranker;
- LLM query expansion;
- LLM classification на каждый search;
- LLM summarization на каждый retrieval;
- автоматический recursive graph traversal;
- automatic web search на каждый вопрос;
- recompilation всей Wiki после каждого изменения;
- обработку неизменившейся документации через LLM;
- большие third-party frameworks ради нескольких простых функций;
- отдельный сервер, если задача решается локальным CLI;
- новую database как source of truth вместо Markdown.

Не оптимизируй архитектуру под красивую AI/RAG терминологию.

Оптимизируй её под:

```text
меньше context
меньше LLM calls
меньше duplicated knowledge
быстрее retrieval
легче debugging
```

---

# 29. Предпочтительная конфигурация

После изучения существующего config аккуратно добавь эквивалент следующих настроек, сохраняя стиль проекта:

```toml
[memory]
auto_update = true
default_scope = "auto"
search_limit = 8
context_budget_tokens = 1200

[memory.ranking]
prefer_wiki = true
fallback_to_notes = true
include_raw_by_default = false

[memory.semantic]
enabled = false
provider = "none"

[sources]
enabled = true
```

Не копируй этот schema слепо, если существующий config организован иначе.

Интегрируй настройки в текущую архитектуру проекта.

---

# 30. Порядок реализации

Работай по этапам.

Phase 1:
изучи код, tests и текущий index schema.

Phase 2:
реализуй block-level FTS index и backward-compatible search.

Phase 3:
реализуй knowledge tiers, scope и ranking.

Phase 4:
реализуй targeted `read`.

Phase 5:
реализуй deterministic `context` с token budget.

Phase 6:
обнови Agent Contract и routing repo/wiki/external docs.

Phase 7:
добавь Source Registry + provenance + freshness metadata.

Phase 8:
добавь deterministic source watcher/hash/diff foundation.

Phase 9:
добавь version filtering.

Phase 10:
добавь documentation ingestion mode, если его можно сделать без разрушения существующего pipeline.

Phase 11:
добавь optional semantic provider interface, но не превращай его в required dependency.

Phase 12:
tests, benchmark, migration, documentation.

После каждого крупного этапа запускай релевантные tests.

---

# 31. Критерии готовности

Работа считается законченной, когда можно выполнить сценарий:

```bash
python3 scripts/memory_index.py search \
  "bybit orderbook reconnect" \
  --json
```

и получить несколько компактных block-level результатов вместо целых документов.

Затем:

```bash
python3 scripts/memory_index.py read "<block_key>"
```

возвращает только нужный section.

А:

```bash
python3 scripts/memory_index.py context \
  "bybit orderbook reconnect" \
  --budget-tokens 1200
```

создаёт небольшой evidence/context pack, который не превышает budget и предпочитает canonical Wiki.

При этом:

```text
raw source
atomic note
canonical Wiki
```

не должны одновременно засорять default context одним и тем же знанием.

Source watcher при неизменившейся документации должен заканчивать работу без LLM processing.

Существующие Wiki compile/update workflows должны продолжить работать.

Существующие tests плюс новые tests должны проходить.

---

# 32. Что предоставить после реализации

После выполнения не ограничивайся фразой «готово».

Дай:

1. краткое описание новой архитектуры;
2. список изменённых/добавленных файлов;
3. какие существующие interfaces сохранены;
4. новые CLI-команды и примеры;
5. результаты tests;
6. результаты retrieval benchmark;
7. потенциальные ограничения;
8. что оставлено как optional/future improvement;
9. пример полного Codex workflow:
   `repo -> memory search -> context -> official docs fallback -> wiki update`.

Если во время реализации выяснится, что какая-то предложенная здесь деталь конфликтует с реальной архитектурой ObsidianLLMWIKI, не ломай проект ради буквального выполнения инструкции.

Сохрани цель и invariants, а конкретную реализацию адаптируй к существующему коду.

Основной критерий любого архитектурного решения:

> Помогает ли оно агенту получить нужное проверенное знание, передав при этом как можно меньше ненужного текста в LLM context?

Если нет — не добавляй его.
