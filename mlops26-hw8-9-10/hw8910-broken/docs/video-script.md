# Сценарий видео сдачи (6–7 минут)

Запись экрана выполняется после подготовки `.lab-real` командой `uv sync --extra real`. Данные и веса не показывать в проводнике; демонстрировать команды и сохранённые журналы.

| Время | Что показать |
|---|---|
| 0:00–0:40 | `git log -2 --oneline`: исходный `9a13c7c` и исправленный `a101453`; `defects.csv` с семью причинами. |
| 0:40–1:35 | `reports/logs/integration-before.log` и `contracts-before.log`: поведенческие FAIL исходного прототипа. Затем `make check` или `reports/logs/fast-green.log`: 21/21. |
| 1:35–2:35 | `flow-fail.log` (`PREPARED`, exit 1), `flow-recovered.log` (`Cached`, exit 0), `flow-invalidated.log` (новый ключ после изменения байтов). Показывать разные процессы и один release ID. |
| 2:35–3:25 | `reports/release-report.json`, `real-gate-final.log` и `real-gate-pointer.log`: training/eval run ID, регрессия `type-feat` 9/9 → 8/9, одинаковый SHA `accepted.json`. |
| 3:25–4:45 | `reports/baseline-manifest.json`; запустить `make serve-real`, `GET /health`, `GET /ready`, невалидный `POST /generate` → 422 и неизменный `/debug.calls`, затем реальный ответ из `reports/real-api-check.json`. Если загрузка занимает время, показать сохранённый лог и уже работающий сервер. |
| 4:45–5:45 | `reports/bench-raw.jsonl` и `bench-summary.json`: 8+8, 0 ошибок; пояснить ожидание в очереди при двух клиентах. |
| 5:45–6:30 | STUB A → B → A в `test_loaded_version_survives_pointer_change_and_rollback`; фактическая версия меняется после перезапуска. Завершить ограничениями: кандидат отклонён, нет второго принятого адаптера, Docker/кластер и TTFT не измерялись. |

Команды для живого показа из PowerShell в каталоге `hw8910-broken`:

```powershell
git log -2 --oneline
make check
Get-Content reports/logs/real-gate-pointer.log
Get-Content reports/real-api-check.json
Get-Content reports/bench-summary.json
```

Видео следует сохранить отдельно и добавить ссылку в `docs/report.md` перед сдачей.
