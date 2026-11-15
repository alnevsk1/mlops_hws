# Запуск, проверка и удаление

PowerShell, рабочая папка — mlops26-hw11/hw11-broken.
Python 3.12; пользователь создаёт .venv и uv.lock командой uv sync.
В Windows пакеты агент не устанавливает; внутри Docker сборка с
uv sync --frozen --no-dev разрешена пользователем отдельно.

## Стенд

```powershell
./scripts/bootstrap.ps1
kubectl --context kind-mlops-hw11 -n hw11 get pods -o wide
kubectl --context kind-mlops-hw11 top nodes
```

Скрипт повторно использует только кластер mlops-hw11.
Для повторной сборки после изменений используйте новый тег и замените image
в chart/values.yaml и clients.yaml; не переиспользуйте тег при уже загруженном
образе в kind. Запуск bootstrap рассчитан на свежий стенд или тот же образ.

Доступ к мониторингу — два отдельных терминала:

```powershell
kubectl --context kind-mlops-hw11 -n hw11 port-forward service/prometheus 9091:9090
kubectl --context kind-mlops-hw11 -n hw11 port-forward service/grafana 3001:3000
```

Prometheus: http://127.0.0.1:9091. Grafana:
http://127.0.0.1:3001/d/hw11 (анонимное чтение только локального стенда).

## Проверки и опыты

```powershell
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync python -m scripts.suite_b
uv run --no-sync python -m scripts.finish_experiments
uv run --no-sync python -m scripts.refine_metrics
uv run --no-sync python -m scripts.export_metrics
uv run --no-sync python -m scripts.diagnostics
uv run --no-sync python -m scripts.analyze_alerts
uv run --no-sync python -m scripts.analyze_hpa
uv run --no-sync python -m scripts.report
```

Опыты выполняются последовательно, иначе нагрузка перестаёт быть сопоставимой.
Имена результатов уникальны; experiment.py отказывается перезаписывать каталог.
Для нового полного повторения сначала сохраните результаты отдельно, затем
измените имена прогонов в сценариях. suite_b пропускает уже записанные summary;
наличие summary не заменяет проверку exit_codes и полноты сырых строк.
После серии B её временный maxSurge=2 возвращается к 1 тем же kubectl-manager,
чтобы следующий Helm 4 upgrade не конфликтовал с владением полем.
Основная сохранённая серия B использовала исходные histogram buckets
(results/environment/params-b-original.yaml); итоговый params.yaml содержит
уточнённые границы. Стоимость backend, входы и лимиты одинаковы.

## Хранение и ограничения

Prometheus: 6 часов / 256 MB, emptyDir до 512 MiB; Grafana emptyDir до 128 MiB.
Метрики эфемерны, сырые клиентские и Kubernetes-журналы сохраняются в results.
Перед удалением экспортировать Prometheus через scripts.export_metrics.
Свой образ API, kind-node, monitoring и их фактические размеры фиксируются
в results/environment. Общий docker system df включает предыдущие ДЗ.

metrics-server использует --kubelet-insecure-tls только в этом локальном kind.
В production требуется проверяемый сертификат kubelet. Стенд не публикует
NodePort/Ingress наружу; /debug и /drain — служебные пути учебного API.

## Удаление (команда для пользователя, агент не запускал)

После сохранения результатов остановить port-forward (Ctrl+C), затем:

```powershell
kind delete cluster --name mlops-hw11
```

Это удаляет весь собственный кластер ДЗ 11, включая его ConfigMap, тома и метрики.
Другие кластеры и Docker-ресурсы команда не затрагивает.
Образы и build cache остаются в Docker. Глобальные docker system prune,
volume prune и массовое удаление не требуются.
