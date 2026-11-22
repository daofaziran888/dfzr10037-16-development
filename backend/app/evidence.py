"""审计证据封包与回放。

审计人员原先只能看到质量审计的报告登记行，没法还原取证现场。
本模块把「审计报告 + 实际取样边 + 图代引用」封成可回放的证据文件：

- 证据包分原包与补发勘误，重试同一请求编号只返回原包，不会把勘误混进来；
- 证据必须绑定可读取的取样图代与关系明细，图代回滚后拒绝回放，
  也不允许换另一张图完成同一次整改；
- 封包时同步写入复核记录与图谱演化待办，记录取样节点/边以及回滚影响。
"""
from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app import graph_versions
from app.store import store

# 至少两条相互印证的错连线索才足以发起复核；只有一条时先追查原案。
REVIEW_CLUE_THRESHOLD = 2

KIND_LABELS = {"original": "原包", "supplement": "补发勘误"}

_PACKAGES: dict[str, dict[str, Any]] = {}
_PACKAGE_BY_TOKEN: dict[str, str] = {}
_REVIEWS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _next_no(prefix: str, existing: dict[str, Any]) -> str:
    highest = 0
    for key in existing:
        tail = key.rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}-{highest + 1:04d}"


def _report_of(report_id: Any) -> dict[str, Any] | None:
    try:
        wanted = int(report_id)
    except (TypeError, ValueError):
        return None
    return store.find("quality_audit", wanted)


def _signature(
    report_id: int, generation: str, edge_ids: list[str], kind: str, supersedes: str | None
) -> str:
    return _digest({
        "report_id": report_id,
        "generation": generation,
        "edge_ids": sorted(edge_ids),
        "kind": kind,
        "supersedes": supersedes or "",
    })


def _covered_edges(report_id: int, generation: str) -> set[str]:
    covered: set[str] = set()
    for package in _PACKAGES.values():
        if package["report_id"] == report_id and package["generation"] == generation:
            covered.update(edge["edge_id"] for edge in package["edges"])
    return covered


def _seal_payload(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_no": package["evidence_no"],
        "kind": package["kind"],
        "report_id": package["report_id"],
        "report_no": package["report_no"],
        "generation": package["generation"],
        "generation_digest": package["generation_digest"],
        "sampled_at": package["sampled_at"],
        "request_token": package["request_token"],
        "supersedes": package["supersedes"],
        "node_ids": sorted(node["node_id"] for node in package["nodes"]),
        "edge_ids": sorted(edge["edge_id"] for edge in package["edges"]),
        "edges": sorted(
            (deepcopy(edge) for edge in package["edges"]), key=lambda item: item["edge_id"]
        ),
    }


def _package_public(package: dict[str, Any]) -> dict[str, Any]:
    """证据包对外视图：附带当次封存的摘要和当前是否可回放。"""
    view = deepcopy(package)
    generation = graph_versions.get_generation(package["generation"])
    state = generation.get("state") if generation else "missing"
    view["generation_state"] = state
    view["replayable"] = bool(
        generation
        and state == graph_versions.READABLE
        and graph_versions.generation_digest(package["generation"]) == package["generation_digest"]
        and _digest(_seal_payload(package)) == package["integrity"]
    )
    view["kind_label"] = KIND_LABELS.get(package["kind"], package["kind"])
    view["review"] = inspect_review(package["review_no"])
    view["clue_assessment"] = assess_clues(package["report_id"], package["generation"])
    return view


