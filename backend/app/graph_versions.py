"""图代注册表：保存可读取的图谱版本快照，供审计取证与回放引用。

审计证据必须绑定到具体图代（例如 g5），而不是"当前图"。
图代一旦被图谱演化回滚，状态会切换为 rolled_back；证据回放据此拒绝
拿别的图代顶替，避免用另一张图完成同一次整改。
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

READABLE = "readable"
ROLLED_BACK = "rolled_back"


def _edge(
    edge_id: str,
    subject: str,
    predicate: str,
    obj: str,
    source_field: str,
    target_field: str,
    confidence: float,
    *,
    mislinked: bool = False,
    note: str = "",
) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "source_field": source_field,
        "target_field": target_field,
        "confidence": confidence,
        "mislinked": mislinked,
        "note": note,
    }


def _node(node_id: str, label: str, node_type: str, source: str) -> dict[str, Any]:
    return {"node_id": node_id, "label": label, "node_type": node_type, "source": source}


_G5_NODES = [
    _node("n-order-id", "订单表.order_id", "字段", "供应链订单库"),
    _node("n-order-payno", "订单表.pay_no", "字段", "供应链订单库"),
    _node("n-cust-id", "客户表.customer_id", "字段", "客户主数据"),
    _node("n-cust-phone", "客户表.phone_no", "字段", "客户主数据"),
    _node("n-pay-payno", "支付流水表.pay_no", "字段", "支付流水库"),
]

_G5_EDGES = [
    _edge(
        "e-g5-01", "n-order-id", "字段映射", "n-cust-id",
        "order_id", "customer_id", 0.38, mislinked=True,
        note="订单号被错连到客户编号，调查组已选中作为取证样本",
    ),
    _edge(
        "e-g5-02", "n-order-payno", "字段映射", "n-cust-phone",
        "pay_no", "phone_no", 0.36, mislinked=True,
        note="同一批错连中的第二条关系，取样后由他人补报",
    ),
    _edge(
        "e-g5-03", "n-order-payno", "数据血缘", "n-pay-payno",
        "pay_no", "pay_no", 0.93,
        note="正确血缘，用于对照",
    ),
]

_GENERATIONS: dict[str, dict[str, Any]] = {
    "g3": {
        "generation": "g3",
        "parent": "g2",
        "label": "2026-09 供应链字段映射初版",
        "created_at": "2026-09-12T09:00:00+00:00",
        "state": READABLE,
        "nodes": _G5_NODES[:3],
        "edges": [
            _edge("e-g3-01", "n-order-id", "字段映射", "n-cust-id",
                  "order_id", "customer_id", 0.61,
                  note="旧版映射，置信度偏低"),
        ],
    },
    "g4": {
        "generation": "g4",
        "parent": "g3",
        "label": "2026-10 客户主数据字段修订",
        "created_at": "2026-10-18T14:20:00+00:00",
        "state": READABLE,
        "nodes": _G5_NODES[:4],
        "edges": [
            _edge("e-g4-01", "n-order-id", "字段映射", "n-cust-id",
                  "order_id", "customer_id", 0.55,
                  note="g4 只有一条订单-客户映射，与 g5 样本不同"),
            _edge("e-g4-02", "n-order-payno", "数据血缘", "n-pay-payno",
                  "pay_no", "pay_no", 0.88,
                  note="g4 不含 pay_no→phone_no 的错连"),
        ],
    },
    "g5": {
        "generation": "g5",
        "parent": "g4",
        "label": "2026-11 供应链字段映射修订",
        "created_at": "2026-11-10T10:30:00+00:00",
        "state": READABLE,
        "nodes": _G5_NODES,
        "edges": _G5_EDGES,
    },
    # g6 是复核后重建的图代，沿用了 g5 的关系记录编号（e-g5-02）。
    # 它看似"包含同一批样本"，但绝不能拿来顶替已回滚的 g5 完成同一次整改。
    "g6": {
        "generation": "g6",
        "parent": "g5",
        "label": "2026-11 复核重建（沿用 g5 关系记录）",
        "created_at": "2026-11-20T16:00:00+00:00",
        "state": READABLE,
        "nodes": _G5_NODES + [
            _node("n-cust-name", "客户表.customer_name", "字段", "客户主数据"),
        ],
        "edges": [
            _edge("e-g6-01", "n-order-id", "字段映射", "n-order-id",
                  "order_id", "order_id", 0.99,
                  note="复核后改为订单号自映射，不再错连客户编号"),
            _edge("e-g5-02", "n-order-payno", "字段映射", "n-cust-phone",
                  "pay_no", "phone_no", 0.36, mislinked=True,
                  note="沿用 g5 记录，尚未裁决，不能因此视为同一次取样"),
            _edge("e-g6-02", "n-order-payno", "数据血缘", "n-pay-payno",
                  "pay_no", "pay_no", 0.95,
                  note="正确血缘"),
        ],
    },
}


def list_generations() -> list[dict[str, Any]]:
    """列出图代摘要，供取证界面选择与核对。"""
    result: list[dict[str, Any]] = []
    for generation, record in _GENERATIONS.items():
        result.append({
            "generation": record["generation"],
            "parent": record.get("parent"),
            "label": record.get("label", ""),
            "state": record.get("state", READABLE),
            "created_at": record.get("created_at", ""),
            "node_count": len(record.get("nodes", [])),
            "edge_count": len(record.get("edges", [])),
            "rollback": deepcopy(record.get("rollback")),
        })
    return result


def get_generation(generation: str) -> dict[str, Any] | None:
    record = _GENERATIONS.get(str(generation or "").strip())
    return deepcopy(record) if record is not None else None


def is_readable(generation: str) -> bool:
    record = _GENERATIONS.get(str(generation or "").strip())
    return bool(record and record.get("state") == READABLE)


def mark_rolled_back(generation: str, record: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """把图代标记为已回滚；重复回滚保持幂等。"""
    record_key = str(generation or "").strip()
    target = _GENERATIONS.get(record_key)
    if target is None:
        return None
    if target.get("state") != ROLLED_BACK:
        target["state"] = ROLLED_BACK
        target["rollback"] = {
            "record_id": (record or {}).get("id"),
            "record_no": (record or {}).get("记录编号", ""),
            "rollback_version": (record or {}).get("回滚版本", ""),
            "rolled_back_at": (record or {}).get("演化时间", ""),
            "reason": (record or {}).get("变更描述", ""),
        }
    return deepcopy(target)


def edge_by_id(generation: str, edge_id: str) -> dict[str, Any] | None:
    record = _GENERATIONS.get(str(generation or "").strip())
    if record is None:
        return None
    for edge in record.get("edges", []):
        if edge.get("edge_id") == edge_id:
            decorated = deepcopy(edge)
            decorated["generation"] = record["generation"]
            decorated["subject_node"] = _node_by_id(record, edge.get("subject"))
            decorated["object_node"] = _node_by_id(record, edge.get("object"))
            return decorated
    return None


def _node_by_id(record: dict[str, Any], node_id: str | None) -> dict[str, Any] | None:
    for node in record.get("nodes", []):
        if node.get("node_id") == node_id:
            return deepcopy(node)
    return None


def nodes_for_edges(generation: str, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    record = _GENERATIONS.get(str(generation or "").strip())
    if record is None:
        return []
    wanted: list[str] = []
    for edge in edges:
        for key in ("subject", "object"):
            node_id = edge.get(key)
            if node_id and node_id not in wanted:
                wanted.append(node_id)
    return [
        deepcopy(node)
        for node_id in wanted
        for node in record.get("nodes", [])
        if node.get("node_id") == node_id
    ]


def generation_digest(generation: str) -> str:
    """节点与边的内容摘要，用于发现同代内容漂移（不含回滚状态）。"""
    record = _GENERATIONS.get(str(generation or "").strip())
    if record is None:
        return ""
    payload = {
        "generation": record.get("generation"),
        "nodes": record.get("nodes", []),
        "edges": record.get("edges", []),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
