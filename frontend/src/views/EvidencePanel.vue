<template>
  <section class="evidence-panel" data-testid="evidence-panel" :data-report-id="reportId">
    <header class="evidence-head">
      <div>
        <h3>取证文件 · 可回放审计证据</h3>
        <p class="evidence-desc">
          从实际审计样本与图代引用打包，文件内嵌取样节点、关系明细与复核记录；
          图代回滚后不能拿另一张图完成同一次整改。
        </p>
      </div>
      <button class="btn ghost" data-testid="evidence-refresh" type="button" :disabled="busy" @click="reloadAll">
        刷新
      </button>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="evidence-error">{{ errorMessage }}</p>
    <p v-else-if="notice" class="evidence-notice" data-testid="evidence-notice">{{ notice }}</p>

    <div class="evidence-grid">
      <article class="evidence-card">
        <h4>1. 选择取样图代</h4>
        <label class="evidence-field">
          <span>图代</span>
          <select v-model="selectedGeneration" data-testid="evidence-generation" @change="loadGeneration">
            <option v-for="item in generations" :key="item.generation" :value="item.generation">
              {{ item.generation }} · {{ item.label }}
              {{ item.state === 'rolled_back' ? '（已回滚）' : '' }}
            </option>
          </select>
        </label>
        <p v-if="currentGeneration" class="evidence-hint" data-testid="evidence-generation-state">
          状态：{{ currentGeneration.state === 'readable' ? '可读取' : '已回滚 · 不可回放' }}；
          节点 {{ currentGeneration.nodes.length }}，关系 {{ currentGeneration.edges.length }}
        </p>
        <p v-if="currentGeneration?.rollback" class="evidence-warn" data-testid="evidence-rollback-note">
          回滚记录 {{ currentGeneration.rollback.record_no || '—' }}
          → 回到 {{ currentGeneration.rollback.rollback_version || '—' }}
        </p>
      </article>

      <article class="evidence-card">
        <h4>2. 勾选取样关系</h4>
        <ul class="evidence-edges">
          <li v-for="edge in currentGeneration?.edges ?? []" :key="edge.edge_id">
            <label :data-testid="`evidence-edge-${edge.edge_id}`">
              <input v-model="selectedEdges" type="checkbox" :value="edge.edge_id" />
              <span class="edge-id">{{ edge.edge_id }}</span>
              <span>{{ edge.subject }} —{{ edge.predicate }}→ {{ edge.object }}</span>
              <span class="edge-meta">置信 {{ edge.confidence }}</span>
              <span v-if="edge.mislinked" class="edge-flag">错连</span>
            </label>
          </li>
          <li v-if="!(currentGeneration?.edges ?? []).length" class="evidence-hint">该图代没有可取样关系</li>
        </ul>
      </article>

      <article class="evidence-card">
        <h4>3. 封存与回放</h4>
        <label class="evidence-field">
          <span>包类型</span>
          <select v-model="packageKind" data-testid="evidence-kind">
            <option value="original">原包</option>
            <option value="supplement">补发勘误</option>
          </select>
        </label>
        <label v-if="packageKind === 'supplement'" class="evidence-field">
          <span>关联原包</span>
          <select v-model="supersedesNo" data-testid="evidence-supersedes">
            <option v-for="item in originalPackages" :key="item.evidence_no" :value="item.evidence_no">
              {{ item.evidence_no }}（{{ item.generation }}）
            </option>
          </select>
        </label>
        <button class="btn primary" data-testid="evidence-package" type="button" :disabled="busy" @click="packageEvidence">
          {{ busy ? '处理中…' : '打包导出证据文件' }}
        </button>
        <p class="evidence-hint">
          请求编号 <code data-testid="evidence-token">{{ requestToken }}</code>，
          中断后重试沿用编号即可取回原包。
        </p>
        <label class="evidence-reopen">
          <span>重新打开已下载文件</span>
          <input
            data-testid="evidence-reopen-file"
            type="file"
            accept="application/json,.json"
            @change="reopenFile"
          />
        </label>
      </article>
    </div>

    <section class="evidence-block">
      <h4>线索判断</h4>
      <p data-testid="evidence-clue-summary">
        已封存 {{ clues?.clue_count ?? 0 }} 条线索 / 复核门槛 {{ clues?.review_threshold ?? 2 }}：
        {{ clues?.next_action ?? '尚未取样' }}
      </p>
      <p v-if="clues?.package_count" class="evidence-hint">
        当前涉及图代 {{ clues?.generations?.join('、') || '—' }}；
        {{ clues?.review_sufficient ? '可发起复核' : '暂不足以发起复核' }}
      </p>
    </section>

    <section class="evidence-block">
      <h4>已封存的证据文件</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>证据编号</th><th>类型</th><th>取样图代</th><th>关系</th>
            <th>复核记录</th><th>回放</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in packages" :key="item.evidence_no" :data-testid="`evidence-row-${item.evidence_no}`">
            <td>{{ item.evidence_no }}</td>
            <td>{{ item.kind_label }}{{ item.supersedes ? `（关联 ${item.supersedes}）` : '' }}</td>
            <td>{{ item.generation }} · {{ item.generation_state === 'readable' ? '可读' : '已回滚' }}</td>
            <td>{{ item.edges.map((edge) => edge.edge_id).join('、') }}</td>
            <td>{{ item.review_no }} · {{ item.review.status }}</td>
            <td>{{ item.replayable ? '可回放' : '不可回放' }}</td>
            <td class="row-actions">
              <button class="link" :data-testid="`evidence-download-${item.evidence_no}`" type="button" @click="download(item.evidence_no)">
                下载文件
              </button>
              <button class="link" :data-testid="`evidence-replay-${item.evidence_no}`" type="button" @click="replay(item.evidence_no)">
                重放
              </button>
              <button class="link" :data-testid="`evidence-close-${item.evidence_no}`" type="button" @click="close(item.review_no)">
                结案
              </button>
            </td>
          </tr>
          <tr v-if="!packages.length">
            <td colspan="7" class="empty-state">该报告还没有封存取证文件，先在上方取样打包</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="lastReplay" class="evidence-block" data-testid="evidence-replay-result">
      <h4>{{ lastReplaySource === 'file' ? '重新打开文件的结果' : '最近一次回放' }}</h4>
      <p :class="lastReplay.ok ? 'evidence-hint' : 'error-text'">{{ lastReplay.reason }}</p>
      <p v-if="lastReplay.ok" class="evidence-hint">
        立即重放到 <strong data-testid="evidence-replayed-generation">{{ lastReplay.immediate_generation }}</strong>
        ，取样节点 {{ lastReplay.nodes?.length ?? 0 }}，关系 {{ lastReplay.edges?.length ?? 0 }}。
      </p>
      <p v-else-if="lastReplay.substitute_candidates?.length" class="evidence-warn">
        其他图代也包含这些边（{{ lastReplay.substitute_candidates.join('、') }}），
        但同一整改不得改用它们顶替。
      </p>
    </section>

    <section v-if="reviews.length" class="evidence-block">
      <h4>复核记录与图谱演化待办</h4>
      <ul class="evidence-reviews">
        <li v-for="item in reviews" :key="item.review_no" :data-testid="`evidence-review-${item.review_no}`">
          <strong>{{ item.review_no }}</strong>
          · {{ item.evidence_no }} · {{ item.status }}
          <template v-if="item.status_reason"> · {{ item.status_reason }}</template>
          <p class="evidence-hint">
            演化待办 {{ item.graph_evolve_todo.record_no }}（{{ item.graph_evolve_todo.record_status }}）；
            回滚到 {{ item.live_rollback_impact.rollback_version }}：
            {{ item.live_rollback_impact.reason }}
          </p>
        </li>
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  closeReview,
  createPackage,
  downloadEvidenceFile,
  getClues,
  getGeneration,
  listGenerations,
  listPackages,
  listReviews,
  replayEvidence,
  verifyEvidenceFile,
} from '@/api/evidence-service'
import type {
  ClueAssessment,
  EvidenceEdge,
  EvidenceNode,
  EvidencePackage,
  GenerationSummary,
  ReplayResult,
  ReviewRecord,
} from '@/api/evidence-service'

