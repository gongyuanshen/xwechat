<template>
  <div v-if="open" class="library-shade" @click.self="$emit('close')">
    <form
      ref="dialog"
      tabindex="-1"
      class="library-dialog theme-scope"
      role="dialog"
      aria-modal="true"
      aria-labelledby="library-save-title"
      @submit.prevent="save"
      @keydown.esc.stop="$emit('close')"
      @keydown.tab="trapFocus"
    >
      <h2 id="library-save-title">保存到本地资料夹</h2>
      <p v-if="item?.kind === 'attachment'">复制附件文件并保存来源；原文件不可用时会提示失败。</p>
      <p v-else-if="item?.kind === 'message'">保存消息原文与来源，不包含附件文件。要保留附件，请选择“保存附件副本到资料夹”。</p>
      <p v-else-if="item?.kind === 'extracted_text'">保存已识别或提取的全文与出处，不包含附件文件。识别结果请核对原图或原文档。</p>
      <p v-else>保存报告副本和来源信息，不修改原始回答。</p>
      <p v-if="item?.kind === 'attachment'">图片保存本机当前可读取的版本，可能是缩略图，不保证原图。</p>
      <p v-if="error" role="alert" class="library-error" :class="{ 'privacy-blur': privacyMode }">{{ error }}</p>
      <fieldset :disabled="busy">
        <label
          >资料夹<select
            v-model="folderId"
            :class="{ 'privacy-blur': privacyMode }"
            required
            :disabled="loading || busy"
          >
            <option value="" disabled>请选择资料夹</option>
            <option
              v-for="folder in folders"
              :key="folder.id"
              :value="folder.id"
            >
              {{ folder.name }}
            </option>
          </select></label
        >
        <div class="library-inline">
          <input
            v-model="folderName"
            :class="{ 'privacy-blur': privacyMode }"
            aria-label="新资料夹名称"
            placeholder="新资料夹名称"
            maxlength="100"
          /><button
            type="button"
            :disabled="busy || !folderName.trim()"
            @click="createFolder"
          >
            新建资料夹
          </button>
        </div>
        <label>标题<input v-model="title" :class="{ 'privacy-blur': privacyMode }" maxlength="200" /></label>
        <label>备注<textarea v-model="notes" :class="{ 'privacy-blur': privacyMode }" rows="3" /></label>
        <label>标签<input v-model="tags" :class="{ 'privacy-blur': privacyMode }" placeholder="以逗号分隔" /></label>
      </fieldset>
      <footer>
        <button type="button" @click="$emit('close')">取消</button
        ><button
          type="submit"
          :disabled="loading || busy || !folderId || !account"
        >
          {{ busy ? "正在保存…" : item?.kind === 'attachment' ? "保存附件副本" : item?.kind === 'extracted_text' ? "保存文字" : "保存副本" }}
        </button>
      </footer>
    </form>
  </div>
</template>
<script setup>
import { useLibraryApi } from "~/composables/useLibraryApi";
import { ref, watch, nextTick, onUnmounted } from "vue";
import { storeToRefs } from "pinia";
import { usePrivacyStore } from "~/stores/privacy";
const props = defineProps({ open: Boolean, account: String, item: Object });
const emit = defineEmits(["close", "saved"]);
const api = useLibraryApi();
const { privacyMode } = storeToRefs(usePrivacyStore());
const folders = ref([]),
  folderId = ref(""),
  folderName = ref(""),
  title = ref(""),
  notes = ref(""),
  tags = ref(""),
  error = ref(""),
  loading = ref(false),
  busy = ref(false);
const dialog = ref(null);
let previousFocus;
watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousFocus = document.activeElement;
      await nextTick();
      dialog.value
        ?.querySelector(
          "input:not(:disabled), select:not(:disabled), button:not(:disabled)",
        )
        ?.focus();
    } else previousFocus?.focus();
  },
  { immediate: true },
);
onUnmounted(() => previousFocus?.focus());
function trapFocus(event) {
  const controls = [
    ...dialog.value.querySelectorAll("button, input, select, textarea"),
  ].filter((el) => !el.matches(":disabled") && !el.closest("fieldset[disabled]"));
  const first = controls[0],
    last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
let generation = 0;
const message = (e) => typeof e.data?.detail === 'string' ? e.data.detail : e.data?.detail?.message || e.message;
watch(
  () => [props.open, props.account, props.item],
  async () => {
    const token = ++generation;
    folders.value = [];
    folderId.value = "";
    folderName.value = "";
    error.value = "";
    busy.value = false;
    title.value = props.item?.title || "";
    notes.value = "";
    tags.value = "";
    if (!props.open || !props.account) {
      loading.value = false;
      return;
    }
    loading.value = true;
    try {
      const result = await api.listFolders(props.account);
      if (token === generation) folders.value = result;
    } catch (e) {
      if (token === generation) error.value = message(e);
    } finally {
      if (token === generation) loading.value = false;
    }
  },
  { immediate: true },
);
async function createFolder() {
  const token = generation;
  busy.value = true;
  error.value = "";
  try {
    const folder = await api.createFolder(props.account, {
      name: folderName.value.trim(),
    });
    if (token !== generation) return;
    folders.value.push(folder);
    folderId.value = folder.id;
    folderName.value = "";
  } catch (e) {
    if (token === generation) error.value = message(e);
  } finally {
    if (token === generation) busy.value = false;
  }
}
async function save() {
  const token = generation;
  busy.value = true;
  error.value = "";
  try {
    const saved = await api.createItem(props.account, {
      ...props.item,
      folder_id: folderId.value,
      title: title.value,
      notes: notes.value,
      tags: tags.value
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean),
    });
    if (token === generation) emit("saved", saved);
  } catch (e) {
    if (token === generation) error.value = message(e);
  } finally {
    if (token === generation) busy.value = false;
  }
}
</script>
<style scoped>
.library-shade {
  position: fixed;
  inset: 0;
  background: #0007;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 20px;
}
.library-dialog {
  background: var(--app-surface-bg, #fff);
  color: var(--app-text-primary, #1f2937);
  width: min(460px, 100%);
  max-height: 90vh;
  overflow: auto;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 12px 40px #0003;
}
.library-dialog fieldset {
  border: 0;
  padding: 0;
  min-width: 0;
}
.library-dialog h2 {
  font-size: 18px;
  font-weight: 600;
}
.library-dialog p {
  font-size: 13px;
  margin: 10px 0;
}
.library-dialog label {
  display: grid;
  gap: 6px;
  font-size: 13px;
  margin-top: 14px;
}
.library-dialog input,
.library-dialog textarea,
.library-dialog select {
  width: 100%;
  border: 1px solid var(--app-border, #d1d5db);
  border-radius: 6px;
  padding: 8px;
  background: transparent;
  color: inherit;
}
.library-inline,
footer {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}
footer {
  justify-content: flex-end;
}
button {
  padding: 7px 12px;
  border-radius: 6px;
  border: 1px solid var(--app-border, #d1d5db);
  white-space: nowrap;
}
button:disabled {
  opacity: 0.5;
}
.library-error {
  color: var(--danger-color, #b91c1c);
}
</style>
