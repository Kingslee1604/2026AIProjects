"""Record-format gates for ADR-0003: JSON (pydantic) vs Avro (fastavro) vs Protobuf.

    uv run --frozen --all-groups python spikes/record_format/formats.py formats
    uv run --frozen --all-groups python spikes/record_format/formats.py registry confluent|apicurio|karapace

`formats` needs the dev stack (`make up`): it measures size on the broker's disk, not just in
Python. `registry` starts one schema registry next to the dev stack, checks that it accepts a
safe schema change and blocks a breaking one, and measures its memory. Raw results go to
bench/results/<date>-<host>-record-format/.

Every decode ends in a validated UsageEvent, because that is what a consumer needs, so the
timings compare whole paths, not bare codecs.
"""

import io
import json
import pathlib
import random
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from typing import Any
from urllib import request
from urllib.error import HTTPError

import fastavro
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from pydantic import ConfigDict

from eventplat.events import UsageEvent

BOOTSTRAP = "localhost:29092"
BROKER_CONTAINER = "eventplat-kafka-1-1"
NETWORK = "eventplat_default"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SAMPLE = 100_000
TIMING_RUNS = 3
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
OUT = (
    pathlib.Path(__file__).parents[2]
    / "bench"
    / "results"
    / (f"{date.today()}-{socket.gethostname()}-record-format")
)


def sample_events(n: int) -> list[UsageEvent]:
    rng = random.Random(7)  # noqa: S311 - repeatable test data
    start = datetime(2026, 10, 27, tzinfo=UTC)
    return [
        UsageEvent(
            event_id=uuid.UUID(int=rng.getrandbits(128), version=4),
            tenant_id=f"tenant-{rng.randrange(500):04d}",
            model=rng.choice(MODELS),
            input_tokens=rng.randrange(10, 20_000),
            output_tokens=rng.randrange(1, 4_000),
            occurred_at=start + timedelta(microseconds=rng.randrange(86_400_000_000)),
        )
        for _ in range(n)
    ]


# ---------------------------------------------------------------- the three formats

AVRO_V1: dict[str, Any] = {
    "type": "record",
    "name": "UsageEvent",
    "namespace": "eventplat",
    "fields": [
        {"name": "event_id", "type": {"type": "string", "logicalType": "uuid"}},
        {"name": "tenant_id", "type": "string"},
        {"name": "model", "type": "string"},
        {"name": "input_tokens", "type": "long"},
        {"name": "output_tokens", "type": "long"},
        {"name": "occurred_at", "type": {"type": "long", "logicalType": "timestamp-micros"}},
    ],
}
# A safe change: a new field with a default.
AVRO_V2 = AVRO_V1 | {
    "fields": [*AVRO_V1["fields"], {"name": "cached_tokens", "type": "long", "default": 0}]
}
# A breaking change: a new field without a default, so old records cannot be read with it.
AVRO_BREAKING = AVRO_V1 | {"fields": [*AVRO_V1["fields"], {"name": "region", "type": "string"}]}

AVRO_V1_PARSED = fastavro.parse_schema(AVRO_V1)
AVRO_V2_PARSED = fastavro.parse_schema(AVRO_V2)


def protobuf_class(extra_field: bool) -> Any:
    """The message class for usage.proto, built at runtime so the spike needs no protoc.

    message UsageEvent {
      bytes  event_id       = 1;  // 16 raw bytes
      string tenant_id      = 2;
      string model          = 3;
      uint64 input_tokens   = 4;
      uint64 output_tokens  = 5;
      int64  occurred_at_us = 6;  // microseconds since the Unix epoch, UTC
      uint64 cached_tokens  = 7;  // v2 only
    }
    """
    f = descriptor_pb2.FieldDescriptorProto
    fields = [
        ("event_id", f.TYPE_BYTES),
        ("tenant_id", f.TYPE_STRING),
        ("model", f.TYPE_STRING),
        ("input_tokens", f.TYPE_UINT64),
        ("output_tokens", f.TYPE_UINT64),
        ("occurred_at_us", f.TYPE_INT64),
    ]
    if extra_field:
        fields.append(("cached_tokens", f.TYPE_UINT64))
    proto = descriptor_pb2.FileDescriptorProto(
        name=f"usage_v{2 if extra_field else 1}.proto", package="eventplat", syntax="proto3"
    )
    message = proto.message_type.add(name="UsageEvent")
    for number, (name, kind) in enumerate(fields, 1):
        message.field.add(name=name, number=number, type=kind, label=f.LABEL_OPTIONAL)
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(proto.SerializeToString())
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("eventplat.UsageEvent"))


