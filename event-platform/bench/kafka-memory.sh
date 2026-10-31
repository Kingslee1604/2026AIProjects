#!/usr/bin/env bash
# Measure the RAM of a local Kafka cluster at idle and under load.
#
#   bench/kafka-memory.sh 1   # one node   (deploy/kafka/compose.kafka-1.yaml)
#   bench/kafka-memory.sh 3   # three nodes (deploy/kafka/compose.kafka-3.yaml)
#
# The load generator runs in its own container so its JVM is not counted as broker memory.
# Raw samples, tool output and a summary land in bench/results/<date>-<host>-kafka-memory/k<N>/.
set -euo pipefail

NODES="${1:?usage: kafka-memory.sh 1|3}"
RECORDS="${RECORDS:-2000000}"
RECORD_SIZE="${RECORD_SIZE:-200}"
IMAGE="apache/kafka:4.3.1"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/deploy/kafka/compose.kafka-$NODES.yaml"
PROJECT="eventplat-k$NODES"
NETWORK="${PROJECT}_default"
OUT="$ROOT/bench/results/$(date +%F)-$(hostname)-kafka-memory/k$NODES"
mkdir -p "$OUT"

BOOTSTRAP="$(seq -s, -f 'kafka-%g:9092' 1 "$NODES")"
REPLICATION="$NODES"

compose() { docker compose -f "$COMPOSE" "$@"; }

# One line per container per sample: "<phase> <sample#> <container> <MiB>"
sample() {
  local tag="$1" i=0
  while :; do
    docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' |
      grep -E "^($PROJECT|eventplat-bench)" |
      awk -v i="$i" -v tag="$tag" '{
        v=$2; mib=v+0
        if (v ~ /GiB/) mib*=1024; else if (v ~ /KiB/) mib/=1024
        printf "%s %d %s %.1f\n", tag, i, $1, mib }'
    i=$((i + 1))
    sleep 1
  done
}

kafka_tool() {  # run a Kafka CLI tool in a throwaway container on the cluster network
  docker run --rm --name "eventplat-bench-$1" --network "$NETWORK" \
    -e KAFKA_HEAP_OPTS="-Xms256m -Xmx256m" "$IMAGE" "/opt/kafka/bin/$2" "${@:3}"
}

{
  echo "date: $(date -Is)"
  echo "host: $(hostname)"
  echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"
  echo "wsl_mem_total_mib: $(free -m | awk '/Mem:/{print $2}')"
  echo "docker: $(docker info --format '{{.ServerVersion}}')"
  echo "image: $IMAGE"
  echo "nodes: $NODES  records: $RECORDS  record_size: $RECORD_SIZE"
} > "$OUT/machine.txt"
free -m > "$OUT/free-before.txt"

compose down -v --remove-orphans >/dev/null 2>&1 || true
compose up -d

echo "waiting for cluster..."
ready=0
for _ in $(seq 30); do
  if [ "$(compose ps --status exited -q | wc -l)" -gt 0 ]; then
    compose logs --tail 20 >&2
    echo "a Kafka node exited during start-up" >&2
    exit 1
  fi
  if timeout 30 docker run --rm --network "$NETWORK" "$IMAGE" /opt/kafka/bin/kafka-topics.sh \
      --bootstrap-server "$BOOTSTRAP" --list >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[ "$ready" = 1 ] || { echo "cluster not ready after waiting" >&2; exit 1; }
sleep 20  # let start-up settle before the idle sample

: > "$OUT/samples.txt"
sample idle >> "$OUT/samples.txt" & SAMPLER=$!
sleep 15
kill "$SAMPLER"; wait "$SAMPLER" 2>/dev/null || true

kafka_tool topic kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --create --topic bench \
  --partitions 6 --replication-factor "$REPLICATION" | tee "$OUT/topic.txt"

sample load >> "$OUT/samples.txt" & SAMPLER=$!
kafka_tool producer kafka-producer-perf-test.sh --topic bench --num-records "$RECORDS" \
  --record-size "$RECORD_SIZE" --throughput -1 \
  --producer-props bootstrap.servers="$BOOTSTRAP" acks=all linger.ms=5 batch.size=65536 \
  | tee "$OUT/producer.txt"
kafka_tool consumer kafka-consumer-perf-test.sh --bootstrap-server "$BOOTSTRAP" --topic bench \
  --messages "$RECORDS" --timeout 60000 | tee "$OUT/consumer.txt"
sleep 3
kill "$SAMPLER"; wait "$SAMPLER" 2>/dev/null || true

free -m > "$OUT/free-after-load.txt"
compose down -v

python3 - "$OUT" <<'PY'
import collections, json, pathlib, sys
out = pathlib.Path(sys.argv[1])
peak = collections.defaultdict(float)
totals = collections.defaultdict(lambda: collections.defaultdict(float))
for line in (out / "samples.txt").read_text().split("\n"):
    if not line.strip():
        continue
    tag, i, name, mib = line.split()
    kind = "client" if name.startswith("eventplat-bench") else "broker"
    peak[(tag, name)] = max(peak[(tag, name)], float(mib))
    if kind == "broker":
        totals[tag][i] += float(mib)
summary = {
    "per_container_peak_mib": {f"{t}/{n}": round(v, 1) for (t, n), v in sorted(peak.items())},
    "cluster_peak_mib": {t: round(max(s.values()), 1) for t, s in totals.items()},
}
(out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
PY
echo "results: $OUT"
