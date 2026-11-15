# ДЗ 11 — Kubernetes под нагрузкой

CPU-имитатор развивает FastAPI API из ДЗ 8–10. Стенд: kind + Helm,
Prometheus/Grafana, две реплики, Service и ClientIP, HPA 2–3.
Условие: ../homework11.html.

- [Инструкция запуска и удаления](docs/runbook.md)
- [Текущее состояние проверок](docs/status.md)
- [Отчёт по фактически собранным данным](report.html)
- [Сырые результаты](results/)
- [Ревью маршрутизации и кейс из лекции](docs/review.md)

## Результат

Проверки завершены: 9/9 локальных, 7/7 интеграционных, проверка сырых
доказательств PASS. Сохранены 12 основных прогонов B по 120 с и оба цикла
HPA 2→3→2. При прогреве ошибки устранены: 40/140 → 0/140; при rollout
обрывы: 2/9 → 0/9. Это измерения CPU-имитатора, не языковой модели.

Код: 9f7309c; отчёт и результаты находятся в ветке codex/hw11.

## Окружение

Python 3.12, uv; зависимости закреплены в pyproject.toml и uv.lock.
Локальную .venv создаёт пользователь:

```powershell
uv sync
```

Все проверки выполняются через uv run --no-sync. Установка Windows-зависимостей
агентом запрещена. Для Docker отдельно разрешён uv sync --frozen --no-dev
при сборке образа. PyTorch/Transformers/Prefect этому CPU-стенду не нужны.

```powershell
make check
make check-evidence
```

check проверяет код и unit-тесты; check-evidence требует завершённых настоящих
Kubernetes-опытов. Эти две проверки не взаимозаменяемы.

## Воспроизводимость

params.yaml — источник runtime-настроек; scripts/sync_chart.py переносит его
и артефакты в Helm package. Helm values переопределяют параметры конкретного
опыта. Образы и инфраструктура ограничены ресурсами; конфигурации и фактические
версии находятся в results/environment.

Сырые данные не перезаписываются. Основные сравнения имеют префикс B-main-;
более ранние пилоты B-* оставлены с их фактическими параметрами.
Имитатор возвращает backend_mode=simulated. Его задержки не называются
TTFT/TPOT/tokens/s.

## Официальная документация

[Prometheus Python client](https://prometheus.github.io/client_python/),
[HTTPX streaming](https://www.python-httpx.org/async/),
[Kubernetes probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/),
[kind](https://kind.sigs.k8s.io/docs/user/quick-start/),
[Helm](https://helm.sh/docs/intro/install/).
