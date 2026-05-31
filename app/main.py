import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .lineage import report_to_json, rewrite_query
from .models import TraceRun
from .schemas import RewritePreviewResponse, TraceRunRequest, TraceRunResponse

app = FastAPI(title="SQL Traceability Rewriter")

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    recent = db.query(TraceRun).order_by(TraceRun.id.desc()).limit(20).all()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "recent": recent,
        },
    )


def _trace_to_response(trace: TraceRun) -> TraceRunResponse:
    return TraceRunResponse(
        id=trace.id,
        title=trace.title,
        base_sql=trace.base_sql,
        reference_sql=trace.reference_sql,
        output_sql=trace.output_sql,
        report=json.loads(trace.report_json),
        created_at=trace.created_at,
    )


@app.post("/api/rewrite", response_model=RewritePreviewResponse)
def rewrite_preview(payload: TraceRunRequest):
    try:
        output_sql, report = rewrite_query(payload.base_sql, payload.reference_sql, dialect="bigquery")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RewritePreviewResponse(output_sql=output_sql, report=report)


@app.post("/api/trace-runs", response_model=TraceRunResponse)
def create_trace_run(payload: TraceRunRequest, db: Session = Depends(get_db)):
    try:
        output_sql, report = rewrite_query(payload.base_sql, payload.reference_sql, dialect="bigquery")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace = TraceRun(
        title=payload.title or "Sin titulo",
        base_sql=payload.base_sql,
        reference_sql=payload.reference_sql,
        output_sql=output_sql,
        report_json=report_to_json(report),
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)

    return _trace_to_response(trace)


@app.get("/api/trace-runs", response_model=list[TraceRunResponse])
def list_trace_runs(db: Session = Depends(get_db)):
    records = db.query(TraceRun).order_by(TraceRun.id.desc()).limit(100).all()
    return [
        _trace_to_response(record)
        for record in records
    ]


@app.get("/api/trace-runs/{trace_id}", response_model=TraceRunResponse)
def get_trace_run(trace_id: int, db: Session = Depends(get_db)):
    trace = db.get(TraceRun, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Traza no encontrada")
    return _trace_to_response(trace)


@app.get("/trace-runs/{trace_id}", response_class=HTMLResponse)
def trace_detail(trace_id: int, request: Request, db: Session = Depends(get_db)):
    trace = db.get(TraceRun, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Traza no encontrada")

    recent = db.query(TraceRun).order_by(TraceRun.id.desc()).limit(20).all()
    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "trace": trace,
            "trace_report": json.loads(trace.report_json),
            "recent": recent,
        },
    )
