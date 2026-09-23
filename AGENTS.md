# open-edx — робоча інструкція Codex і opencode

Навчальна платформа шкільних курсів на Open edX: короткі відеолекції з мемами, квізи, AI-репетитор у контексті уроку, батьківський кабінет і підписка.

Читай перед роботою, у цьому порядку:

1. `.specify/memory/constitution.md` — непорушні правила. Пріоритет над усім.
2. `specs/NNN-<фіча>/spec.md` — вимоги поточної фічі.

Ця інструкція деталізує конституцію і не може їй суперечити.

## Почергова робота з Claude Code

Codex, opencode і Claude Code працюють з одним репозиторієм почергово. Джерело
істини — файли під git, а не історія чату конкретного агента. Перед продовженням
чужої роботи перевір `git status`, `git diff`, поточні `spec.md` / `plan.md` /
`tasks.md` і останні коміти. Не перезаписуй незавершені зміни іншого агента.

`AGENTS.md` описує цей процес для Codex і opencode, `CLAUDE.md` — для Claude
Code. Якщо правило процесу змінюється, синхронно онови обидва файли.

## Процес

SDD через spec-kit. Порядок на фічу:

```
$speckit-specify   -> specs/NNN-<фіча>/spec.md      вимоги з ID FR-NNN-MM
$speckit-clarify   -> зняти двозначності                       (за потреби)
$speckit-plan      -> plan.md, data-model.md, contracts/       агент architect
$speckit-tasks     -> tasks.md з рядками T-xxx [FR-NNN-MM]     агент architect
$speckit-implement -> задача за задачею                        агент implementer
```

## Розподіл ролей Codex

Ролі визначені в `.codex/agents/*.toml`. Для відповідної фази делегуй роботу
цьому custom agent. Агентів, які змінюють файли, запускай послідовно; reviewer
запускай після завершення implementer.

| Коли | Агент | Модель |
|---|---|---|
| план, data-model, contracts, декомпозиція на задачі | `architect` | gpt-6-astra / ultra |
| звичайна задача `T-xxx` | `implementer` | gpt-5.5 / medium |
| задача з міткою `critical:`, або після ескалації | `implementer-senior` | gpt-5.6-sol / xhigh |
| ревʼю після кожної задачі і перед мержем фічі | `reviewer` | gpt-5.6-sol / xhigh, read-only |

## Розподіл ролей opencode

Ролі визначені в `.opencode/agents/*.md` (mode: all — і субагенти через
`task`, і окремі прогони `opencode run --agent <роль>`). Для відповідної фази
делегуй роботу цьому агенту. Агентів, які змінюють файли, запускай послідовно;
reviewer запускай після завершення implementer.

Команди spec-kit для opencode — слеш-форми: `/speckit-specify`,
`/speckit-clarify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`
(зареєстровані в `.opencode/commands/`). Решта процесу — та сама, що для Codex.

| Коли | Агент | Модель |
|---|---|---|
| план, data-model, contracts, декомпозиція на задачі | `architect` | `codex/gpt-5.6-sol`; фолбек — `opencode/gpt-5.5`, `opencode/zen-claude-opus-5-5`, `claude/opus`, `claude/sonnet` |
| рутинна задача `T-xxx`: boilerplate, тести, дрібні правки | `implementer` | `opencode/mimo-v2.6-flash-free` |
| складні фічі, багатофайлові зміни, рефакторинги; задача з міткою `critical:` або після ескалації | `implementer-senior` | `opencode/nemotron-3-ultra-free` |
| ревʼю після кожної задачі і перед мержем фічі | `reviewer` | `codex/gpt-5.5`, read-only; фолбек — `opencode/zen-claude-sonnet-5`, `opencode/zen-claude-opus-5-5`, `claude/sonnet`, `claude/opus` |

### Автоматичний failover моделей opencode

Плагін `@razroo/opencode-model-fallback` зареєстрований у `opencode.json` та
встановлюється з кореневого `package.json` / `package-lock.json`. Для агентів
`model` у `.opencode/agents/*.md` — основна модель, а впорядкований
`fallback_models` — автоматичний ланцюг плагіна. Плагін читає ланцюг із
конфігурації агента або з його frontmatter `options`; він переходить до
наступної моделі при підтримуваній помилці провайдера (зокрема rate limit,
quota, недоступна модель і HTTP 429/5xx). Після вичерпання ланцюга помилка
повертається виклику; не перемикайся на незадекларовані чи платні моделі без
явного дозволу.

