import os
import json
import threading
import time
import pika
import redis
import uvicorn
from fastapi import FastAPI
from agents import run_clause_extractor, run_risk_analyzer, run_summarizer
from memory import store_clauses, find_similar_clauses

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
JAEGER_ENDPOINT = os.getenv("JAEGER_ENDPOINT")

# Tracing is optional in the cloud (no Jaeger there) - only enable if JAEGER_ENDPOINT is set
if JAEGER_ENDPOINT:
    resource = Resource(attributes={"service.name": "contract-analyzer-worker"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=JAEGER_ENDPOINT, insecure=True)))
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Tiny HTTP app just so Render treats this as a normal (free) Web Service.
# The real work happens in the background thread started in __main__ below.
health_app = FastAPI()


@health_app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "message": "Worker is running"}


def get_connection(max_retries=10, delay=3):
    for attempt in range(1, max_retries + 1):
        try:
            return pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        except pika.exceptions.AMQPConnectionError:
            print(f"[Worker] RabbitMQ not ready yet (attempt {attempt}/{max_retries}), retrying in {delay}s...")
            time.sleep(delay)
    raise RuntimeError("Could not connect to RabbitMQ after multiple retries")


def update_job(job_id: str, status: str, result=None):
    redis_client.set(job_id, json.dumps({"status": status, "result": result}))


def get_job(job_id: str):
    data = redis_client.get(job_id)
    return json.loads(data) if data else None


def handle_new_contract(ch, method, properties, body):
    message = json.loads(body)
    job_id = message["job_id"]
    contract_text = message["contract_text"]

    with tracer.start_as_current_span("process_contract") as span:
        span.set_attribute("job_id", job_id)

        print(f"[Worker] Processing job {job_id}")
        update_job(job_id, "processing")

        with tracer.start_as_current_span("clause_extraction"):
            clauses = run_clause_extractor(contract_text)
        print(f"[Worker] Clauses extracted for {job_id}")

        with tracer.start_as_current_span("memory_lookup") as mem_span:
            similar = find_similar_clauses(contract_text)
            mem_span.set_attribute("similar_clauses_found", len(similar))
        if similar:
            print(f"[Worker] Found {len(similar)} similar clause(s) from past contracts for {job_id}")

        with tracer.start_as_current_span("risk_analysis") as risk_span:
            risks = run_risk_analyzer(clauses, similar_past_clauses=similar)
            risk_span.set_attribute("risk_level", risks.get("highest_risk_level", "unknown"))
        print(f"[Worker] Risk level for {job_id}: {risks.get('highest_risk_level')}")

        if risks.get("highest_risk_level") == "high":
            print(f"[Worker] HIGH RISK detected for {job_id} - pausing for human review")
            span.set_attribute("outcome", "pending_human_review")
            update_job(job_id, "pending_human_review", {"clauses": clauses, "risks": risks})
        else:
            with tracer.start_as_current_span("summarization"):
                summary = run_summarizer(clauses, risks)
            print(f"[Worker] Summary complete for {job_id}")
            span.set_attribute("outcome", "completed")
            update_job(job_id, "completed", {"clauses": clauses, "risks": risks, "summary": summary})
            store_clauses(job_id, clauses, risks)

    ch.basic_ack(delivery_tag=method.delivery_tag)


def handle_human_decision(ch, method, properties, body):
    message = json.loads(body)
    job_id = message["job_id"]
    decision = message["decision"]

    with tracer.start_as_current_span("process_human_decision") as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("decision", decision)

        job = get_job(job_id)
        if job is None or job.get("status") != "pending_human_review":
            print(f"[Worker] Ignoring decision for {job_id} - job not found or not pending review")
            span.set_attribute("outcome", "ignored")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        clauses = job["result"]["clauses"]
        risks = job["result"]["risks"]

        if decision == "approve":
            print(f"[Worker] Job {job_id} approved by human reviewer - generating summary")
            with tracer.start_as_current_span("summarization"):
                summary = run_summarizer(clauses, risks)
            update_job(job_id, "completed", {"clauses": clauses, "risks": risks, "summary": summary})
            print(f"[Worker] Summary complete for {job_id}")
            store_clauses(job_id, clauses, risks)
            span.set_attribute("outcome", "completed")
        else:
            print(f"[Worker] Job {job_id} rejected by human reviewer")
            update_job(job_id, "rejected", {"clauses": clauses, "risks": risks})
            span.set_attribute("outcome", "rejected")

    ch.basic_ack(delivery_tag=method.delivery_tag)


def consume_queue():
    connection = get_connection()
    channel = connection.channel()
    channel.queue_declare(queue="contract_analysis")
    channel.queue_declare(queue="human_decisions")

    channel.basic_consume(queue="contract_analysis", on_message_callback=handle_new_contract)
    channel.basic_consume(queue="human_decisions", on_message_callback=handle_human_decision)

    print("[Worker] Waiting for messages...")
    channel.start_consuming()


if __name__ == "__main__":
    # Run the RabbitMQ consumer loop in the background...
    consumer_thread = threading.Thread(target=consume_queue, daemon=True)
    consumer_thread.start()

    # ...and expose a tiny HTTP server on top, so Render (or any platform that
    # only offers free "Web Services") is happy this process listens on a port.
    port = int(os.getenv("PORT", 8001))
    uvicorn.run(health_app, host="0.0.0.0", port=port)