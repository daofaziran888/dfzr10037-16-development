"""审计证据接口：从取样图代与关系明细打包、下载、重放，并读取复核记录。

路由只做参数搬运和响应封装；封包、回滚判定、线索充分性等业务判断都在
app.evidence 与 app.graph_versions 里。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import evidence, graph_versions

router = APIRouter(prefix="/api", tags=["审计证据"])


class EvidencePackageRequest(BaseModel):
    report_id: int
    generation: str
    edge_ids: list[str] = Field(default_factory=list)
    request_token: str | None = None
    kind: str = "original"
    supersedes: str | None = None


@router.get("/evidence/generations")
def list_generations() -> dict[str, Any]:
    """可取样图代及其节点/边规模，标记已回滚的图代。"""
    return {"items": graph_versions.list_generations()}


@router.get("/evidence/generations/{generation}")
def get_generation(generation: str) -> dict[str, Any]:
    record = graph_versions.get_generation(generation)
    if record is None:
        raise HTTPException(status_code=404, detail=f"图代 {generation} 未登记")
    record["digest"] = graph_versions.generation_digest(generation)
    return record


@router.get("/evidence")
def list_packages(report_id: int | None = Query(default=None)) -> dict[str, Any]:
    return {"items": evidence.list_packages(report_id)}


@router.post("/evidence/packages")
def create_package(payload: EvidencePackageRequest) -> dict[str, Any]:
    package, message = evidence.create_package(
        report_id=payload.report_id,
        generation=payload.generation,
        edge_ids=payload.edge_ids,
        request_token=payload.request_token,
        kind=payload.kind,
        supersedes=payload.supersedes,
    )
    if package is None:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message, "package": package}


@router.get("/evidence/{evidence_no}/file")
def download_file(evidence_no: str) -> Response:
    content = evidence.evidence_file(evidence_no)
    if content is None:
        raise HTTPException(status_code=404, detail=f"证据包 {evidence_no} 不存在")
    body = json.dumps(content, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{evidence_no}.evidence.json"',
        },
    )


@router.get("/evidence/{evidence_no}/replay")
def replay(evidence_no: str) -> dict[str, Any]:
    result = evidence.replay(evidence_no)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@router.post("/evidence/{evidence_no}/replay")
def replay_post(evidence_no: str) -> dict[str, Any]:
    return replay(evidence_no)


@router.post("/evidence/verify-file")
def verify_file(payload: dict[str, Any]) -> dict[str, Any]:
    """重新打开下载到本地的证据文件时，校验关联图代是否仍可回放。"""
    return evidence.verify_file(payload)


@router.get("/evidence/reviews")
def list_reviews(report_id: int | None = Query(default=None)) -> dict[str, Any]:
    return {"items": evidence.list_reviews(report_id)}


@router.get("/evidence/reviews/{review_no}")
def get_review(review_no: str) -> dict[str, Any]:
    review = evidence.inspect_review(review_no)
    if review is None:
        raise HTTPException(status_code=404, detail=f"复核记录 {review_no} 不存在")
    return review


class ReviewCloseRequest(BaseModel):
    verdict: str
    remark: str | None = None


@router.post("/evidence/reviews/{review_no}/close")
def close_review(review_no: str, payload: ReviewCloseRequest) -> dict[str, Any]:
    review, message = evidence.close_review(review_no, payload.verdict, payload.remark)
    if review is None:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message, "review": review}


@router.get("/evidence/clues/{report_id}")
def clue_assessment(report_id: int) -> dict[str, Any]:
    return evidence.assess_clues(report_id)


@router.get("/evidence/packages/{evidence_no}")
def get_package(evidence_no: str) -> dict[str, Any]:
    """单份证据包详情；放在 /evidence/packages 下避免与固定路径重名冲突。"""
    package = evidence.get_package(evidence_no)
    if package is None:
        raise HTTPException(status_code=404, detail=f"证据包 {evidence_no} 不存在")
    return package


@router.get("/evidence/rollback-impact/{generation}")
def rollback_impact(generation: str, edges: str | None = Query(default=None)) -> dict[str, Any]:
    record = graph_versions.get_generation(generation)
    if record is None:
        raise HTTPException(status_code=404, detail=f"图代 {generation} 未登记")
    edge_ids = [item.strip() for item in (edges or "").split(",") if item.strip()]
    selected = (
        [graph_versions.edge_by_id(generation, edge_id) for edge_id in edge_ids]
        if edge_ids else record.get("edges", [])
    )
    if any(edge is None for edge in selected):
        raise HTTPException(status_code=404, detail="指定的取样边不在该图代中")
    return evidence.assess_rollback(generation, [edge for edge in selected if edge])
