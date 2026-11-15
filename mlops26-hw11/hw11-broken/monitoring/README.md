# Источники и ограничения мониторинга

- Prometheus: prom/prometheus:v3.5.0, 5-секундный scrape/evaluation,
  retention 6h/256MB, emptyDir 512MiB.
- Grafana: grafana/grafana:12.1.1, анонимное чтение через localhost port-forward,
  emptyDir 128MiB. grafana-export.json получен из работающего API Grafana.
- kube-state-metrics: registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.17.0;
  отслеживает только pods/deployments/HPA namespace hw11.
- metrics-server.yaml основан на
  https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.0/components.yaml.
  Единственное изменение исходного манифеста при загрузке — флаг
  --kubelet-insecure-tls для сертификата локального kind.
- CPU/RAM собираются из kubelet /metrics/cadvisor через API proxy.
  RBAC hw11-monitor нужен для discovery и чтения proxy только в собственном кластере.
- Request ID и идентификаторы клиентов остаются в журналах; метрики не используют
  их как labels. Метки outcome ограничены completed/rejected/aborted.
- В load-тесте отдельная единица потока — chunk. Реальных токенов модели нет.

Проверка конфигурации выполнена внутри работающего Prometheus:
```powershell
kubectl --context kind-mlops-hw11 -n hw11 exec deployment/prometheus -- promtool check config /etc/prometheus/prometheus.yml
```

Два правила прошли проверку. Пример состояния и переходов — timeline.jsonl
каждого прогона. Отсутствие ALERTS после firing означает возврат условия в норму,
а не потерю сервером отдельных запросов: состояние scrape проверяется отдельно.
