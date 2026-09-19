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
| план, data-model, contracts, декомпозиція на задачі | `architect` | openrouter/openai/gpt-5.6-sol |
| рутинна задача `T-xxx`: boilerplate, тести, дрібні правки | `implementer` | openrouter/nvidia/nemotron-3.5-lightning:free |
| складні фічі, багатофайлові зміни, рефакторинги; задача з міткою `critical:` або після ескалації | `implementer-senior` | openrouter/nvidia/nemotron-3-ultra-550b-a55b:free |
| ревʼю після кожної задачі і перед мержем фічі | `reviewer` | deepseek/deepseek-v4-pro, read-only |

### Fallback-ланцюги opencode

Якщо модель не відповіла або повернула порожню відповідь, бери наступну в
ланцюгу. Файли агентів носять тільки основну модель; ланцюг тримається тут.

`implementer-senior`:

1. `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` — 1M контекст, multi-step reasoning, оркестрація;
2. `openrouter/thinkingmachines/inkling:free` — 1M контекст, reasoning + мультимодальність;
3. `openrouter/poolside/laguna-s-2.1:free` — найсильніший чисто кодовий агент free-тіру;
4. `openrouter/deepseek/deepseek-v4-flash-0731:free` — кодинг/агенти, 1M контекст, Rust;
5. крайній fallback — `openrouter/moonshotai/kimi-k2.7-code`.

`implementer`:

1. `openrouter/nvidia/nemotron-3.5-lightning:free` — 3B active, high-throughput;
2. альтернативи: `openrouter/cohere/north-mini-code:free` (64K output) або
   `openrouter/poolside/laguna-xs-2.1:free`;
3. крайній fallback — `openrouter/openai/gpt-oss-120b`.

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
