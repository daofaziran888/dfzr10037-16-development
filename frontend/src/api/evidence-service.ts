import { request } from './client'

export type GenerationSummary = {
  generation: string
  parent: string | null
  label: string
  state: 'readable' | 'rolled_back' | string
  created_at: string
  node_count: number
  edge_count: number
  rollback: Record<string, string> | null
}

export type EvidenceEdge = {
  edge_id: string
  subject: string
  predicate: string
  object: string
  source_field: string
  target_field: string
  confidence: number
  mislinked: boolean
  note: string
}

export type EvidenceNode = {
  node_id: string
  label: string
  node_type: string
  source: string
}

export type ClueAssessment = {
  report_id: number
  generation: string
  generations: string[]
  clue_count: number
  clue_edges: EvidenceEdge[]
  readable: boolean
  can_investigate_immediately: boolean
  review_sufficient: boolean
  review_threshold: number
  package_count: number
  next_action: string
}

export type RollbackImpact = {
  affected: boolean
  rollback_version: string
  reason: string
  missing_edges: string[]
}

export type ReviewRecord = {
  review_no: string
  evidence_no: string
  kind: string
  kind_label: string
  status: string
  status_reason?: string
  generation: string
  generation_state: string
  replayable: boolean
  graph_evolve_todo: { record_no: string; record_id: number | null; record_status: string }
  rollback_impact: RollbackImpact
  live_rollback_impact: RollbackImpact
  clue_assessment: ClueAssessment
}

export type EvidencePackage = {
  evidence_no: string
  kind: 'original' | 'supplement'
  kind_label: string
  report_id: number
  report_no: string
  generation: string
  generation_state: string
  replayable: boolean
  supersedes: string | null
  sampled_at: string
  request_token: string
  edges: EvidenceEdge[]
  nodes: EvidenceNode[]
  review_no: string
  review: ReviewRecord
  clue_assessment: ClueAssessment
  idempotent_replay?: boolean
}

export type ReplayResult = {
  ok: boolean
  status: string
  evidence_no: string
  kind_label?: string
  generation?: string
  immediate_generation: string
  replayable: boolean
  substitution_allowed: boolean
  substitute_candidates: string[]
  reason: string
  nodes?: EvidenceNode[]
  edges?: EvidenceEdge[]
  clue_assessment?: ClueAssessment
}

export type PackageResult = {
  ok: boolean
  message: string
  package?: EvidencePackage
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((payload as { detail?: string }).detail ?? fallback)
  }
  return payload as T
}

export async function listGenerations(): Promise<GenerationSummary[]> {
  const response = await request('/api/evidence/generations')
  const payload = await readJson<{ items: GenerationSummary[] }>(response, '图代列表读取失败')
  return payload.items
}

export async function getGeneration(generation: string): Promise<{
  generation: string
  state: string
  digest: string
  edges: EvidenceEdge[]
  nodes: EvidenceNode[]
  rollback: Record<string, string> | null
}> {
  return await readJson(await request(`/api/evidence/generations/${generation}`), '图代明细读取失败')
}

export async function listPackages(reportId: number): Promise<EvidencePackage[]> {
  const response = await request(`/api/evidence?report_id=${reportId}`)
  const payload = await readJson<{ items: EvidencePackage[] }>(response, '证据包列表读取失败')
  return payload.items
}

export async function listReviews(reportId: number): Promise<ReviewRecord[]> {
  const response = await request(`/api/evidence/reviews?report_id=${reportId}`)
  const payload = await readJson<{ items: ReviewRecord[] }>(response, '复核记录读取失败')
  return payload.items
}

export async function getClues(reportId: number): Promise<ClueAssessment> {
  return await readJson(await request(`/api/evidence/clues/${reportId}`), '线索判断读取失败')
}

export async function createPackage(payload: {
  report_id: number
  generation: string
  edge_ids: string[]
  request_token: string
  kind: 'original' | 'supplement'
  supersedes?: string | null
}): Promise<PackageResult> {
  return await readJson(
    await request('/api/evidence/packages', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    '证据封包失败',
  )
}

export async function downloadEvidenceFile(evidenceNo: string): Promise<Blob> {
  const response = await request(`/api/evidence/${evidenceNo}/file`)
  if (!response.ok) throw new Error('证据文件下载失败')
  return await response.blob()
}

export async function replayEvidence(evidenceNo: string): Promise<ReplayResult> {
  return await readJson(await request(`/api/evidence/${evidenceNo}/replay`), '证据回放失败')
}

export async function verifyEvidenceFile(
  content: Record<string, unknown>,
): Promise<ReplayResult> {
  return await readJson(
    await request('/api/evidence/verify-file', {
      method: 'POST',
      body: JSON.stringify(content),
    }),
    '证据文件校验失败',
  )
}

export async function closeReview(
  reviewNo: string,
  verdict: string,
): Promise<{ ok: boolean; message: string; review: ReviewRecord }> {
  return await readJson(
    await request(`/api/evidence/reviews/${reviewNo}/close`, {
      method: 'POST',
      body: JSON.stringify({ verdict }),
    }),
    '复核结案失败',
  )
}
