import os
import json
from groq import Groq

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "openai/gpt-oss-120b"


def run_clause_extractor(contract_text: str) -> dict:
    prompt = f"""You are a legal contract analysis assistant. Extract the key clauses from the contract below.

Return ONLY valid JSON in this exact format:
{{
  "parties": ["..."],
  "duration": "...",
  "obligations": ["..."],
  "termination_conditions": ["..."],
  "payment_terms": "..."
}}

Contract:
{contract_text[:6000]}
"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content
    return _safe_json_parse(content)


def run_risk_analyzer(clauses: dict, similar_past_clauses=None) -> dict:
    memory_context = ""
    if similar_past_clauses:
        memory_context = f"""

For reference, here are similar clauses found in previously analyzed contracts, along with the risk level assigned to them:
{json.dumps(similar_past_clauses)}

Use this only as additional context for consistency. Make your own independent judgment on the current clauses; do not blindly copy a past rating if the wording or context differs."""

    prompt = f"""You are a legal risk analysis assistant. Given these extracted contract clauses, identify risks.

For each risk, assign a risk level of "low", "medium", or "high".

Return ONLY valid JSON in this exact format:
{{
  "risks": [
    {{"clause": "...", "risk_level": "low|medium|high", "explanation": "..."}}
  ],
  "highest_risk_level": "low|medium|high"
}}

Clauses:
{json.dumps(clauses)}{memory_context}
"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content
    return _safe_json_parse(content)


def run_summarizer(clauses: dict, risks: dict) -> dict:
    prompt = f"""You are a legal summarization assistant. Write a concise, plain-language summary of this contract for a non-lawyer, including the key risks that were approved.

Return ONLY valid JSON in this exact format:
{{
  "summary": "...",
  "key_risks_noted": ["..."]
}}

Clauses:
{json.dumps(clauses)}

Approved Risks:
{json.dumps(risks)}
"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content
    return _safe_json_parse(content)


def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Failed to parse model output", "raw": text}