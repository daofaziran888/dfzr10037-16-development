# 知谱工坊 · 元数据知识图谱与智能探查平台

面向元数据实体抽取、关系建模、知识图谱构建、图谱搜索、实体链接、消岐融合、路径推理、子图探查与图谱演化追踪的元数据知识图谱与智能探查平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实体抽取 | `entity_extract` | 实体定义 | 实体编号、实体类型、数据来源 |
| 关系建模 | `relation_model` | 关系类型 | 关系编号、关系名、主体类型 |
| 图谱构建 | `graph_build` | 构建任务 | 任务编号、数据范围、图类型 |
| 图谱搜索 | `graph_search` | 搜索模板 | 模板编号、搜索类型、查询语法 |
| 实体链接 | `entity_link` | 链接规则 | 规则编号、源实体、目标实体 |
| 消岐融合 | `disambig` | 消岐配置 | 配置编号、歧义类型、候选实体 |
| 路径推理 | `path_reason` | 推理任务 | 任务编号、起始实体、目标实体 |
| 子图探查 | `subgraph_exp` | 探查配置 | 配置编号、探查种子、扩展策略 |
| 图谱演化 | `graph_evolve` | 演化记录 | 记录编号、变更类型、影响实体 |
| 质量审计 | `quality_audit` | 审计报告 | 报告编号、审计范围、实体准确率 |
| 元数据标注 | `metadata_tag` | 标注任务 | 任务编号、标注对象、标注类型 |
| 分类体系 | `taxonomy` | 分类树 | 分类编号、分类名称、父分类 |
| 查询缓存 | `query_cache` | 缓存条目 | 条目编号、查询模板、结果哈希 |
| 图谱索引 | `graph_index` | 索引配置 | 索引编号、目标属性、索引类型 |
| 数据血缘 | `data_lineage` | 血缘链路 | 链路编号、上游实体、下游实体 |
| 推荐引擎 | `reco_engine` | 推荐规则 | 规则编号、推荐场景、源实体 |
| 实体归并 | `entity_merge` | 归并任务 | 任务编号、源实体、目标实体 |
| 批量导入 | `batch_load` | 导入任务 | 任务编号、数据文件、目标图谱 |
| 图谱报告 | `report_gen` | 图谱报表 | 报告编号、报告类型、数据范围 |
| 图谱比对 | `graph_compare` | 比对任务 | 任务编号、基准图、对比图 |
| 定时任务 | `schedule_job` | 调度作业 | 作业编号、任务类型、关联图谱 |
| 治理看板 | `govern_dash` | 治理指标 | 指标编号、指标名称、评估对象 |

## 审计证据回放

质量审计的报告登记行之外，新增可打包、可下载、可回放的审计证据文件，
入口在「质量审计」工作台底部（也可用接口直接操作）。

| 接口 | 说明 |
| --- | --- |
| `GET /api/evidence/generations` | 可取样图代与节点/边规模，含回滚状态 |
| `GET /api/evidence/generations/{g}` | 图代明细（节点、关系、内容摘要） |
| `POST /api/evidence/packages` | 从取样图代 + 关系编号封存原包 / 补发勘误 |
| `GET /api/evidence/{no}/file` | 下载证据文件（含节点、关系、复核记录与回放策略） |
| `GET /api/evidence/{no}/replay` | 重放，返回「立即重放到哪一代」 |
| `POST /api/evidence/verify-file` | 重新打开已下载文件时校验图代是否仍可回放 |
| `GET /api/evidence/reviews` | 复核记录与图谱演化待办 |
| `POST /api/evidence/reviews/{no}/close` | 复核结案（证据不可回放或线索不足时拒绝） |
| `GET /api/evidence/clues/{report_id}` | 线索充分性与下一步动作 |
| `GET /api/evidence/rollback-impact/{g}?edges=...` | 回溯回滚目标图代的节点/边，判断是否影响样本 |

约束：

- 证据包必须绑定一个可读取的取样图代与至少一条关系明细，不打包「当前图」。
- 原包与补发勘误分开编号，勘误用 `supersedes` 指向原包；同一请求编号重试
  只返回原包，不会把勘误混进来。
- 图代被「图谱演化 · 执行回滚」置为不可读后，引用它的证据一律拒绝回放，
  即使别的图代存在同名关系记录也不允许顶替（`substitution_allowed` 恒为 false）。
- 封包同时写入复核记录与图谱演化待办，记录取样节点/边、回滚目标与影响判断。
- 只有一条错连线索时原案可立即追查；补报第二条关系后线索才足够发起复核。

示例（服务启动后）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/evidence/packages \
  -H 'Content-Type: application/json' \
  -d '{"report_id":2,"generation":"g5","edge_ids":["e-g5-01"],"request_token":"manual-1"}'
```

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
