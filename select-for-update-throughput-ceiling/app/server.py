#!/usr/bin/env python3
import argparse
import json
import os
import random
import threading
import time

from opentelemetry import trace
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from psycopg_pool import ConnectionPool, PoolTimeout


class FileSpanExporter(SpanExporter):
    def __init__(self, path):
        self.f = open(path, "w")
        self.count = 0

    def export(self, spans):
        for s in spans:
            self.f.write(json.dumps({
                "trace_id": format(s.context.trace_id, "032x"),
                "span_id": format(s.context.span_id, "016x"),
                "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
                "name": s.name,
                "start_ns": s.start_time,
                "end_ns": s.end_time,
                "attributes": dict(s.attributes or {}),
                "status": s.status.status_code.name,
            }) + "\n")
        self.count += len(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.f.close()


def poisson_offsets(rate, duration, seed):
    rng = random.Random(seed)
    out, t = [], 0.0
    if rate <= 0:
        return out
    while True:
        t += rng.expovariate(rate)
        if t >= duration:
            return out
        out.append(t)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=float, required=True)
    p.add_argument("--pool", type=int, required=True)
    p.add_argument("--work", type=float, required=True)
    p.add_argument("--work-at", choices=["inside", "before"], required=True)
    p.add_argument("--plain-rate", type=float, default=0)
    p.add_argument("--duration", type=float, default=30)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", required=True)
    a = p.parse_args()

    exporter = FileSpanExporter(os.path.join(a.out, "spans.jsonl"))
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=1 << 17, max_export_batch_size=512))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("server")
    PsycopgInstrumentor().instrument(tracer_provider=provider)

    dsn = " ".join(f"{k}={os.environ[v]}" for k, v in
                   [("host", "PGHOST"), ("port", "PGPORT"), ("dbname", "PGDATABASE"),
                    ("user", "PGUSER"), ("password", "PGPASSWORD")])
    pool = ConnectionPool(dsn, min_size=a.pool, open=False)
    pool.open(wait=True)

    records = []
    rec_lock = threading.Lock()

    def finish(rec):
        rec["end_ns"] = time.time_ns()
        with rec_lock:
            records.append(rec)

    def order(i, sched_ns):
        rec = {"kind": "order", "id": i, "sched_ns": sched_ns}
        with tracer.start_as_current_span("POST /orders") as root:
            rec["trace_id"] = format(root.get_span_context().trace_id, "032x")
            rec["start_ns"] = time.time_ns()
            try:
                with pool.connection() as conn:
                    rec["conn_ns"] = time.time_ns()
                    if a.work_at == "before":
                        time.sleep(a.work)
                    with conn.cursor() as cur:
                        cur.execute("SELECT stock FROM items WHERE id = 1 FOR UPDATE")
                        stock = cur.fetchone()[0]
                        if stock <= 0:
                            conn.rollback()
                            rec["status"] = "sold_out"
                            return
                        if a.work_at == "inside":
                            time.sleep(a.work)
                        cur.execute("UPDATE items SET stock = stock - 1 WHERE id = 1")
                        cur.execute("INSERT INTO orders (item_id) VALUES (1)")
                    conn.commit()
                    rec["commit_ns"] = time.time_ns()
                rec["status"] = "ok"
            except PoolTimeout:
                rec["status"] = "pool_timeout"
            except Exception as e:
                rec["status"] = "error"
                rec["error"] = repr(e)
            finally:
                finish(rec)

    def plain(i, sched_ns):
        rec = {"kind": "plain", "id": i, "sched_ns": sched_ns}
        with tracer.start_as_current_span("GET /items") as root:
            rec["trace_id"] = format(root.get_span_context().trace_id, "032x")
            rec["start_ns"] = time.time_ns()
            try:
                with pool.connection() as conn:
                    rec["conn_ns"] = time.time_ns()
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, stock FROM items WHERE id = 1")
                        cur.fetchone()
                    conn.commit()
                    rec["commit_ns"] = time.time_ns()
                rec["status"] = "ok"
            except PoolTimeout:
                rec["status"] = "pool_timeout"
            except Exception as e:
                rec["status"] = "error"
                rec["error"] = repr(e)
            finally:
                finish(rec)

    t0_ns = time.time_ns() + 1_000_000_000
    threads = []
    threads_lock = threading.Lock()

    def dispatch(handler, offsets):
        for i, off in enumerate(offsets):
            sched_ns = t0_ns + int(off * 1e9)
            delay = (sched_ns - time.time_ns()) / 1e9
            if delay > 0:
                time.sleep(delay)
            t = threading.Thread(target=handler, args=(i, sched_ns))
            t.start()
            with threads_lock:
                threads.append(t)

    streams = [(order, poisson_offsets(a.rate, a.duration, a.seed))]
    if a.plain_rate > 0:
        streams.append((plain, poisson_offsets(a.plain_rate, a.duration, a.seed + 1)))
    dispatchers = [threading.Thread(target=dispatch, args=s) for s in streams]
    for d in dispatchers:
        d.start()
    for d in dispatchers:
        d.join()
    for t in threads:
        t.join()
    done_ns = time.time_ns()

    with pool.connection() as conn:
        stock, orders = conn.execute("SELECT (SELECT stock FROM items WHERE id = 1), (SELECT count(*) FROM orders)").fetchone()
    pool.close()
    provider.shutdown()

    with open(os.path.join(a.out, "requests.jsonl"), "w") as f:
        for r in sorted(records, key=lambda r: (r["kind"], r["id"])):
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(a.out, "run.json"), "w") as f:
        json.dump({
            "rate": a.rate, "pool": a.pool, "work": a.work, "work_at": a.work_at,
            "plain_rate": a.plain_rate, "duration": a.duration, "seed": a.seed,
            "t0_ns": t0_ns, "done_ns": done_ns,
            "arrivals": {fn.__name__: len(offs) for fn, offs in streams},
            "spans_exported": exporter.count,
            "db_stock_after": stock, "db_orders_after": orders,
        }, f, indent=1)


if __name__ == "__main__":
    main()
