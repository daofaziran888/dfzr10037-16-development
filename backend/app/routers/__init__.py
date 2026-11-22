"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import evidence, resource

# 证据路由含固定前缀 /api/evidence，必须在通用资源路由（/api/{module}）之前注册。
ROUTERS = [evidence, resource]