PB_V1 = protobuf_class(extra_field=False)
PB_V2 = protobuf_class(extra_field=True)


def micros(dt: datetime) -> int:
    return (dt - EPOCH) // timedelta(microseconds=1)


def avro_encode(e: UsageEvent) -> bytes:
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, AVRO_V1_PARSED, e.model_dump())
    return buf.getvalue()


def avro_decode_raw(raw: bytes) -> Any:
    return fastavro.schemaless_reader(io.BytesIO(raw), AVRO_V1_PARSED, None)


def pb_encode(e: UsageEvent) -> bytes:
    return PB_V1(
        event_id=e.event_id.bytes,
        tenant_id=e.tenant_id,
        model=e.model,
        input_tokens=e.input_tokens,
        output_tokens=e.output_tokens,
        occurred_at_us=micros(e.occurred_at),
    ).SerializeToString()


def pb_fields(m: Any) -> dict[str, Any]:
    return {
        "event_id": uuid.UUID(bytes=m.event_id),
        "tenant_id": m.tenant_id,
        "model": m.model,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "occurred_at": EPOCH + timedelta(microseconds=m.occurred_at_us),
    }


FORMATS: dict[str, dict[str, Callable[..., Any]]] = {
    "json": {
        "encode": UsageEvent.to_bytes,
        "decode_raw": json.loads,
        "decode": UsageEvent.from_bytes,
    },
    "avro": {
        "encode": avro_encode,
        "decode_raw": avro_decode_raw,
        "decode": lambda raw: UsageEvent.model_validate(avro_decode_raw(raw)),
    },
    "protobuf": {
        "encode": pb_encode,
        "decode_raw": PB_V1.FromString,
        "decode": lambda raw: UsageEvent.model_validate(pb_fields(PB_V1.FromString(raw))),
    },
}


# ---------------------------------------------------------------- formats gates


def best_rate(fn: Callable[[Any], Any], items: list[Any]) -> int:
    """Records per second, best of TIMING_RUNS (least disturbed by the rest of the laptop)."""
    best = float("inf")
    for _ in range(TIMING_RUNS):
        started = time.perf_counter()
        for item in items:
            fn(item)
        best = min(best, time.perf_counter() - started)
    return round(len(items) / best)


