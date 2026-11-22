"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app import evidence
from app.services.base import BaseModuleService


class QualityAuditService(BaseModuleService):
    MODULE = "quality_audit"
    ENTITY = "审计报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编号", "审计范围", "实体准确率", "关系完整率", "冗余度", "更新时效", "合规结论", "审计时间"]
    REQUIRED_FIELDS = ["报告编号", "审计范围", "实体准确率"]
    STATUS_ORDER = ["待审计", "审计中", "已通过", "需整改"]
    ACTION_RULES = {"发起审计": "审计中", "确认通过": "已通过", "指派整改": "需整改"}
    NEGATIVE_ACTIONS = []

    # ===== 跨模块数据流实现 =====
    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        """报告详情附带可回放证据一览，避免只看到登记行看不到取证现场。"""
        entry = super().get_entry(entry_id)
        if entry is None:
            return None
        # 链路 L19: quality_audit → 审计证据（报告读取时带出封存证据与线索判断）
        entry = dict(entry)
        entry["evidence"] = evidence.report_evidence_summary(entry_id)
        return entry

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["cross_module_consumers"] = ["evidence_replay"]
        return base
