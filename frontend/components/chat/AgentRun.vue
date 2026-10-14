<template>
  <section class="agent-reply agent-run">
    <AgentLibraryScopeNotice v-if="run.library_scope" :scope="run.library_scope" :coverage="run.topic_coverage" />
    <div class="not-prose max-w-prose space-y-4 agent-process-panel" :class="{ 'is-open': open, 'is-running': running }" aria-label="执行过程">
    <button class="agent-process-toggle" type="button" :aria-expanded="open" :aria-controls="`process-${run.id}`" :title="open ? '收起执行过程' : '展开执行过程'" @click="toggle">
      <span class="agent-process-title">{{ running ? '执行中' : '执行过程' }}</span>
      <small class="agent-process-meta">{{ Math.floor(Math.max(0, elapsed || 0) / 60) }}分{{ Math.floor(Math.max(0, elapsed || 0)) % 60 }}秒</small>
      <ChevronDown v-if="open" :size="16" :stroke-width="1.8" aria-hidden="true" />
      <ChevronRight v-else :size="16" :stroke-width="1.8" aria-hidden="true" />
    </button>
    <div v-show="open || running" class="agent-process-body" :class="{'is-collapsed':!open}">
    <div :id="`process-${run.id}`" v-show="open" class="agent-process" :class="{ 'is-live': running }">
      <AgentSubtasks v-if="(run.subtasks?.total || run.subtasks?.scanning || run.subtasks?.planned) && !firstTaskRecord" :run="run" :now="now" @locate="$emit('locate', $event)" />
      <template v-for="item in groupedRecords" :key="item.id">
        <AgentSubtasks v-if="(run.subtasks?.total || run.subtasks?.scanning || run.subtasks?.planned) && item.id === firstTaskRecord" :run="run" :now="now" @locate="$emit('locate', $event)" />
        <AgentToolCall v-else-if="item.kind === 'tool' && !(item.action === 'task' && run.subtasks?.total)" :items="item.calls" :now="now" :name-for="nameFor" :view-state="viewState" />
        <div v-else-if="item.kind === 'progress'" class="agent-progress-note" :class="{'is-superseded':item.status === 'superseded'}" role="group" aria-label="阶段性回复"><AgentAnswer :text="item.text" :citations="citations" :references="run.references || []" :streaming="item.status === 'running'" @locate="$emit('locate', $event)" /><small v-if="item.status === 'superseded'">已根据补充要求调整</small></div>
        <div v-else-if="item.kind === 'supplement'" class="agent-supplement"><p>{{ item.text }}</p><small>{{ item.status === 'applied' ? '补充要求已应用' : '已收到补充要求' }}</small></div>
        <template v-else-if="isCompaction(item)"><AgentContextCompaction v-if="open" :item="item" :run="run" :view-state="viewState" /></template>
        <p v-else-if="item.kind === 'notice'" class="agent-process-notice" role="status">{{ item.text }}<small v-if="item.attempt"> · 第 {{ item.attempt }} 次尝试</small></p>
        <div v-else-if="item.kind === 'answer' && item.status === 'superseded'" class="agent-progress-note is-superseded"><small>旧答案已根据补充要求调整</small></div>
        <div v-else-if="item.kind === 'status'" class="agent-stage-row" :class="`is-${item.status}`">
          <Check v-if="item.status === 'completed'" :size="16" :stroke-width="1.8" aria-hidden="true" />
          <CirclePause v-else-if="item.status !== 'running'" :size="16" :stroke-width="1.8" aria-hidden="true" />
          <span :class="{ 'agent-shimmer': item.status === 'running' }">{{ String(item?.text || '').replace(/^正在/, '') }}</span>
          <small><span class="sr-only">{{ stageOutcome(item.status) }} · </span>{{ duration((item.finished_at ?? currentNow / 1000) - item.started_at) }}</small>
        </div>
      </template>
    </div>
      <div v-if="running && !(open && compacting) && !(open && run.subtasks?.running && run.stage === '执行独立子任务')" class="agent-live-step" role="status" aria-live="polite" aria-atomic="true">
        <span class="agent-stream-status agent-shimmer">{{ activeTool ? activeTool.detail || activeTool.text || run.stage : '思考中' }}</span>
        <time aria-hidden="true">{{ duration(activeTool ? (currentNow / 1000 - (activeTool.started_at ?? run.stage_started_at ?? currentNow / 1000)) : stageElapsed) }}</time>
      </div>
    </div>
    </div>
    <div v-if="run.error" class="agent-error" role="alert">{{ run.error }}<details v-if="run.error_info?.diagnostic_id"><summary>诊断信息</summary><small>{{ run.error_info.category }} · {{ run.error_info.diagnostic_id }}</small></details></div>
    <div v-if="run.answer" class="agent-final-answer"><h3 v-if="run.status !== 'completed'" class="agent-answer-heading">{{ running ? '正在回答' : '未完成的回答' }}</h3><AgentAnswer :text="run.answer" :citations="citations" :references="run.references || []" :streaming="running" @locate="$emit('locate', $event)" /></div>

    <div v-if="run.choices?.length" class="agent-choices"><button v-for="choice in run.choices" :key="choice.username" type="button" @click="$emit('choose', choice)">{{ choice.name }}<small>{{ choice.username }}</small></button></div>
    <div v-if="!running" class="agent-result-actions">
      <div v-if="run.answer" class="agent-answer-footer">
        <AgentCopyAction :text="run.answer" :citations="citations" :references="run.references || []" />
        <span v-if="finalSummary" class="agent-final-summary" :title="finalSummaryTitle">{{ finalSummary }}</span>
      </div>
      <button v-if="!running && latest && run.error_info?.action === 'settings'" type="button" @click="$emit('settings')">检查 AI 服务</button>
      <button v-else-if="!running && run.restart_required" type="button" @click="$emit('restart')"><RotateCw :size="16" :stroke-width="1.8" aria-hidden="true" />使用新引擎重新运行</button>
      <button v-else-if="!running && latest && run.can_resume !== false && ['budget','failed','cancelled','interrupted'].includes(run.status)" type="button" @click="$emit('continue')"><RotateCw :size="16" :stroke-width="1.8" aria-hidden="true" />{{ run.status === 'failed' ? '重试这一步' : '继续查找' }}</button>
    </div>

  </section>