def _create_review(package: dict[str, Any]) -> dict[str, Any]:
    report = _report_of(package["report_id"]) or {}
    rollback_impact = assess_rollback(package["generation"], package["edges"])
    clue_assessment = assess_clues(package["report_id"], package["generation"])
    review_no = _next_no("REVI", _REVIEWS)
    evolve_row, todo_no = _write_evolve_todo(package, review_no, rollback_impact)
    review = {
        "review_no": review_no,
        "report_id": package["report_id"],
        "report_no": package["report_no"],
        "report_scope": report.get("审计范围", ""),
        "evidence_no": package["evidence_no"],
        "kind": package["kind"],
        "kind_label": KIND_LABELS.get(package["kind"], package["kind"]),
        "supersedes": package["supersedes"],
        "generation": package["generation"],
        "generation_digest": package["generation_digest"],
        "rollback_version": package["rollback_version"],
        "evidence_file": f"evidence://{package['evidence_no']}.json",
        "nodes": deepcopy(package["nodes"]),
        "edges": deepcopy(package["edges"]),
        "graph_evolve_todo": {
            "record_no": todo_no,
            "record_id": evolve_row.get("id") if evolve_row else None,
            "record_status": "已记录",
            "review_status": "待复核",
            "action": "回滚前复核取样节点与边",
        },
        "rollback_impact": rollback_impact,
        "clue_assessment": clue_assessment,
        "status": "待复核",
        "created_at": _now(),
    }
    _REVIEWS[review_no] = review
    package["review_no"] = review_no
    return review