Поточний ланцюг `implementer` (з `.opencode/agents/implementer.md`):

1. `opencode/mimo-v2.6-flash-free` — основна;
2. `openrouter/nvidia/nemotron-3.5-lightning:free`;
3. `opencode/nemotron-3.5-lightning-free`;
4. `inclusionai/ling-3.0-flash-fin:free`;
5. `opencode/ling-3.0-flash-fin-free`;
6. після вичерпання — зупинити задачу й повідомити про помилку.

Поточний ланцюг `implementer-senior` (з `.opencode/agents/implementer-senior.md`):

1. `opencode/nemotron-3-ultra-free` — основна;
2. `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`;
3. `opencode/muse-spark-1.3-contributor-free`;
4. `opencode/muse-spark-1.2-contributor-free`;
5. `openrouter/qwen/qwen3.8-27b:free`;
6. `opencode/big-pickle`;
7. після вичерпання — зупинити задачу й повідомити про помилку.

`architect` і `reviewer` мають `model:` і `fallback_models:` у своїх конфігах (`.opencode/agents/architect.md`, `.opencode/agents/reviewer.md`). Основна модель — Codex (`codex/gpt-5.6-sol` для architect, `codex/gpt-5.5` для reviewer). Фолбек-ланцюг плагіна для architect: `opencode/gpt-5.5`, `opencode/zen-claude-opus-5-5`, `claude/opus`, `claude/sonnet`. Для reviewer: `opencode/zen-claude-sonnet-5`, `opencode/zen-claude-opus-5-5`, `claude/sonnet`, `claude/opus`. Ці проксі не використовують глобальний `fallback_models` з `opencode.json`.

Контекстом для агента завжди є цей репозиторій `open-edx` і фактичний FR-ID з
поточного `tasks.md`/`spec.md`.

Після змін `opencode.json`, `.opencode/agents/` або плагіна перезапусти opencode,
щоб він завантажив оновлені налаштування.

### Ескалація — автоматична

Переводь задачу з `implementer` на `implementer-senior`, щойно виконано будь-що:

1. дві підряд спроби завершились червоним тестом;
2. субагент повернув `BLOCKED`;
3. `reviewer` двічі підряд повернув `CHANGES_REQUESTED` по тій самій задачі.

Передавай разом із логом попередніх спроб — без нього ескалація вироджується в
третю спробу навмання.

Дописуй у `tasks.md` фічі:

```
escalated: T-034 implementer->implementer-senior, reason=<коротка причина>
```

Не питай дозволу на ескалацію — це передбачений маршрут, а не виняток.

## Цикл однієї задачі

```
тест -> червоний (переконайся, що з правильної причини) -> код -> зелений
     -> python3 scripts/trace.py -> reviewer -> наступна задача
```

Одна задача — один прохід субагента. Не бери дві.

## Маркери трасування

Обовʼязкові, інакше `trace.py` червоний:

```python
# impl: FR-NNN-MM          у шапці файла-імплементації
# verifies: FR-NNN-MM      у тесті
```

Маркери шукаються тільки в коді (`.rs .py .toml .yaml .yml .sql .sh .ipynb`).
У `.md` вони ігноруються навмисно, щоб доки могли наводити FR-ID як приклад.
Файл, що містить FR-ID як дані (фікстура, генератор), додає рядок
`trace: ignore-file` і випадає зі сканування.

## Команди

```bash
python3 scripts/trace.py          # згенерувати docs/traceability.md + перевірити
python3 scripts/trace.py --check  # тільки перевірити
python3 scripts/test_trace.py     # тести самого валідатора
```

Для vault виконай `graphify update .`, потім
`graphify export obsidian --dir ./vault`. Запускай після мержу кожної фічі.

Pre-commit хук підключений через `core.hooksPath .githooks`. Якщо клонуєш репо
заново — виконай `git config core.hooksPath .githooks`.

## Правила, які найчастіше порушують

**Тести не ходять у мережу.** Зовнішні сервіси — тільки записані фікстури.

**Константа, що впливає на результат, іде в YAML під git**, не в код і не в
прапорець CLI.

**`spec.md` не редагується під час імплементації.** Задача нездійсненна як
специфікована -> `BLOCKED` із поясненням, не мовчазна зміна вимоги.

<!-- TODO: додай сюди правила, специфічні для цього проекту -->