</template>

<script setup>
import { computed } from 'vue'
import { Check, ChevronDown, ChevronRight, CirclePause, RotateCw } from '@lucide/vue'
import AgentToolCall from './AgentToolCall.vue'
import AgentSubtasks from './AgentSubtasks.vue'
import AgentCopyAction from './AgentCopyAction.vue'
import { groupTimelineTools } from '~/utils/agentTimeline'
import AgentAnswer from './AgentAnswer.vue'
import AgentContextCompaction from './AgentContextCompaction.vue'
import AgentLibraryScopeNotice from './AgentLibraryScopeNotice.vue'
const props = defineProps({run:{type:Object,required:true},now:Number,nearBottom:Boolean,latest:Boolean,nameFor:{type:Function,default:()=>''},viewState:{type:Object,required:true}})
defineEmits(['locate','choose','continue','restart','settings'])
const citations = computed(() => (props.run.citations || []).map(source => source.kind === 'library' ? { ...source, run_id: props.run.id, run_version: props.run.version } : source))
const running = computed(() => ['queued','running'].includes(props.run.status))
const formatTokenCount = value => {
  if (!Number.isFinite(value) || value < 0) return '未知'
  if (value < 1000) return String(Math.trunc(value))
  const units = [[1000, 'k'], [10000, 'w'], [1000000, 'm']]
  for (let i = 0; i < units.length; i++) {
    const [scale, suffix] = units[i], next = units[i + 1]
    const rounded = Number((value / scale).toFixed(1))
    if (next && rounded * scale >= next[0]) continue
    return `${rounded}${suffix}`
  }
}
const summaryText = formatCount => {
  const parts = [], run = props.run
  if (run.coverage_state !== 'not_applicable' && Number.isFinite(run.read_count)) {
    parts.push(`已读取 ${run.read_count} 条`)
    if (run.analysis?.tracked === false) parts.push('按需检索')
  }
  if (run.stage_notes?.count) {
    parts.push(`阶段笔记 ${run.stage_notes.count} 份 (${run.stage_notes.total_facts || 0} 条事实)`)
  }
  if (run.usage) parts.push(`输入 ${formatCount(run.usage.input_tokens)} · 输出 ${formatCount(run.usage.output_tokens)} Token`)
  return parts.join(' · ')
}
const finalSummary = computed(() => summaryText(formatTokenCount))
const finalSummaryTitle = computed(() => summaryText(value => Number.isFinite(value) && value >= 0 ? String(Math.trunc(value)) : '未知') + (props.run.usage?.unknown ? '（部分调用未返回完整用量，显示已知部分）' : ''))
// 有过程就默认展开，完成后不自动收起；仍保留用户手动收起的选择。
const open = computed({get:()=>props.viewState[props.run.id] ?? !(running.value && !records.value.length),set:v=>{props.viewState[props.run.id]=v}})
// 仅去掉与入口重复的当前阶段，保留已完成的同名阶段及阶段小结。
const records = computed(() => (props.run.timeline?.length ? props.run.timeline : (props.run.activity || []).map(x=>({...x,kind:'status'}))).filter(x=>(x.kind !== 'answer' || x.status === 'superseded') && !(running.value && x.kind === 'status' && x.status === 'running' && x.text === props.run.stage)).sort((a,b)=>(a.seq||0)-(b.seq||0)))
const groupedRecords = computed(() => groupTimelineTools(records.value))
const isCompaction = item => item.kind === 'notice' && item.context_job?.id && Number.isFinite(item.context_job.before)
const compactions = computed(() => records.value.filter(isCompaction))
const compacting = computed(() => compactions.value.some(item => item.context_job.status === 'running' && (item.input_version ?? props.run.version) === props.run.version))
const activeTool = computed(() => running.value ? records.value.filter(item => item.kind === 'tool' && item.status === 'running' && (item.input_version ?? props.run.version) === props.run.version).at(-1) : null)
const firstTaskRecord = computed(() => groupedRecords.value.find(item => item.kind === 'tool' && item.action === 'task')?.id)
const currentNow = computed(() => props.now || Date.now())
const elapsed = computed(() => (props.run.elapsed_seconds || 0) + (running.value ? Math.max(0, currentNow.value / 1000 - (props.run.segment_started ?? currentNow.value / 1000)) : 0))
const stageElapsed = computed(() => Math.max(0, currentNow.value / 1000 - (props.run.stage_started_at ?? props.run.segment_started ?? currentNow.value / 1000)))
const duration = value => { const n=Math.max(0,Math.floor(value || 0)); return n>=60 ? `${Math.floor(n/60)}分${n%60}秒` : `${n}秒` }
const stageOutcome = status => ({running:'进行中',completed:'已完成',failed:'未完成',superseded:'已调整',cancelled:'已停止',paused:'已暂停',incomplete:'未完成'}[status] || '已结束')
const toggle = () => { open.value = !open.value }
</script>