def _write_evolve_todo(
    package: dict[str, Any], review_no: str, rollback_impact: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    rows = store.rows("graph_evolve")
    next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
    todo_no = f"GRAP-EVID-{next_id:04d}"
    row = {
        "id": next_id,
        "status": "已记录",
        "pending": True,
        "abnormal": rollback_impact["affected"],
        "记录编号": todo_no,
        "变更类型": "审计证据复核待办",
        "影响实体": "、".join(node["node_id"] for node in package["nodes"][:4]) or package["generation"],
        "影响关系": "、".join(edge["edge_id"] for edge in package["edges"]),
        "版本标签": package["generation"],
        "变更描述": (
            f"审计报告{package['report_no']}封存证据{package['evidence_no']}（{review_no}），"
            "回滚前复核该批取样节点与边"
        ),
        "回滚版本": package["rollback_version"],
        "演化时间": package["sampled_at"][:10],
        "证据编号": package["evidence_no"],
        "复核记录": review_no,
        "回滚影响": "受影响" if rollback_impact["affected"] else "不受影响",
    }
    rows.append(row)
    return row, todo_no


def _triple(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(edge.get("subject", "")),
        str(edge.get("predicate", "")),
        str(edge.get("object", "")),
    )


def assess_rollback(generation: str, edges: list[dict[str, Any]]) -> dict[str, Any]:
    """回溯到回滚目标图代的节点与边，判断回滚会不会影响这批样本。

    跨代比较用 subject/predicate/object 三元组，而不是关系记录号：
    同一段关系在父图代里可能换了编号，这不代表回滚会丢掉样本。
    """
    record = graph_versions.get_generation(generation)
    if record is None:
        return {
            "affected": True,
            "rollback_version": "",
            "reason": f"取样图代 {generation} 不存在，无法判断回滚影响",
            "missing_edges": [edge["edge_id"] for edge in edges],
            "changed_edges": [],
            "orphan_nodes": [],
        }
    rollback_version = str(record.get("parent") or "")
    target = graph_versions.get_generation(rollback_version)
    if target is None:
        return {
            "affected": True,
            "rollback_version": rollback_version,
            "reason": f"回滚目标图代 {rollback_version or '未登记'} 不存在，按受影响处理",
            "missing_edges": [edge["edge_id"] for edge in edges],
            "changed_edges": [],
            "orphan_nodes": [],
        }
    target_triples = {_triple(edge) for edge in target.get("edges", [])}
    target_by_id = {edge["edge_id"]: edge for edge in target.get("edges", [])}
    missing: list[str] = []
    changed: list[str] = []
    for edge in edges:
        if _triple(edge) in target_triples:
            continue
        counterpart = target_by_id.get(edge["edge_id"])
        if counterpart is not None and _triple(counterpart) != _triple(edge):
            changed.append(edge["edge_id"])
        else:
            missing.append(edge["edge_id"])
    sampled_node_ids = {
        node_id
        for edge in edges
        for node_id in (edge.get("subject"), edge.get("object"))
        if node_id
    }
    target_node_ids = {node["node_id"] for node in target.get("nodes", [])}
    target_edge_node_ids = {
        node_id
        for edge in target.get("edges", [])
        for node_id in (edge.get("subject"), edge.get("object"))
        if node_id
    }
    orphan_nodes = sorted(
        node_id for node_id in sampled_node_ids
        if node_id not in target_node_ids and node_id not in target_edge_node_ids
    )
    affected = bool(missing or changed)
    if affected:
        parts = []
        if missing:
            parts.append(f"回滚目标 {rollback_version} 不含取样关系 {'、'.join(missing)}")
        if changed:
            parts.append(f"回滚目标 {rollback_version} 中 {'、'.join(changed)} 的端点已变化")
        reason = "；".join(parts) + "；这批样本在回滚后不可回放"
    else:
        reason = f"取样关系在回滚目标 {rollback_version} 中按三元组仍完整存在，回滚不影响这批样本"
    return {
        "affected": affected,
        "rollback_version": rollback_version,
        "reason": reason,
        "missing_edges": missing,
        "changed_edges": changed,
        "orphan_nodes": orphan_nodes,
    }


def assess_clues(report_id: int, generation: str | None = None) -> dict[str, Any]:
    """按取样图代归并线索：同一次整改只认一个图代，回滚后代不能与旧代拼凑。"""
    scoped = [
        package for package in _PACKAGES.values()
        if package["report_id"] == report_id
        and (generation is None or package["generation"] == generation)
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for package in sorted(scoped, key=lambda item: item["evidence_no"]):
        groups.setdefault(package["generation"], []).append(package)

    summaries: list[dict[str, Any]] = []
    for generation_key, package_list in groups.items():
        edge_map: dict[str, dict[str, Any]] = {}
        for package in package_list:
            for edge in package["edges"]:
                decorated = deepcopy(edge)
                decorated["evidence_no"] = package["evidence_no"]
                decorated["kind"] = package["kind"]
                edge_map.setdefault(edge["edge_id"], decorated)
        readable = graph_versions.is_readable(generation_key)
        summaries.append({
            "generation": generation_key,
            "readable": readable,
            "clue_edges": list(edge_map.values()),
            "clue_count": len(edge_map),
            "package_count": len(package_list),
            "review_sufficient": len(edge_map) >= REVIEW_CLUE_THRESHOLD and readable,
        })

    actionable = sorted(
        summaries,
        key=lambda item: (item["review_sufficient"], item["readable"], item["clue_count"]),
        reverse=True,
    )
    best = actionable[0] if actionable else None
    readable = bool(best and best["readable"])
    clue_count = best["clue_count"] if best else 0
    sufficient = bool(best and best["review_sufficient"])
    if not scoped:
        next_action = "尚未封存审计证据：先取样并打包"
        generation_key = generation or ""
    elif not readable:
        generation_key = best["generation"] if best else (generation or "")
        next_action = (
            "关联图代已回滚，证据不能回放；须重新取样，"
            "不能以其他图代代替同一次整改"
        )
    elif sufficient:
        generation_key = best["generation"]
        next_action = (
            f"线索已足够发起复核：合并核对 {'、'.join(edge['edge_id'] for edge in best['clue_edges'])}，"
            "按复核记录追踪相关版本的节点与边"
        )
    else:
        generation_key = best["generation"]
        next_action = (
            f"原案可立即追查 {best['clue_edges'][0]['edge_id']}；"
            "等待补报第二条关系后再发起复核"
        )
    return {
        "report_id": report_id,
        "generation": generation_key,
        "generations": sorted(groups),
        "generation_summaries": summaries,
        "clue_count": clue_count,
        "clue_edges": best["clue_edges"] if best else [],
        "readable": readable,
        "can_investigate_immediately": clue_count >= 1 and readable,
        "review_sufficient": sufficient,
        "review_threshold": REVIEW_CLUE_THRESHOLD,
        "package_count": len(scoped),
        "next_action": next_action,
    }


def inspect_review(review_no: str) -> dict[str, Any] | None:
    review = _REVIEWS.get(str(review_no or ""))
    if review is None:
        return None
    view = deepcopy(review)
    generation = graph_versions.get_generation(review["generation"])
    state = generation.get("state") if generation else "missing"
    replayable = bool(generation and state == graph_versions.READABLE)
    view["generation_state"] = state
    view["replayable"] = replayable
    view["live_rollback_impact"] = assess_rollback(
        review["generation"], review["edges"]
    )
    view["clue_assessment"] = assess_clues(
        review["report_id"], review["generation"]
    )
    if review.get("status") == "已复核":
        view["status"] = "已复核"
        view["verdict"] = review.get("verdict")
        view["comment"] = review.get("remark", "")
        view["closed_at"] = review.get("closed_at", "")
        view["status_reason"] = (
            "复核已结案；图代现已被回滚，后续整改须重新取样"
            if not replayable else "复核已结案"
        )
    elif replayable:
        view["status"] = "待复核"
    else:
        view["status"] = "图代已回滚·需重新取样"
        view["status_reason"] = (
            f"关联图代 {review['generation']} 已回滚，证据不能回放，"
            "不得改用其他图代完成同一次整改"
        )
    return view


def list_reviews(report_id: int | None = None) -> list[dict[str, Any]]:
    reviews = [inspect_review(review_no) for review_no in sorted(_REVIEWS)]
    if report_id is not None:
        reviews = [review for review in reviews if review and review["report_id"] == report_id]
    return [review for review in reviews if review is not None]


def list_packages(report_id: int | None = None) -> list[dict[str, Any]]:
    packages = [_package_public(package) for _, package in sorted(_PACKAGES.items())]
    if report_id is not None:
        packages = [package for package in packages if package["report_id"] == report_id]
    return packages


def get_package(evidence_no: str) -> dict[str, Any] | None:
    package = _PACKAGES.get(str(evidence_no or ""))
    return _package_public(package) if package is not None else None


def _existing_token(token: str, signature: str) -> tuple[dict[str, Any] | None, str | None]:
    evidence_no = _PACKAGE_BY_TOKEN.get(token)
    if evidence_no is None:
        return None, None
    package = _PACKAGES[evidence_no]
    if package["request_signature"] != signature:
        return None, (
            f"请求编号 {token} 已用于另一份证据包 {evidence_no}，"
            "重试请沿用原请求内容，补发勘误请换新的请求编号"
        )
    return package, (
        f"请求编号 {token} 已封存为{package['kind_label']} {evidence_no}，本次为幂等重试"
    )


def create_package(
    *,
    report_id: Any,
    generation: Any,
    edge_ids: list[str],
    request_token: Any,
    kind: str = "original",
    supersedes: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    report = _report_of(report_id)
    if report is None:
        return None, f"审计报告 {report_id} 不存在，无法封存证据"
    try:
        report_key = int(report_id)
    except (TypeError, ValueError):
        return None, f"审计报告 {report_id} 编号不合法"

    generation_key = str(generation or "").strip()
    graph_record = graph_versions.get_generation(generation_key)
    if graph_record is None:
        return None, f"图代 {generation_key or '（空）'} 未登记，不能作为取证取样图"

    selected = [str(edge_id).strip() for edge_id in edge_ids if str(edge_id).strip()]
    if not selected:
        return None, "至少选择一条取样关系"
    if len(set(selected)) != len(selected):
        return None, "取样关系有重复，请去重后再打包"
    sampled_edges: list[dict[str, Any]] = []
    for edge_id in selected:
        edge = graph_versions.edge_by_id(generation_key, edge_id)
        if edge is None:
            return None, f"图代 {generation_key} 中不存在关系 {edge_id}"
        sampled_edges.append(edge)

    if kind not in KIND_LABELS:
        return None, f"证据包类型 {kind} 不受支持（original / supplement）"
    token = str(request_token or "").strip()
    if not token:
        token = _digest({"report": report_key, "generation": generation_key,
                         "edges": sorted(selected), "at": _now()})

    supersedes_key = str(supersedes or "").strip() or None
    if kind == "original":
        supersedes_key = None
    else:
        if supersedes_key is None:
            candidates = sorted(
                (
                    package["evidence_no"] for package in _PACKAGES.values()
                    if package["report_id"] == report_key
                    and package["generation"] == generation_key
                    and package["kind"] == "original"
                ),
                key=lambda item: int(item.rsplit("-", 1)[-1] or 0),
            )
            if not candidates:
                return None, "补发勘误必须先有同报告同图代的原包"
            supersedes_key = candidates[-1]
        original = _PACKAGES.get(supersedes_key)
        if original is None:
            return None, f"原包 {supersedes_key} 不存在，补发勘误无法关联"
        if original["report_id"] != report_key or original["generation"] != generation_key:
            return None, "补发勘误只能关联同一报告、同一取样图代的原包"
        if original["kind"] != "original":
            return None, f"{supersedes_key} 不是原包，勘误应关联原包"

    signature = _signature(report_key, generation_key, selected, kind, supersedes_key)
    with _LOCK:
        existing, reused_message = _existing_token(token, signature)
        if reused_message is not None:
            if existing is None:
                return None, reused_message
            view = _package_public(existing)
            view["idempotent_replay"] = True
            return view, reused_message

        # 回滚检查放在幂等判断之后：同一请求的中断重试仍能取回原包。
        if not graph_versions.is_readable(generation_key):
            return None, (
                f"图代 {generation_key} 当前状态为已回滚，不能作为可回放的取样图；"
                "请从新的可读取图代重新取样"
            )

        # 校验放在幂等判断之后：同一次重试即使图代中途回滚，也返回原包而不是报错。
        if kind == "supplement" and supersedes_key is not None:
            already = _covered_edges(report_key, generation_key)
            fresh = [edge_id for edge_id in selected if edge_id not in already]
            if not fresh:
                return None, (
                    f"补报的关系 {'、'.join(selected)} 均已归入取证文件，"
                    "请选择新补报的关系再封存勘误"
                )

        evidence_no = _next_no("EVID", _PACKAGES)
        package: dict[str, Any] = {
            "evidence_no": evidence_no,
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "report_id": report_key,
            "report_no": str(report.get("报告编号", "")),
            "record_no": str(report.get("报告编号", "")),
            "generation": generation_key,
            "generation_label": graph_record.get("label", ""),
            "generation_digest": graph_versions.generation_digest(generation_key),
            "rollback_version": str(graph_record.get("parent") or ""),
            "sampled_at": _now(),
            "request_token": token,
            "request_signature": signature,
            "supersedes": supersedes_key,
            "edges": sampled_edges,
            "nodes": graph_versions.nodes_for_edges(generation_key, sampled_edges),
        }
        package["integrity"] = _digest(_seal_payload(package))
        package["review_no"] = ""
        _PACKAGES[evidence_no] = package
        _PACKAGE_BY_TOKEN[token] = evidence_no
        _create_review(package)
        package["integrity"] = _digest(_seal_payload(package))
        view = _package_public(package)
        return view, f"已封存{KIND_LABELS[kind]} {evidence_no}，关联图代 {generation_key}"


def evidence_file(evidence_no: str) -> dict[str, Any] | None:
    """可下载、可回放的证据文件内容。"""
    package = _PACKAGES.get(str(evidence_no or ""))
    if package is None:
        return None
    review = inspect_review(package["review_no"])
    return {
        "format": "zhspace-audit-evidence/1",
        "evidence_no": package["evidence_no"],
        "kind": package["kind"],
        "kind_label": package["kind_label"],
        "supersedes": package["supersedes"],
        "report": {
            "id": package["report_id"],
            "report_no": package["report_no"],
        },
        "sampled_at": package["sampled_at"],
        "request_token": package["request_token"],
        "generation": {
            "generation": package["generation"],
            "label": package["generation_label"],
            "digest": package["generation_digest"],
            "rollback_version": package["rollback_version"],
            "state": (graph_versions.get_generation(package["generation"]) or {}).get("state", "missing"),
        },
        "nodes": deepcopy(package["nodes"]),
        "edges": deepcopy(package["edges"]),
        "review": review,
        "replay_policy": {
            "requires_generation": package["generation"],
            "requires_digest": package["generation_digest"],
            "allow_substitute_generation": False,
            "note": "关联图代被回滚时不得改用其他图代完成同一次整改",
        },
        "integrity": package["integrity"],
    }


def replay(evidence_no: str) -> dict[str, Any]:
    package = _PACKAGES.get(str(evidence_no or ""))
    if package is None:
        return {"ok": False, "status": "not_found", "evidence_no": evidence_no,
                "replayable": False, "immediate_generation": "",
                "reason": f"证据包 {evidence_no} 不存在"}
    sealed_ok = _digest(_seal_payload(package)) == package["integrity"]
    generation = graph_versions.get_generation(package["generation"])
    state = generation.get("state") if generation else "missing"
    live_digest = graph_versions.generation_digest(package["generation"])
    digest_ok = live_digest == package["generation_digest"]
    substitutions = _generations_containing(package)
    base = {
        "evidence_no": package["evidence_no"],
        "kind": package["kind"],
        "kind_label": package["kind_label"],
        "generation": package["generation"],
        "generation_state": state,
        "sealed_integrity_ok": sealed_ok,
        "generation_digest_match": digest_ok,
        "substitution_allowed": False,
        "substitute_candidates": substitutions,
        "review": inspect_review(package["review_no"]),
        "clue_assessment": assess_clues(package["report_id"], package["generation"]),
    }
    if generation is None:
        base.update({
            "ok": False, "status": "blocked", "replayable": False,
            "immediate_generation": "",
            "reason": f"关联图代 {package['generation']} 未登记，证据不能回放",
        })
        return base
    if state != graph_versions.READABLE:
        rollback = generation.get("rollback") or {}
        base.update({
            "ok": False, "status": "blocked", "replayable": False,
            "immediate_generation": "",
            "blocked_at_generation": package["generation"],
            "rollback": rollback,
            "reason": (
                f"关联图代 {package['generation']} 已回滚"
                f"（{rollback.get('record_no') or '演化记录'}，"
                f"回滚到 {rollback.get('rollback_version') or package['rollback_version']}）；"
                "该证据不能回放，也不会改用其他图代完成同一次整改"
            ),
        })
        return base
    if not sealed_ok:
        base.update({
            "ok": False, "status": "blocked", "replayable": False,
            "immediate_generation": "",
            "reason": "证据文件封存摘要不一致，按污染处理并拒绝回放",
        })
        return base
    if not digest_ok:
        base.update({
            "ok": False, "status": "blocked", "replayable": False,
            "immediate_generation": "",
            "reason": f"图代 {package['generation']} 内容已漂移，与封存摘要不一致，拒绝回放",
        })
        return base
    live_edges = []
    for edge in package["edges"]:
        live = graph_versions.edge_by_id(package["generation"], edge["edge_id"])
        if live is None:
            base.update({
                "ok": False, "status": "blocked", "replayable": False,
                "immediate_generation": "",
                "reason": f"取样关系 {edge['edge_id']} 已不在图代中，证据不完整",
            })
            return base
        live_edges.append(live)
    base.update({
        "ok": True,
        "status": "replayed",
        "replayable": True,
        "immediate_generation": package["generation"],
        "replayed_at": _now(),
        "nodes": graph_versions.nodes_for_edges(package["generation"], live_edges),
        "edges": live_edges,
        "reason": (
            f"证据立即重放到图代 {package['generation']}，"
            f"取样 {len(live_edges)} 条关系与封存文件一致"
        ),
    })
    return base


def verify_file(content: dict[str, Any]) -> dict[str, Any]:
    """重新打开已下载的证据文件：先验封存摘要，再看关联图代是否仍可读。

    离线文件里没有服务端的封包状态，因此以文件自带的 generation/digest/
    integrity 为准；发现图代已回滚时一律拒绝，绝不建议换图代顶替。
    """
    if not isinstance(content, dict) or content.get("format") != "zhspace-audit-evidence/1":
        return {
            "ok": False, "status": "invalid", "replayable": False,
            "reason": "不是可识别的审计证据文件（format 不匹配）",
        }
    evidence_no = str(content.get("evidence_no", ""))
    generation = content.get("generation") or {}
    generation_key = str(generation.get("generation", ""))
    sealed_integrity = str(content.get("integrity", ""))
    recorded = _PACKAGES.get(evidence_no)
    result: dict[str, Any] = {
        "evidence_no": evidence_no,
        "kind": content.get("kind"),
        "kind_label": KIND_LABELS.get(str(content.get("kind")), str(content.get("kind") or "")),
        "supersedes": content.get("supersedes"),
        "generation": generation_key,
        "recorded_on_server": recorded is not None,
        "substitution_allowed": False,
    }
    if recorded is not None:
        result["integrity_matches_server"] = recorded["integrity"] == sealed_integrity
        result["supersedes_matches_server"] = recorded["supersedes"] == (content.get("supersedes") or None)
    else:
        result["integrity_matches_server"] = None
        result["supersedes_matches_server"] = None
    live = graph_versions.get_generation(generation_key)
    if live is None:
        result.update({
            "ok": False, "status": "blocked", "replayable": False,
            "reason": f"文件关联的图代 {generation_key or '（空）'} 未登记，不能回放",
        })
        return result
    result["generation_state"] = live.get("state")
    if live.get("state") != graph_versions.READABLE:
        rollback = live.get("rollback") or {}
        result.update({
            "ok": False, "status": "blocked", "replayable": False,
            "rollback": rollback,
            "substitute_candidates": _generations_containing_edge_ids(
                [edge.get("edge_id", "") for edge in content.get("edges", [])],
                exclude=generation_key,
            ),
            "reason": (
                f"重新打开文件时发现关联图代 {generation_key} 已回滚"
                f"（{rollback.get('record_no') or '演化记录'}，"
                f"回滚到 {rollback.get('rollback_version') or generation.get('rollback_version')}）；"
                "该文件不能回放，也不能改用另一图代完成同一次整改"
            ),
        })
        return result
    if graph_versions.generation_digest(generation_key) != generation.get("digest"):
        result.update({
            "ok": False, "status": "blocked", "replayable": False,
            "reason": f"图代 {generation_key} 当前内容与文件记录的摘要不一致，拒绝回放",
        })
        return result
    missing = [
        edge.get("edge_id", "") for edge in content.get("edges", [])
        if graph_versions.edge_by_id(generation_key, str(edge.get("edge_id", ""))) is None
    ]
    if missing:
        result.update({
            "ok": False, "status": "blocked", "replayable": False,
            "reason": f"文件中的关系 {'、'.join(missing)} 已不在图代 {generation_key} 中",
        })
        return result
    result.update({
        "ok": True,
        "status": "replayed",
        "replayable": True,
        "immediate_generation": generation_key,
        "edges": [
            graph_versions.edge_by_id(generation_key, str(edge.get("edge_id", "")))
            for edge in content.get("edges", [])
        ],
        "reason": f"文件与图代 {generation_key} 一致，可立即重放",
    })
    return result


def _generations_containing_edge_ids(edge_ids: list[str], exclude: str) -> list[str]:
    wanted = sorted(item for item in edge_ids if item)
    result: list[str] = []
    for summary in graph_versions.list_generations():
        candidate = summary["generation"]
        if candidate == exclude or summary["state"] != graph_versions.READABLE:
            continue
        record = graph_versions.get_generation(candidate) or {}
        present = {edge["edge_id"] for edge in record.get("edges", [])}
        if wanted and all(edge_id in present for edge_id in wanted):
            result.append(candidate)
    return result


def _generations_containing(package: dict[str, Any]) -> list[str]:
    """列出同样包含这批关系记录号的其他可读图代，仅用于说明，永远不允许替代。"""
    return _generations_containing_edge_ids(
        [edge["edge_id"] for edge in package["edges"]],
        exclude=package["generation"],
    )


def apply_rollback(generation: str, entry: dict[str, Any]) -> list[str]:
    """图谱演化执行回滚后同步复核记录状态，返回受影响的复核编号。"""
    affected: list[str] = []
    generation_key = str(generation or "").strip()
    if not generation_key:
        return affected
    for review_no, review in _REVIEWS.items():
        if review["generation"] != generation_key:
            continue
        review["status"] = "图代已回滚·需重新取样"
        review["rollback_observed"] = {
            "record_id": entry.get("id"),
            "record_no": entry.get("记录编号", ""),
            "rollback_version": entry.get("回滚版本", ""),
            "rolled_back_at": entry.get("演化时间", ""),
            "reason": entry.get("变更描述", ""),
        }
        affected.append(review_no)
    return affected


def close_review(review_no: str, verdict: str, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """复核结论：只有证据仍可回放、且线索足够时才允许结案。"""
    review = _REVIEWS.get(str(review_no or ""))
    if review is None:
        return None, f"复核记录 {review_no} 不存在"
    verdict_key = str(verdict or "").strip()
    if verdict_key not in ("确认错连", "排除错连"):
        return None, f"复核结论「{verdict_key}」不受支持（确认错连 / 排除错连）"
    if review.get("status") == "已复核":
        return None, f"复核记录 {review['review_no']} 已结案（{review.get('verdict')}），不能重复结案"
    live = inspect_review(review["review_no"]) or {}
    if not live.get("replayable"):
        return None, (
            f"复核记录 {review['review_no']} 关联图代 {review['generation']} 已回滚，"
            "证据不可回放；须重新取样后再复核，不能直接结案"
        )
    if not live["clue_assessment"]["review_sufficient"]:
        return None, (
            f"线索尚不足以发起复核（{live['clue_assessment']['clue_count']}"
            f"/{live['clue_assessment']['review_threshold']}），先继续追查"
        )
    review["status"] = "已复核"
    review["verdict"] = verdict_key
    review["remark"] = remark or ""
    review["closed_at"] = _now()
    for row in store.rows("graph_evolve"):
        if row.get("复核记录") == review["review_no"]:
            row["status"] = "已评审"
            row["pending"] = False
    return inspect_review(review["review_no"]), f"复核记录 {review['review_no']} 已结案：{verdict_key}"


def report_evidence_summary(report_id: int) -> dict[str, Any]:
    packages = list_packages(report_id)
    reviews = list_reviews(report_id)
    return {
        "evidence_count": len(packages),
        "packages": [
            {
                "evidence_no": package["evidence_no"],
                "kind": package["kind"],
                "kind_label": package["kind_label"],
                "generation": package["generation"],
                "replayable": package["replayable"],
                "supersedes": package["supersedes"],
            }
            for package in packages
        ],
        "reviews": [
            {
                "review_no": review["review_no"],
                "status": review["status"],
                "evidence_no": review["evidence_no"],
            }
            for review in reviews
        ],
        "clue_assessment": assess_clues(report_id),
    }