def size_on_broker(name: str, payloads: list[bytes], compression: str) -> int:
    """Bytes the broker stores for these records, from kafka-log-dirs."""
    topic = f"spike.format.{name}.{compression}.{uuid.uuid4().hex[:6]}"
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    admin.create_topics([NewTopic(topic, 1, 1)])[topic].result(30)
    producer = Producer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "enable.idempotence": True,
            "acks": "all",
            "compression.type": compression,
            "linger.ms": 20,
        }
    )
    events = sample_events(len(payloads))
    for event, payload in zip(events, payloads, strict=True):
        while True:
            try:
                producer.produce(topic, key=event.key, value=payload)
                break
            except BufferError:
                producer.poll(0.05)
    assert producer.flush(60) == 0
    out = subprocess.run(
        [
            "docker",
            "exec",
            BROKER_CONTAINER,
            "/opt/kafka/bin/kafka-log-dirs.sh",
            "--bootstrap-server",
            "kafka-1:9092",
            "--describe",
            "--topic-list",
            topic,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    report = json.loads(next(line for line in out.splitlines() if line.startswith("{")))
    size = sum(
        p["size"]
        for broker in report["brokers"]
        for d in broker["logDirs"]
        for p in d["partitions"]
    )
    admin.delete_topics([topic])[topic].result(30)
    return size


def evolution() -> dict[str, dict[str, str]]:
    """Add a field (cached_tokens, default 0). Can old and new code still read each other?

    new reader, old record: a consumer upgraded before the producers.
    old reader, new record: a producer upgraded before the consumers.
    """
    event = sample_events(1)[0]

    class UsageEventV2(UsageEvent):
        cached_tokens: int = 0

    class UsageEventTolerant(UsageEvent):
        model_config = ConfigDict(frozen=True, extra="ignore")

    v2 = UsageEventV2(**event.model_dump(), cached_tokens=42)
    avro_old = avro_encode(event)
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, AVRO_V2_PARSED, v2.model_dump())
    avro_new = buf.getvalue()
    pb_message = PB_V2.FromString(pb_encode(event))
    pb_message.cached_tokens = 42
    pb_new = pb_message.SerializeToString()

    def attempt(fn: Callable[[], Any]) -> str:
        try:
            fn()
        except Exception as exc:
            return f"fails: {type(exc).__name__}: {str(exc).partition(chr(10))[0][:80]}"
        return "ok"

    def avro_read(raw: bytes, writer: Any, reader: Any = None) -> Any:
        """Like a consumer would: decode, and insist every byte was used."""
        stream = io.BytesIO(raw)
        record = fastavro.schemaless_reader(stream, writer, reader)
        if stream.tell() != len(raw):
            raise ValueError(f"{len(raw) - stream.tell()} bytes left unread")
        return record

    return {
        "json, extra=forbid (as today)": {
            "new reader, old record": attempt(lambda: UsageEventV2.from_bytes(event.to_bytes())),
            "old reader, new record": attempt(lambda: UsageEvent.from_bytes(v2.to_bytes())),
        },
        "json, extra=ignore": {
            "new reader, old record": attempt(lambda: UsageEventV2.from_bytes(event.to_bytes())),
            "old reader, new record": attempt(lambda: UsageEventTolerant.from_bytes(v2.to_bytes())),
        },
        "avro, writer schema known (registry)": {
            "new reader, old record": attempt(
                lambda: avro_read(avro_old, AVRO_V1_PARSED, AVRO_V2_PARSED)
            ),
            "old reader, new record": attempt(
                lambda: avro_read(avro_new, AVRO_V2_PARSED, AVRO_V1_PARSED)
            ),
        },
        "avro, writer schema unknown (no registry)": {
            "new reader, old record": attempt(lambda: avro_read(avro_old, AVRO_V2_PARSED)),
            "old reader, new record": attempt(lambda: avro_read(avro_new, AVRO_V1_PARSED)),
        },
        "protobuf": {
            "new reader, old record": attempt(lambda: PB_V2.FromString(pb_encode(event))),
            "old reader, new record": attempt(
                lambda: UsageEvent.model_validate(pb_fields(PB_V1.FromString(pb_new)))
            ),
        },
    }


def formats_gates() -> dict[str, Any]:
    events = sample_events(SAMPLE)
    results: dict[str, Any] = {}
    for name, f in FORMATS.items():
        payloads = [f["encode"](e) for e in events]
        decoded = [f["decode"](p) for p in payloads[:1000]]
        assert decoded == events[:1000], f"{name}: round trip changed the records"
        results[name] = {
            "bytes_per_record": round(sum(map(len, payloads)) / len(payloads), 1),
            "encode_per_s": best_rate(f["encode"], events),
            "decode_codec_only_per_s": best_rate(f["decode_raw"], payloads),
            "decode_to_validated_event_per_s": best_rate(f["decode"], payloads),
            "on_broker_bytes_per_record": {
                c: round(size_on_broker(name, payloads, c) / len(payloads), 1)
                for c in ("none", "lz4", "zstd")
            },
        }
        print(name, json.dumps(results[name]), flush=True)
    return {
        "records": SAMPLE,
        "versions": {p: version(p) for p in ("pydantic", "fastavro", "protobuf")},
        "formats": results,
        "evolution": evolution(),
    }


# ---------------------------------------------------------------- registry gates

REGISTRIES: dict[str, dict[str, Any]] = {
    "confluent": {
        "image": "confluentinc/cp-schema-registry:8.3.2",
        "license": "Confluent Community License (source-available, not OSI open source)",
        "port": 8081,
        "base": "",
        "env": {
            "SCHEMA_REGISTRY_HOST_NAME": "spike-registry",
            "SCHEMA_REGISTRY_LISTENERS": "http://0.0.0.0:8081",
            "SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS": "PLAINTEXT://kafka-1:9092",
            "SCHEMA_REGISTRY_KAFKASTORE_TOPIC": "_schemas_spike_confluent",
            "SCHEMA_REGISTRY_KAFKASTORE_TOPIC_REPLICATION_FACTOR": "1",
        },
        "command": [],
    },
    "apicurio": {
        "image": "apicurio/apicurio-registry:3.3.3",
        "license": "Apache-2.0",
        "port": 8080,
        "base": "/apis/ccompat/v7",
        "env": {
            "APICURIO_STORAGE_KIND": "kafkasql",
            "APICURIO_KAFKASQL_BOOTSTRAP_SERVERS": "kafka-1:9092",
            "APICURIO_KAFKASQL_TOPIC": "_schemas_spike_apicurio",
        },
        "command": [],
    },
    "karapace": {
        "image": "ghcr.io/aiven-open/karapace:6.2.4",
        "license": "Apache-2.0",
        "port": 8081,
        "base": "",
        "env": {
            "KARAPACE_KARAPACE_REGISTRY": "true",
            "KARAPACE_ADVERTISED_HOSTNAME": "spike-registry",
            "KARAPACE_BOOTSTRAP_URI": "kafka-1:9092",
            "KARAPACE_HOST": "0.0.0.0",  # noqa: S104 - inside the container
            "KARAPACE_PORT": "8081",
            "KARAPACE_GROUP_ID": "karapace-spike",
            "KARAPACE_MASTER_ELIGIBILITY": "true",
            "KARAPACE_TOPIC_NAME": "_schemas_spike_karapace",
            "KARAPACE_REPLICATION_FACTOR": "1",
            "KARAPACE_LOG_LEVEL": "WARNING",
        },
        "command": ["python3", "-m", "karapace"],
    },
}
REGISTRY_CONTAINER = "spike-registry"
REGISTRY_MEM_LIMIT = "512m"
HOST_PORT = 18081


def http(
    method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 10
) -> tuple[int, Any]:
    req = request.Request(
        f"http://127.0.0.1:{HOST_PORT}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"null")
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def memory_mib(container: str) -> float:
    usage = (
        subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.split("/")[0]
        .strip()
    )
    return round(float(usage.rstrip("GiMKB")) * (1024 if "GiB" in usage else 1), 1)


def registry_gates(name: str) -> dict[str, Any]:
    spec = REGISTRIES[name]
    subprocess.run(["docker", "rm", "-f", REGISTRY_CONTAINER], capture_output=True, check=False)
    env = [arg for k, v in spec["env"].items() for arg in ("-e", f"{k}={v}")]
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REGISTRY_CONTAINER,
            "--network",
            NETWORK,
            "--memory",
            REGISTRY_MEM_LIMIT,
            "-p",
            f"127.0.0.1:{HOST_PORT}:{spec['port']}",
            *env,
            spec["image"],
            *spec["command"],
        ],
        check=True,
        capture_output=True,
    )
    base = spec["base"]
    subject = f"{base}/subjects/usage.events-value"
    try:
        # Ready means it accepts a write. Karapace serves reads seconds before it has elected
        # itself leader, and writes wait until then.
        started = time.perf_counter()
        while True:
            try:
                config_status, _ = http(
                    "PUT", f"{base}/config/usage.events-value", {"compatibility": "BACKWARD"}, 2
                )
                if config_status == 200:
                    break
            except OSError:
                pass
            if time.perf_counter() - started > 180:
                logs = subprocess.run(
                    ["docker", "logs", "--tail", "30", REGISTRY_CONTAINER],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                sys.exit(f"{name} not ready after 180 s:\n{logs.stdout}{logs.stderr}")
            time.sleep(0.5)
        ready_s = round(time.perf_counter() - started, 1)

        def schema(s: dict[str, Any]) -> dict[str, Any]:
            return {"schemaType": "AVRO", "schema": json.dumps(s)}

        v1_status, v1 = http("POST", f"{subject}/versions", schema(AVRO_V1))
        v2_status, _ = http("POST", f"{subject}/versions", schema(AVRO_V2))
        breaking_status, breaking = http("POST", f"{subject}/versions", schema(AVRO_BREAKING))
        time.sleep(30)
        idle = memory_mib(REGISTRY_CONTAINER)
        schema_id = v1["id"] if isinstance(v1, dict) else 1
        lookups_started = time.perf_counter()
        for _ in range(2_000):
            assert http("GET", f"{base}/schemas/ids/{schema_id}")[0] == 200
        lookups_per_s = round(2_000 / (time.perf_counter() - lookups_started))
        image_mib = round(
            int(
                subprocess.run(
                    ["docker", "image", "inspect", "--format", "{{.Size}}", spec["image"]],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            / 2**20
        )
        result = {
            "image": spec["image"],
            "image_mib": image_mib,
            "license": spec["license"],
            "mem_limit": REGISTRY_MEM_LIMIT,
            "ready_s": ready_s,
            "set_backward_compatibility": config_status,
            "register_v1": v1_status,
            "register_safe_change": v2_status,
            "register_breaking_change": breaking_status,
            "breaking_change_blocked": breaking_status == 409,
            "breaking_change_message": str(breaking)[:200],
            "idle_mib": idle,
            "after_lookups_mib": memory_mib(REGISTRY_CONTAINER),
            "lookups_per_s": lookups_per_s,
        }
    finally:
        subprocess.run(["docker", "rm", "-f", REGISTRY_CONTAINER], capture_output=True, check=False)
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "formats":
        result, out = formats_gates(), OUT / "formats.json"
    else:
        result, out = registry_gates(sys.argv[2]), OUT / f"registry-{sys.argv[2]}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
