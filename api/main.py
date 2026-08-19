import io
import json
import uuid
import pika
import redis
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

resource = Resource(attributes={"service.name": "contract-analyzer-api"})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317", insecure=True)))
trace.set_tracer_provider(provider)

app = FastAPI(title="Agentic Contract Analyzer API")
FastAPIInstrumentor.instrument_app(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)


class DecisionRequest(BaseModel):
    decision: str  # "approve" or "reject"


def extract_text(filename: str, content: bytes) -> str:
    if filename.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    return content.decode("utf-8", errors="ignore")


def publish_to_queue(job_id: str, contract_text: str):
    connection = pika.BlockingConnection(pika.ConnectionParameters(host="rabbitmq"))
    channel = connection.channel()
    channel.queue_declare(queue="contract_analysis")

    message = json.dumps({"job_id": job_id, "contract_text": contract_text})
    channel.basic_publish(exchange="", routing_key="contract_analysis", body=message)
    connection.close()


def publish_decision(job_id: str, decision: str):
    connection = pika.BlockingConnection(pika.ConnectionParameters(host="rabbitmq"))
    channel = connection.channel()
    channel.queue_declare(queue="human_decisions")

    message = json.dumps({"job_id": job_id, "decision": decision})
    channel.basic_publish(exchange="", routing_key="human_decisions", body=message)
    connection.close()


@app.get("/health")
def health():
    return {"status": "ok", "message": "Agentic Contract Analyzer API is running"}


@app.post("/analyze")
async def analyze_contract(file: UploadFile = File(...)):
    if not file.filename.endswith(".txt") and not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .txt or .pdf files are supported")

    content = await file.read()
    contract_text = extract_text(file.filename, content)

    job_id = str(uuid.uuid4())
    redis_client.set(job_id, json.dumps({"status": "queued", "result": None}))

    publish_to_queue(job_id, contract_text)

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def get_status(job_id: str):
    data = redis_client.get(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return json.loads(data)


@app.post("/jobs/{job_id}/decision")
def submit_decision(job_id: str, body: DecisionRequest):
    data = redis_client.get(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")

    job = json.loads(data)
    if job["status"] != "pending_human_review":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not pending human review (current status: {job['status']})",
        )

    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")

    publish_decision(job_id, body.decision)

    return {"job_id": job_id, "status": "decision_submitted", "decision": body.decision}


app.mount("/", StaticFiles(directory="static", html=True), name="static")