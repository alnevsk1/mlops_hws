# Дефекты части A: причина → проявление → исправление → доказательство

Числа «до» сняты на нетронутой копии стартового проекта (коммит `6062836`) в отдельном
compose-проекте `mlops-hw6-broken` на порту 5007 с уменьшенным бюджетом
(`max_steps 2`, `train_limit 4`, `val_limit 2`, те же 6 конфигураций) — рабочая копия
и её MLflow не трогались. Итог `tests/check.py` на сломанной копии: **0/9**.
Числа «после» — из основного прогона (`make sweep`, бюджет по умолчанию 4 шага) на
стеке `mlops-hw6`, порт 5006.

## 1. Все кандидаты пишутся в один run

**В чём был.** `src/sweep.py` открывал один вложенный run `all-candidates` и внутри
него в цикле обучал все шесть конфигураций. `run_candidate` логирует в активный run,
поэтому все шесть писали в одно место. Защита `if not ...data.params` прятала ошибку
MLflow о повторной записи параметров.

**Как проявлялся.** В `metrics/sweep.json` 6 строк, но у них **1** уникальный `run_id`.
Под родительским sweep один дочерний run: имя `all-candidates`, тег `candidate` =
`all-r8-high` (последний кандидат). При этом params — от первого: `r=4`,
`lr=0.0001`, `q_proj,v_proj`, а `final_val_loss = 0.4200` — от последнего. Run
приписывает метрику одной конфигурации параметрам другой. В истории `val_loss` по
6 точек на шагах 0 и 2, `train_loss` — 12 точек вместо 2, в `samples.json` 12 строк
вместо 2. `make candidate` (`select`) отказывает: «Число отдельных runs не совпадает
с конфигурациями». Проверки 1 («отдельных кандидатов: 1») и 3 («неверный r») — FAIL.

**Как исправлен.** `with mlflow.start_run(run_name=candidate["name"], nested=True)`
внутри цикла: у каждого кандидата свой дочерний run со своими params, кривыми,
тегами, `samples.json` и моделью.

**Доказательство.** Sweep `6d392fac00f142c38da2bddfa6a05ca1`: **6** дочерних runs с
**6** разными `run_id`, имя run = тег `candidate` = конфигурация. У каждого params
совпадают со своей строкой `params.yaml` (например, `qv-r4-low`: r=4, lr=0.0001;
`all-r8-high`: r=8, lr=0.0003), в `val_loss` ровно по 1 точке на шагах 0, 2, 4, в
`train_loss` — 4 точки (= `max_steps`). Проверки 1 и 3 — PASS.

## 2. Артефакты пропадают при пересоздании контейнера

**В чём был.** В `compose.yaml` volume `artifacts` объявлен, но не примонтирован к
сервису `mlflow`. Сервер пишет файлы в `--artifacts-destination=/mlartifacts`, то есть
в слой контейнера. Postgres хранит метаданные в своём volume и переживает пересоздание.

**Как проявлялся.** `tests/check_storage.py` на сломанной копии: probe.txt записан,
после `docker compose up --force-recreate mlflow` скачивание
`persistence/probe.txt` → HTTP 500 («too many 500 error responses»), выход 1. Runs и
их метрики в UI на месте, а файлов уже нет: в `check.py` проверки 4 и 5 падают на
скачивании `model/MLmodel` и `samples.json` того же sweep, проверка 8 — FAIL.

**Как исправлен.** `volumes: [artifacts:/mlartifacts]` у сервиса `mlflow`.
`docker inspect mlops-hw6-mlflow-1`: `mlops-hw6_artifacts -> /mlartifacts`.

**Доказательство.** `make check-storage`: probe.txt записан в run
`d4f99e439a2b4950973e71cbe595d0a7`, контейнер пересоздан (`--force-recreate`, новый
контейнер создан 2026-10-25T21:55:27Z), скачанный файл имеет тот же SHA256
`3d38ab28…7b8b5e` (`metrics/storage-check.json`). После пересоздания проверки 4, 5, 7
скачивают модели sweep и загружают candidate из реестра — PASS; проверка 8 — PASS.

## 3. У runs нет версии данных

**В чём был.** `sweep.py` строил теги так:
`{k: v for k, v in provenance(p).items() if not k.startswith("data_")}` — хеши
`data_train_sha256` и `data_val_sha256` отбрасывались, хотя `provenance()` их считает.

