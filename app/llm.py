"""LLM: natural language -> validated JSON {sql, chart_column, title}."""
from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


class LlmNotConfigured(RuntimeError):
    pass


def _client():
    if not API_KEY:
        raise LlmNotConfigured("Set OPENAI_API_KEY (and optionally OPENAI_BASE_URL / LLM_MODEL) in .env")
    from openai import OpenAI

    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


SYSTEM = """You are a senior SQL analyst. Given a SQLite schema and a question, write ONE SELECT query.

Rules:
- SQLite dialect only. Use JOINs where needed. Aggregate when the question implies it.
- Prefer a result shaped for charting when possible: a label column first, a numeric column second.
- Never explain. Respond with STRICT JSON only:
  {"sql": "SELECT ...", "chart_column": "<label column name or null>", "title": "<chart title>"}"""

FEW_SHOT = """Example:
Question: Top 5 cities by revenue
{"sql": "SELECT c.city AS city, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id JOIN order_items oi ON oi.order_id = o.id WHERE o.status != 'cancelled' GROUP BY c.city ORDER BY revenue DESC LIMIT 5", "chart_column": "city", "title": "Top 5 cities by revenue"}
"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError(f"no JSON in LLM reply: {text[:200]!r}")
    return json.loads(match.group(0))


def nl_to_sql(question: str, schema_ddl: str) -> dict:
    client = _client()
    res = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{FEW_SHOT}\nSchema:\n{schema_ddl}\n\nQuestion: {question}"},
        ],
        temperature=0,
        max_tokens=500,
    )
    data = _extract_json(res.choices[0].message.content or "")
    return {
        "sql": str(data.get("sql", "")),
        "chart_column": data.get("chart_column"),
        "title": str(data.get("title", question)),
    }
