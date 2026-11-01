# ДЗ 8+9+10 — проверяемый выпуск модели

## 1. Версии и запуск

Общий репозиторий: `alnevsk1/mlops_hws`, ветка `codex/hw8-9-10`. Исходный прототип сохранён коммитом `9a13c7c`; исправления и доказательства — `a101453`. Семь причин, тесты и красные/зелёные журналы связаны в [defects.csv](../defects.csv). Приложенный `tests/test_release.py` не менялся (`git diff 9a13c7c -- tests/test_release.py` пуст).

Стенд: Windows 11, Intel i5-6500, NVIDIA RTX 4060; Python 3.12.14, PyTorch 2.14.0+cu130, CUDA 13.0, cuDNN 92400, Transformers 5.17.0, Prefect 3.7.2, FastAPI 0.115.14. Версии закреплены в `pyproject.toml` и `uv.lock`. FastAPI 0.162.0 оказался несовместим с Prefect 3.8.8 на вызове `Body(min_items=...)`; текущая пара проверена запуском flow и API.

Новые API использованы по официальным руководствам: [кеш задач Prefect](https://www.prefect.io/docs/v3/how-to-guides/workflows/cache-workflow-steps) и [lifespan FastAPI](https://fastapi.tiangolo.com/advanced/events/).

Команды из `hw8910-broken/`:

```powershell
uv sync --extra real
make check
uv run --no-sync python baseline.py --workspace .lab-real
uv run --no-sync python flow.py --workspace .lab-real --mode real
uv run --no-sync python service.py --workspace .lab-real --backend real --port 8091 --warmup-seconds 0
uv run --no-sync python benchmark.py --url http://127.0.0.1:8091 --n 8 --limit 32
```

`baseline.py` запускается один раз и отказывается перезаписать существующий указатель. Веса не включены в Git: baseline берётся из HF по точной revision, её локальные файлы сверяются по SHA-256. Для повторения оценки ДЗ 6+7 нужны его `data/eval/` из DVC и сохранённые `metrics/`; файлы оценки уже отслеживаются общим Git. MLflow-артефакт отклонённого адаптера находится в прежнем Docker volume и нужен, только если планируется последующий выпуск кандидата. Docker при этом прогоне не был запущен.

## 2. Что переиспользовано из ДЗ 6+7

Training run кандидата: `73dd94f31fd1430198b96c928488bdfd`, реестр `models:/my-qwen-adapter/3`. Evaluation run: `ff70f00c527c4142abdfd5899beb7c71`. Baseline: `Qwen/Qwen3-0.6B` на revision `c1899de289a04d12100db370d81485cdf75e47ca`.

Идентификатор последнего реального Prefect-прохода: `27383956-b707-4620-88a0-d205cc70876b`; [журнал](../reports/logs/real-gate-final.log) показывает повторное использование кеша и отказ.

На 57 golden-примерах baseline дал 10 правильных, кандидат 23; format rate у обоих 56/57. Но критический срез `type-feat` ухудшился с 9/9 до 8/9. Повторный вызов **того же** `quality_gate` из `evaluation/solution.py` ДЗ 6+7 отклонил кандидата. Пороги не менялись, новый sweep и обучение не выполнялись. [release-report.json](../reports/release-report.json) содержит ID eval-run, метрики, причину, пути и SHA-256 полных baseline/candidate-отчётов и сырых ответов. STUB-отчёты из `lab.py` помечены `synthetic` и не служат оценкой модели.

## 3. Граф процесса и кеш

`prepare_data → obtain_artifact → evaluate_gate → publish_candidate` — отдельные задачи одного Prefect-flow. Между задачами передаются описания входа, run ID, URI и хеши; веса и тензоры не становятся результатом задач. `obtain_artifact` сверяет ID готового training run со sweep и URI версии реестра. Это чтение результата, а не повторное обучение.

Реальная подготовка читает 57 golden-строк, проверяет схему и фактически применяет закреплённый Qwen chat template; максимум 417 токенов при лимите 768. Ключ Prefect-кеша — SHA-256 канонического JSON с хешем байтов входа и полным config: точной revision токенизатора, SHA-256 `tokenizer.json` и chat template, системной инструкцией, `enable_thinking`, `max_length`, `code_revision=prepare-v2` и режимом. Результат сохраняется Prefect локально и читается другим процессом. Идентичность результата (`cache_key`) отделена от идентичности попытки (`flow_run_id`/журнал Prefect).

Логи чистого STUB-опыта: [сбой](../reports/logs/flow-fail.log) — `PREPARED`, exit 1 после задачи; [повтор](../reports/logs/flow-recovered.log) — `Cached(type=COMPLETED)`, exit 0 и один выпуск B; [изменение входа](../reports/logs/flow-invalidated.log) — новый хеш и `PREPARED`, повторной публикации B нет. После изменения `config.json` ключ также меняется; это отдельно проверяет `test_every_preparation_setting_invalidates_cache`.

## 4. Публикация, отказ и восстановление

`validate_release` требует schema version 1, непустые model ID/URI/runtime, четыре 64-значных хеша, пересчёт runtime SHA, `status=complete`, именно boolean `passed=True`, eval-run ID, совпадение ID/хешей и конечные accuracy/format rate в [0,1]. `verify_files` независимо пересчитывает хеш каждого файла из закреплённого списка и общий хеш модели. `publish` пишет временный файл рядом с целью, вызывает flush/fsync и `os.replace`; при ошибке временный файл удаляется, прежние байты не меняются.

Идентификатор события выпуска включает model ID и SHA, runtime SHA, protocol/golden SHA и evaluation-run ID. События `publications.jsonl` дедуплицируются и записываются атомарно; отдельные попытки видны в `attempts.jsonl` учебного стенда и в Prefect run-журнале. Если процесс упал после замены `accepted.json`, но до события, повтор добавляет отсутствующее событие ровно один раз. Это проверяет `test_pointer_crash_reconciles_one_event`. Один писатель является явным ограничением.

Неполный отчёт, строковое `passed`, чужие хеши, NaN/Inf, отказ по качеству, испорченный файл весов и ошибка `os.replace` проверяются отрицательными тестами. На реальном отказе SHA-256 `accepted.json` **до и после одинаковый**: `737e8786b78c13993cd1c550b2879fa52d5f93cae5027e66e2513b35347b7395` ([журнал](../reports/logs/real-gate-pointer.log)). Никакого нового `champion` или принятого кандидата не создано. Текущий `accepted.json` создан как **исторически принятая baseline**, без выдуманного PASS кандидата; копия manifest — [baseline-manifest.json](../reports/baseline-manifest.json). SHA модели `052d8c0b8c31f61d79bcbbcca49c85414118746986773d57fb6a56f58e5a0c09`, SHA runtime `f9f5f5e173af8507a97263d5ff806e12c4b269e441f2e75504971e24b4e42937`.

## 5. Быстрые проверки и три триггера

Локальный `make check` компилирует код и запускает 13 приложенных + 8 новых интеграционных тестов без весов: **21/21, exit 0** ([лог](../reports/logs/fast-green.log)). Шесть первых новых тестов на исходной версии дали поведенческие FAIL без ошибки импорта ([лог](../reports/logs/integration-before.log)); приложенные тесты на исходном `release.py` дали 54 отказа subtest ([лог](../reports/logs/contracts-before.log)). Намеренная мутация `cache_key` в отдельном процессе дала **exit 1** и шесть отказов; рабочий код не менялся ([лог](../reports/logs/intentional-red.log)). `ci.example.yml` — шаблон, удалённый Actions-run и защита merge **не заявляются**.

| Изменение | Действие |
|---|---|
| Код процесса/API | `make check`; при изменении chat template или генерации повторить полную оценку ДЗ 6+7 и `make gate-real` |
| Данные | Изменение golden инвалидирует кеш и требует новой оценки; изменение train данных требует нового обучения только для нового адаптера |
| Новый адаптер | Проверить загрузку и хеши, заново получить baseline/candidate outputs, вызвать полный gate, затем выпуск и реальный API smoke |

Правка документации не требует обучения. Временную сетевую ошибку можно повторить ограниченно; неверные данные, несовместимый артефакт и отказ gate не повторяются автоматически.

## 6. API, фактическая версия и откат

FastAPI проверяет manifest и реальные файлы при старте, затем загружает указанную базу и прогревает backend. `/health` говорит о жизни процесса, `/ready` даёт 503 до прогрева, `/generate` тоже даёт 503 до готовности. `max_new_tokens` — целое 1…32, prompt непустой и не длиннее 4096 символов; backend ограничен одним одновременным вычислением. Неверный запрос даёт 4xx **до** генерации.

[Реальный API smoke](../reports/real-api-check.json): health 200, ready 200; `max_new_tokens=0` → 422, вызовы backend 1 → 1; валидный ответ `{"answer": "feat"}`, 7 выходных токенов, 1,392 с генерации, свой request ID и фактически загруженный baseline model ID. Условия системной инструкции, Qwen template, `enable_thinking=false`, `do_sample=false`, seed 42, лимит 32 и dtype `bfloat16` берутся из runtime manifest, построенного из конфигурации оценки.

STUB-проверка A → B → A подтверждает изменение версии **только после перезапуска**. Пока в памяти A, замена файлового указателя на B не меняет ID ответа. STUB-ответы помечены `STUB ONLY` и не считаются модельным smoke. При отклонённом кандидате второй реально принятой версии модели нет.

## 7. Нагрузка и ограничения

После прогрева отправлены одинаковые 8 запросов при клиентской параллельности 1 и затем 8 при 2, лимит 32. [Все 16 строк](../reports/bench-raw.jsonl) сохраняют ID, статус/ошибку, клиентскую задержку, время генерации, model ID и число токенов. [Сводка](../reports/bench-summary.json):

| Параллельность | Успехи/ошибки | Пакет, с | Успехов/с | Средняя задержка, с | Максимум, с |
|---:|---:|---:|---:|---:|---:|
| 1 | 8/0 | 11,914 | 0,671 | 1,488 | 1,836 |
| 2 | 8/0 | 12,443 | 0,643 | 2,948 | 3,886 |

Backend выполняет вычисление последовательно: при двух клиентах запрос ждёт semaphore, поэтому клиентская задержка почти удваивается, а throughput не растёт. Это малая выборка без вывода об устойчивом p95. JSON-ответ даёт полную задержку; TTFT и ITL здесь не измерялись. Prefill обрабатывает входные токены, decode генерирует по одному; KV-cache растёт с длиной контекста и числом одновременных запросов. Этот последовательный Transformers-сервис не является замером SGLang/vLLM.

## 8. Сдача и видео

В Git входят код, lockfile, тесты, manifest, release-report, полные журналы и сырые строки замера. Не входят `.venv`, веса, HF-кеш, данные DVC и Docker volume. Отдельный архив `data/eval/` и `data/source/` ДЗ 6+7 создан в `.lab-submission/hw67-eval-and-source.zip` (780 443 байта; SHA-256 `ed874e7b8b26950eaa7d6bbc38b3915ca4a688921c6a4df4469e614918506ef8`); его нужно приложить преподавателю. Для проверки training artifact также потребуется доступ к MLflow volume/экспорту адаптера. Видео 5–8 минут **ещё не записано**. Готов [сценарий показа](video-script.md): два коммита и `defects.csv` → красные/зелёные тесты → три Prefect-журнала → реальный gate-отказ и неизменный SHA указателя → API smoke → benchmark CSV/JSON и объяснение отката.
