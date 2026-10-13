<template>
  <details class="mx-3 my-2 rounded border border-[var(--app-border)] p-2 text-xs">
    <summary class="cursor-pointer">保存的搜索</summary>
    <p class="my-2 text-[var(--app-text-secondary)]">保存关键词、消息类型和筛选条件；默认固定保存时的起止时间。</p>
    <label v-if="getCriteria && Number(rangeDays) > 0" class="mb-2 flex items-center gap-2">
      <input v-model="relative" type="checkbox" />每次重查最近 {{ rangeDays }} 天
    </label>
    <form v-if="getCriteria" class="flex gap-1" @submit.prevent="save">
      <input v-model="name" :class="{ 'privacy-blur': privacyMode }" aria-label="保存搜索名称" maxlength="120" placeholder="搜索名称" class="min-w-0 flex-1 rounded border border-[var(--app-border)] bg-[var(--app-surface-bg)] px-2 py-1" />
      <button :disabled="busy || !account || !name.trim()" class="rounded px-2 py-1 disabled:opacity-40">保存当前</button>
    </form>
    <p v-if="!applyCriteria" class="my-2 text-[var(--app-text-secondary)]">这里可查看和管理条件；导入对应账号的聊天数据后，可在聊天搜索中重新查询。</p>
    <p v-if="error" role="alert" class="mt-2 text-red-500" :class="{ 'privacy-blur': privacyMode }">{{ error }}</p>
    <p v-if="busy" role="status" class="mt-2">处理中…</p>
    <p v-else-if="!entries.length && !error" class="mt-2 text-[var(--app-text-secondary)]">还没有保存搜索。</p>
    <div v-for="entry in entries" :key="entry.id" class="mt-2 flex flex-wrap items-center gap-1">
      <template v-if="editing === entry.id">
        <input v-model="rename" :class="{ 'privacy-blur': privacyMode }" aria-label="新搜索名称" class="min-w-0 flex-1 rounded border border-[var(--app-border)] bg-[var(--app-surface-bg)] px-2 py-1" @keydown.enter.prevent="renameEntry(entry)" @keydown.esc="editing = ''" />
        <button :disabled="busy || !rename.trim()" @click="renameEntry(entry)">确定</button><button @click="editing = ''">取消</button>
      </template>
      <template v-else>
        <button v-if="applyCriteria" :disabled="busy" class="min-w-0 flex-1 truncate text-left" @click="apply(entry)"><span :class="{ 'privacy-blur': privacyMode }">{{ entry.name }}</span></button>
        <strong v-else class="min-w-0 flex-1 break-words" :class="{ 'privacy-blur': privacyMode }">{{ entry.name }}</strong>
        <button :disabled="busy" :aria-label="`重命名 ${entry.name}`" @click="editing = entry.id; rename = entry.name">改名</button>
        <button :disabled="busy" :aria-label="`删除 ${entry.name}`" @click="remove(entry)">删除</button>
      </template>
      <details v-if="!applyCriteria" class="w-full">
        <summary class="cursor-pointer">查看条件</summary>
        <dl class="mt-1 break-words" :class="{ 'privacy-blur': privacyMode }">
          <div v-for="(value, key) in entry.criteria" :key="key"><dt class="inline">{{ criteriaLabels[key] }}：</dt><dd class="inline">{{ criteriaValue(key, value) }}</dd></div>
        </dl>
      </details>
    </div>
    <button v-if="error" :disabled="busy" class="mt-2" @click="load">重新加载</button>
  </details>
</template>
<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
import { useLibraryApi } from '~/composables/useLibraryApi'
import { useApplicationAccountsStore } from '~/stores/applicationAccounts'
import { normalizeSessionPreview } from '~/lib/chat/formatters'
const props = defineProps({account:{type:String,required:true},rangeDays:{type:String,default:''},getCriteria:Function,applyCriteria:Function})
const api = useLibraryApi()
const { revisions } = storeToRefs(useApplicationAccountsStore())
const criteriaLabels = { scope:'范围', username:'会话', query:'关键词', retrieval_mode:'检索方式', render_types:'消息类型', sender:'发送人', session_type:'会话类型', start_time:'开始时间', end_time:'结束时间', relative_days:'最近天数' }
const criteriaValue = (key, value) => {
  if (value === null || value === '') return '未限定'
  if (key === 'start_time' || key === 'end_time') return new Date(value * 1000).toLocaleString()
  if (key === 'render_types') return normalizeSessionPreview(value)
  if (['scope','retrieval_mode','session_type'].includes(key)) return ({global:'全部聊天',conversation:'指定会话',keyword:'关键词',hybrid:'智能检索',group:'群聊',single:'私聊'})[value]
  return value
}
const { privacyMode } = storeToRefs(usePrivacyStore())
const entries = ref([]), name = ref(''), error = ref(''), busy = ref(false), editing = ref(''), rename = ref('')
const relative = ref(false)
let version = 0, refreshPending = false
const execute = async (action, preserveError = false) => {
  const account = props.account, request = ++version
  const previousError = preserveError ? error.value : ''
  busy.value = true; if (!preserveError) error.value = ''
  const current = () => request === version && account === props.account
  try { await action(account, current) }
  catch (err) { if (current()) error.value = `${previousError ? `${previousError}；刷新保存搜索失败：` : ''}${err?.data?.detail || err.message}` }
  finally {
    if (current()) {
      busy.value = false
      if (refreshPending) { refreshPending = false; void execute(readEntries, true) }
    }
  }
}
const readEntries = async (account,current) => {
  const result = await api.listSearches(account)
  if (current()) entries.value = result
}
const load = () => {
  if (busy.value) { refreshPending = true; return }
  return execute(readEntries)
}
const save = () => execute(async (account,current) => {
  const criteria = props.getCriteria({relative:relative.value})
  if (!criteria.query && criteria.retrieval_mode === 'hybrid') throw new Error('智能搜索需要关键词')
  if (!criteria.query && !criteria.render_types) throw new Error('请先输入关键词或选择消息类型')
  const result = await api.createSearch(account,{name:name.value.trim(),criteria})
  if (current()) {entries.value = [...entries.value,result];name.value = ''}
})
const apply = entry => execute(async (_account,current) => { if(current()) await props.applyCriteria(entry.criteria) })
const renameEntry = entry => execute(async (account,current) => {
  const result = await api.updateSearch(account,entry.id,{name:rename.value.trim()})
  if(current()) {entries.value = entries.value.map(item => item.id === entry.id ? result : item);editing.value = ''}
})
const remove = entry => execute(async (account,current) => {
  await api.deleteSearch(account,entry.id)
  if(current()) entries.value = entries.value.filter(item => item.id !== entry.id)
})
watch(() => props.account, account => {
  ++version;refreshPending = false;entries.value = [];error.value = '';busy.value = false;editing.value = '';name.value = '';relative.value = false
  if(account) void load()
},{immediate:true})
watch(() => revisions.value[props.account], () => { if (props.account) void load() })
onUnmounted(() => { ++version })
</script>
