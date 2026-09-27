from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from registry.auth import require_write_token
from registry.database import get_db
from registry.models import ValidationResultRecord
from registry.schemas import ValidationResultDetail, ValidationResultRequest, ValidationResultSummary

router = APIRouter()


@router.post("/", status_code=201, response_model=ValidationResultSummary,
             dependencies=[Depends(require_write_token)])
def store_result(req: ValidationResultRequest, db: Session = Depends(get_db)):
    record = ValidationResultRecord(
        contract_name=req.contract_name,
        contract_version=req.contract_version,
        dataset_location=req.dataset_location,
        validated_at=req.validated_at,
        overall_status=req.overall_status,
        row_count=req.row_count,
        clause_results=json.dumps([c.model_dump() for c in req.clause_results]),
        error_message=req.error_message,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/", response_model=list[ValidationResultSummary])
def list_results(
    contract_name: str | None = Query(None),
    limit:         int           = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(ValidationResultRecord)
    if contract_name:
        q = q.filter(ValidationResultRecord.contract_name == contract_name)
    return q.order_by(ValidationResultRecord.validated_at.desc()).limit(limit).all()


@router.get("/{result_id}", response_model=ValidationResultDetail)
def get_result(result_id: int, db: Session = Depends(get_db)):
    record = db.get(ValidationResultRecord, result_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Validation result {result_id} not found")
    record.clause_results = json.loads(record.clause_results)  # type: ignore[assignment]
    return record