const props = defineProps<{ reportId: number }>()

const generations = ref<GenerationSummary[]>([])
const selectedGeneration = ref('')
const selectedEdges = ref<string[]>([])
const currentGeneration = ref<{
  generation: string
  state: string
  edges: EvidenceEdge[]
  nodes: EvidenceNode[]
  rollback: Record<string, string> | null
} | null>(null)
const packageKind = ref<'original' | 'supplement'>('original')
const supersedesNo = ref('')
const requestToken = ref(newRequestToken())
const packages = ref<EvidencePackage[]>([])
const reviews = ref<ReviewRecord[]>([])
const clues = ref<ClueAssessment | null>(null)
const lastReplay = ref<ReplayResult | null>(null)
const lastReplaySource = ref<'server' | 'file'>('server')
const notice = ref('')
const errorMessage = ref('')
const busy = ref(false)

const originalPackages = computed(() =>
  packages.value.filter(
    (item) => item.kind === 'original' && item.generation === selectedGeneration.value,
  ),
)

function newRequestToken(): string {
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function describe(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

async function reloadAll() {
  errorMessage.value = ''
  notice.value = ''
  try {
    const [generationList, packageList, reviewList, clueSummary] = await Promise.all([
      listGenerations(),
      listPackages(props.reportId),
      listReviews(props.reportId),
      getClues(props.reportId),
    ])
    generations.value = generationList
    packages.value = packageList
    reviews.value = reviewList
    clues.value = clueSummary
    const readable = generationList.filter((item) => item.state === 'readable')
    if (!selectedGeneration.value || !generationList.some((item) => item.generation === selectedGeneration.value)) {
      selectedGeneration.value = readable.at(-1)?.generation ?? generationList.at(-1)?.generation ?? ''
    }
    if (!originalPackages.value.some((item) => item.evidence_no === supersedesNo.value)) {
      supersedesNo.value = originalPackages.value.at(-1)?.evidence_no ?? ''
    }
    await loadGeneration()
  } catch (error) {
    errorMessage.value = describe(error, '取证信息读取失败')
  }
}

async function loadGeneration() {
  if (!selectedGeneration.value) {
    currentGeneration.value = null
    return
  }
  try {
    currentGeneration.value = await getGeneration(selectedGeneration.value)
    selectedEdges.value = []
  } catch (error) {
    errorMessage.value = describe(error, '图代明细读取失败')
  }
}

async function packageEvidence() {
  errorMessage.value = ''
  notice.value = ''
  busy.value = true
  try {
    if (!selectedEdges.value.length) throw new Error('先勾选至少一条取样关系')
    if (!selectedGeneration.value) throw new Error('先选择取样图代')
    const result = await createPackage({
      report_id: props.reportId,
      generation: selectedGeneration.value,
      edge_ids: selectedEdges.value,
      request_token: requestToken.value,
      kind: packageKind.value,
      supersedes: packageKind.value === 'supplement' ? supersedesNo.value || null : null,
    })
    notice.value = result.message
    if (result.package) {
      const packageNo = result.package.evidence_no
      await download(packageNo)
    }
    await reloadAll()
    requestToken.value = newRequestToken()
  } catch (error) {
    errorMessage.value = describe(error, '证据封包失败')
  } finally {
    busy.value = false
  }
}

async function download(evidenceNo: string) {
  try {
    const blob = await downloadEvidenceFile(evidenceNo)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${evidenceNo}.evidence.json`
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = describe(error, '证据文件下载失败')
  }
}

async function replay(evidenceNo: string) {
  errorMessage.value = ''
  lastReplaySource.value = 'server'
  try {
    lastReplay.value = await replayEvidence(evidenceNo)
  } catch (error) {
    errorMessage.value = describe(error, '证据回放失败')
  }
}

async function reopenFile(event: Event) {
  errorMessage.value = ''
  notice.value = ''
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  lastReplaySource.value = 'file'
  try {
    const content = JSON.parse(await file.text()) as Record<string, unknown>
    lastReplay.value = await verifyEvidenceFile(content)
  } catch (error) {
    errorMessage.value = describe(error, '证据文件无法解析或校验失败')
    lastReplay.value = null
  } finally {
    input.value = ''
  }
}

async function close(reviewNo: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await closeReview(reviewNo, '确认错连')
    notice.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = describe(error, '复核结案失败')
  }
}

onMounted(reloadAll)
</script>