**Как проявлялся.** У дочернего и родительского run нет ни одного тега `data_*`.
Нельзя установить, на каких train/val обучен кандидат, и нельзя заметить подмену
тензоров. `check.py`, проверка 2: «нет актуального data_train_sha256». `select()` тоже
требует эти теги («отсутствует или устарел data_train_sha256; повторите sweep»), в
сломанной версии до этой проверки он не доходил из-за дефекта 1.

**Как исправлен.** `versions = provenance(p)` — все ключи, включая хеши данных, идут в
теги родителя и каждого кандидата.

**Доказательство.** У родителя и всех 6 кандидатов есть `data_train_sha256 = 719a8c9d8298…`
и `data_val_sha256 = 7318e6f93db8…` — совпадают с `sha256` файлов
`data/tokenized/{train,val}.pt` (те же тензоры, что под DVC в `data/tokenized.dvc`).
Проверка 2 (`code`, `config`, `data_train`, `data_val`, `git_sha`, `base_revision`) —
PASS, `make candidate` проходит `select()`.

## 4. Регистрация в обход порога по loss

**В чём был.** `promote()` в `src/promote.py` считал `ratio`, но не вызывал `gate()`:
версия регистрировалась, получала теги `validation=passed`, `gate.max_val_ratio` и
alias при любом пороге. `gate()` (конечность метрик, статус run, `val/base ≤ порог`)
существовал, но был мёртвым кодом.

**Как проявлялся.** Вызов `promote(client, run, …, threshold=0.0)` на run сломанного
sweep с `val/base = 0.4897` создал версию 1 с тегами `validation=passed`,
`gate.max_val_ratio=0.0`. Положительный loss «прошёл» нулевой порог. Проверка 6 — FAIL.

**Как исправлен.** `ratio = gate(run, threshold)` первой строкой `promote()`: при отказе
`ValueError` до `register_model`, версия и alias не создаются.

**Доказательство.** На лучшем run (`73dd94f3…`, val/base = 0.3308) вызов
`promote(…, threshold=0.0)` → `ValueError: Порог не пройден: val/base=0.3308, максимум 0.0`,
модель `hw6-after-zero-threshold` в реестре не создана. Штатный порог 1.0 пройден:
версия 3 `my-qwen-adapter` с тегами `val_ratio=0.3308`, `gate.max_val_ratio=1.0`, alias
`candidate`. Проверка 6 — PASS.

## Итог части A

`make check` на сломанной копии — **0/9**, после исправлений — **9/9** (проверка 9 —
наличие этого отчёта, `comparison.csv` и `conclusion.md`). Отпечаток `tests/`:
`c42ac7788026` (совпадает с выданным, файлы `tests/` не менялись).

## Дополнительно: две проблемы MLflow 2.22 на Windows (не из четырёх дефектов)

Найдены на первых двух прогонах sweep исправленного кода; исправлены в `src/sweep.py`,
sweep пересобран целиком (в отчёте — третий прогон).

1. **Путь артефакта с обратным слешем.** `mlflow.pyfunc` собирает относительный путь
   артефакта в `MLmodel` через `os.path.join` → `artifacts\adapter`. Клиент на Windows
   читает его нормально (проверка 7 проходила), но `tests/check.py` строит из него URL
   `model/artifacts\adapter/adapter_config.json`, сервер отвечает HTTP 500 → проверка 4
   FAIL на всех 6 runs (итог первого прогона 7/9). Исправление: модель сохраняется
   `save_model` во временную папку, путь в `MLmodel` переводится в POSIX
   (`artifacts/adapter`), папка загружается `log_artifacts(…, "model")`.
2. **Завышенный пик памяти.** `pyfunc.save_model` (и `log_model` внутри) для
   `PythonModel` вызывает `load_context`, то есть загружает базу + адаптер на cuda, и
   экземпляр остаётся в глобальном `set_model()` до следующего кандидата. В первых двух
   прогонах `peak_memory_mb` первого кандидата 1164, остальных 2303–2389 — лишняя копия
   базы (~1,1 ГБ). Замер: после `save_model` на cuda 1156 МиБ, с `HW6_DEVICE=cpu` — 16 МиБ.
   Исправление: на время `save_model` служебная копия грузится на CPU (параметр
   `HW6_DEVICE` уже поддерживается `src/model.py`). В третьем прогоне пики 1164–1232 МБ.
   Первая гипотеза (проверка `input_example` в `log_model`) замером не подтвердилась —
   об этом сказано в сообщении коммита `15b59c4`.
