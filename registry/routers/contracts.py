from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from registry.auth import require_write_token
from registry.database import get_db
from registry.models import ContractRecord
from registry.schemas import ContractDetail, ContractPublishRequest, ContractSummary

router = APIRouter()


def _get_or_404(db: Session, *filters: Any, detail: str) -> ContractRecord:
    record = db.query(ContractRecord).filter(*filters).first()
    if not record:
        raise HTTPException(status_code=404, detail=detail)
    return record


@router.post("/", status_code=201, response_model=ContractSummary,
             dependencies=[Depends(require_write_token)],
             responses={200: {"description": "Identical version already published (no-op)"},
                        409: {"description": "Version already published with different content"}})
def publish_contract(req: ContractPublishRequest, response: Response, db: Session = Depends(get_db)):
    """Publish a new contract version and make it the current one.

    Published versions are immutable. Re-publishing a version with identical
    content is a no-op (200), so CI can safely re-run; different content under
    an existing version is rejected (409) and needs a new version number.
    """
    metadata = req.content.get("metadata") or {}
    if metadata.get("name", req.name) != req.name or metadata.get("version", req.version) != req.version:
        raise HTTPException(
            status_code=422,
            detail="name and version must match the contract's metadata.name and metadata.version",
        )

    existing = db.query(ContractRecord).filter(
        ContractRecord.name == req.name, ContractRecord.version == req.version,
    ).first()
    if existing:
        if json.loads(existing.content) == req.content:
            response.status_code = 200
            return existing
        raise HTTPException(
            status_code=409,
            detail=f'Contract "{req.name}" v{req.version} is already published with different '
                   f"content. Published versions are immutable: publish a new version instead.",
        )

    db.query(ContractRecord).filter(
        ContractRecord.name == req.name,
        ContractRecord.is_current.is_(True),
    ).update({"is_current": False})
    record = ContractRecord(
        name=req.name,
        version=req.version,
        content=json.dumps(req.content),
        is_current=True,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        # A concurrent publish of the same contract won the race; the unique
        # indexes guarantee exactly one of them becomes current.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f'Contract "{req.name}" was published concurrently by another request; retry.',
        ) from exc
    db.refresh(record)
    return record


@router.get("/", response_model=list[ContractSummary])
def list_contracts(db: Session = Depends(get_db)):
    return db.query(ContractRecord).filter(ContractRecord.is_current.is_(True)).all()


@router.get("/{name}", response_model=ContractDetail)
def get_contract(name: str, db: Session = Depends(get_db)):
    return _get_or_404(
        db, ContractRecord.name == name, ContractRecord.is_current.is_(True),
        detail=f'Contract "{name}" not found',
    )


@router.get("/{name}/versions", response_model=list[ContractSummary])
def list_versions(name: str, db: Session = Depends(get_db)):
    return (
        db.query(ContractRecord)
        .filter(ContractRecord.name == name)
        .order_by(ContractRecord.published_at.desc())
        .all()
    )


@router.get("/{name}/versions/{version}", response_model=ContractDetail)
def get_version(name: str, version: str, db: Session = Depends(get_db)):
    return _get_or_404(
        db, ContractRecord.name == name, ContractRecord.version == version,
        detail=f'Contract "{name}" v{version} not found',
    )
