# -*- coding: utf-8 -*-
import os
import uuid
from qdrant_client import QdrantClient, models

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None

qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

COLLECTION_NAME = "contract_clauses"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _ensure_collection():
    if not qdrant.collection_exists(COLLECTION_NAME):
        qdrant.create_collection(
            COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=qdrant.get_embedding_size(MODEL_NAME),
                distance=models.Distance.COSINE,
            ),
        )


def store_clauses(job_id: str, clauses: dict, risks: dict):
    """Save the obligations and termination clauses from a finalized analysis
    into Qdrant, so future contracts can be compared against them."""
    risk_by_clause = {r["clause"]: r["risk_level"] for r in risks.get("risks", [])}

    texts = []
    payloads = []
    ids = []

    for category in ("obligations", "termination_conditions"):
        for clause_text in clauses.get(category, []):
            texts.append(clause_text)
            payloads.append({
                "document": clause_text,
                "job_id": job_id,
                "category": category,
                "risk_level": risk_by_clause.get(clause_text, "unknown"),
            })
            ids.append(str(uuid.uuid4()))

    if not texts:
        return

    _ensure_collection()

    vectors = [models.Document(text=t, model=MODEL_NAME) for t in texts]

    qdrant.upload_collection(
        collection_name=COLLECTION_NAME,
        vectors=vectors,
        ids=ids,
        payload=payloads,
    )
    print(f"[Memory] Stored {len(texts)} clauses from job {job_id} into Qdrant")


def find_similar_clauses(contract_text: str, limit: int = 3):
    """Look up similar clauses from previously analyzed contracts.
    Returns an empty list on the very first run, before any memory exists yet."""
    try:
        if not qdrant.collection_exists(COLLECTION_NAME):
            return []

        results = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=models.Document(text=contract_text[:2000], model=MODEL_NAME),
            limit=limit,
        ).points
    except Exception as e:
        print(f"[Memory] No prior memory available yet ({e})")
        return []

    return [
        {
            "text": r.payload.get("document", ""),
            "risk_level": r.payload.get("risk_level", "unknown"),
            "score": round(r.score, 3),
        }
        for r in results
    ]