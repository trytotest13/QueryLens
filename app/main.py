"""QueryLens — FastAPI app. NL question -> LLM SQL -> SAFETY GATE -> read-only SQLite -> chart."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db, llm, safety

app = FastAPI(title="QueryLens — Text-to-SQL", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class QueryIn(BaseModel):
    question: str


@app.on_event("startup")
def _startup() -> None:
    db.ensure_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "llm_configured": bool(llm.API_KEY), "db": str(db.DB_PATH.name)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/schema")
def schema() -> dict:
    return {"ddl": db.schema_ddl()}


@app.post("/api/query")
def query(body: QueryIn) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")

    t0 = time.perf_counter()
    try:
        generated = llm.nl_to_sql(question, db.schema_ddl())
    except llm.LlmNotConfigured as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"LLM provider error: {e}")

    # ---- THE SAFETY GATE: nothing runs unless it passes every check ----
    verdict = safety.validate_sql(generated["sql"])
    if not verdict.ok:
        raise HTTPException(400, {"error": f"SQL rejected by safety gate: {verdict.reason}", "sql": generated["sql"]})

    try:
        conn = db.connect_readonly()
        try:
            cursor = conn.execute(verdict.normalized_sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(100)
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise HTTPException(400, f"SQLite error: {e}")

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # chart spec: label column + numeric column
    chart = None
    if len(columns) >= 2 and rows:
        label_idx = 0
        if generated.get("chart_column") and generated["chart_column"] in columns:
            label_idx = columns.index(generated["chart_column"])
        value_idx = 1 if label_idx == 0 else 0
        labels = [str(r[label_idx]) for r in rows]
        values = [r[value_idx] if isinstance(r[value_idx], (int, float)) else 0 for r in rows]
        chart = {"type": "bar", "labels": labels, "values": values, "title": generated.get("title") or question}

    return {
        "sql": verdict.normalized_sql,
        "rejected_reason": None,
        "columns": columns,
        "rows": rows,
        "chart": chart,
        "latency_ms": latency_ms,
    }
