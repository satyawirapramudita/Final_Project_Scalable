# Prometheus untuk deploy sandbox 3 server (berjalan di app-1).
# Templat: bootstrap mengganti __APP2__ dan __DATA__ dengan IP privat lalu menulis
# deploy/sandbox/prometheus.yml. Nama job sama dengan observability/prometheus/
# prometheus.yml agar dashboard Grafana dan alerts.yml tetap berlaku.

global:
  scrape_interval: 5s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  # Replika di app-1 (DNS Docker) dan app-2 (IP privat, port dibuka deploy/sandbox/app.yml).
  - job_name: auth
    static_configs: [{ targets: ["auth:8001", "__APP2__:8001"] }]
  - job_name: poem
    static_configs: [{ targets: ["poem:8002", "__APP2__:8002"] }]
  - job_name: wordcloud
    static_configs: [{ targets: ["wordcloud:8003", "__APP2__:8003"] }]
  - job_name: search
    static_configs: [{ targets: ["search:8004", "__APP2__:8004"] }]
  - job_name: kbbi-linker
    static_configs: [{ targets: ["kbbi-linker:8005", "__APP2__:8005"] }]
  - job_name: nlp-worker
    static_configs: [{ targets: ["nlp-worker:8100", "__APP2__:8100"] }]

  - job_name: gateway
    static_configs:
      - targets: ["nginx-exporter:9113"]
        labels: { gateway: gateway-a }
      - targets: ["__APP2__:9113"]
        labels: { gateway: gateway-b }

  - job_name: rabbitmq
    static_configs:
      - targets: ["__DATA__:15692", "__DATA__:15693", "__DATA__:15694"]

  - job_name: rabbitmq-queues
    metrics_path: /metrics/detailed
    params:
      family: [queue_coarse_metrics, queue_consumer_count]
    static_configs:
      - targets: ["__DATA__:15692", "__DATA__:15693", "__DATA__:15694"]
