<!--
  AI-7D-R · Script Writer 最小测试弹窗（隔离，单工具 edit_story_text）。
  目的：在独立弹窗里验证“LLM → 一个工具 → 真实落盘 → 诚实回复”最小闭环，不走
  ChangeSession/提案/审查流水线。验证稳定后再逐步加回规则（§16 Level-1 直接编辑）。
  默认直接改、不审核（用户明确要求）。
-->
<template>
  <Teleport to="body">
    <div v-if="open" class="swt-overlay" @click.self="close">
      <div class="swt-modal">
        <header class="swt-head">
          <div>
            <span class="swt-title">Script Writer · 直接编辑测试</span>
            <span class="swt-sub">单工具 edit_story_text · 不走审查流 · 默认直接改</span>
          </div>
          <button class="swt-x" @click="close">✕</button>
        </header>

        <div class="swt-project">当前项目：{{ projectName || '未选择' }}</div>

        <div class="swt-chat" ref="chatEl">
          <div v-if="!messages.length" class="swt-empty">
            直接对 Script Writer 说要怎么改剧本，例如：<br/>
            「把剧本延长到600字」/「把结局改成林夏离开了」/「扩写第二段」<br/>
            它会直接修改正式剧本并告诉你结果。
          </div>
          <div v-for="(m, i) in messages" :key="i" class="swt-msg" :class="'r-' + m.role">
            <span class="swt-role">{{ m.role === 'user' ? '我' : 'Script Writer' }}</span>
            <span class="swt-content">{{ m.content }}</span>
            <span v-if="m.edited" class="swt-tag">已落盘</span>
          </div>
          <div v-if="sending" class="swt-msg r-assistant swt-thinking">Script Writer 思考中…</div>
        </div>

        <div v-if="lastNewStory" class="swt-result">
          <div class="swt-result-head">最新剧本正文（已写入项目）</div>
          <pre class="swt-story">{{ lastNewStory }}</pre>
        </div>

        <div class="swt-input-row">
          <textarea v-model="draft" class="swt-input" rows="2"
            placeholder="对 Script Writer 说…" :disabled="sending || !projectName"
            @keydown.enter.exact.prevent="send"></textarea>
          <button class="swt-send" :disabled="sending || !draft.trim() || !projectName" @click="send">
            {{ sending ? '…' : '发送' }}
          </button>
        </div>
        <div v-if="error" class="swt-err">{{ error }}</div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue';
import { apiClient } from '@/api';
import type { AgentMessage } from '@/api';
import { useProjectStore } from '@/store';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const projectStore = useProjectStore();
const projectName = computed(() => projectStore.projectName);

const messages = ref<Array<AgentMessage & { edited?: boolean }>>([]);
const draft = ref('');
const sending = ref(false);
const error = ref('');
const lastNewStory = ref<string | null>(null);
const chatEl = ref<HTMLElement | null>(null);

async function loadSession() {
  if (!projectName.value) return;
  try {
    const s = await apiClient.scriptWriterTestSession(projectName.value);
    messages.value = (s.messages || []).map((m: any) => ({ ...m }));
    await scroll();
  } catch (e: any) { error.value = '加载会话失败: ' + (e.message || e); }
}

function close() { emit('close'); }

async function send() {
  const text = draft.value.trim();
  if (!text || sending.value || !projectName.value) return;
  draft.value = '';
  messages.value.push({ role: 'user', content: text, status: null, created_at: Date.now() / 1000 });
  sending.value = true; error.value = '';
  await scroll();
  try {
    const res = await apiClient.scriptWriterTestTurn(projectName.value, text);
    messages.value.push({ ...res.message, edited: res.edited });
    if (res.edited && res.new_story_text) {
      lastNewStory.value = res.new_story_text;
    }
  } catch (e: any) {
    error.value = '发送失败: ' + (e.message || e);
  } finally {
    sending.value = false;
    await scroll();
  }
}

async function scroll() {
  await nextTick();
  if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight;
}

watch(() => props.open, (o) => { if (o) loadSession(); }, { immediate: true });
</script>

<style lang="scss" scoped>
.swt-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.swt-modal { width: min(680px, 94vw); max-height: 88vh; display: flex; flex-direction: column; background: var(--bg, #1e1e2e); color: var(--text, #cdd6f4); border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.4); overflow: hidden; }
.swt-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,.08); }
.swt-title { font-weight: 600; font-size: 14px; }
.swt-sub { display: block; font-size: 11px; color: var(--text-secondary, #9399b2); margin-top: 2px; }
.swt-x { background: none; border: none; color: inherit; font-size: 18px; cursor: pointer; }
.swt-project { padding: 6px 16px; font-size: 12px; color: var(--text-secondary, #9399b2); border-bottom: 1px solid rgba(255,255,255,.05); }
.swt-chat { flex: 1; overflow-y: auto; padding: 12px 16px; min-height: 200px; max-height: 46vh; display: flex; flex-direction: column; gap: 8px; }
.swt-empty { color: var(--text-secondary, #9399b2); font-size: 13px; line-height: 1.6; }
.swt-msg { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border-radius: 8px; max-width: 92%; font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.r-user { align-self: flex-end; background: rgba(96,165,250,.15); }
.r-assistant { align-self: flex-start; background: rgba(255,255,255,.05); }
.swt-role { font-size: 11px; color: var(--text-secondary, #9399b2); }
.swt-tag { display: inline-block; font-size: 10px; color: #4ade80; border: 1px solid rgba(74,222,128,.4); border-radius: 6px; padding: 0 4px; margin-top: 2px; align-self: flex-start; }
.swt-thinking { color: var(--text-secondary, #9399b2); font-style: italic; }
.swt-result { border-top: 1px solid rgba(255,255,255,.08); padding: 10px 16px; max-height: 30vh; overflow-y: auto; }
.swt-result-head { font-size: 11px; color: #4ade80; margin-bottom: 4px; }
.swt-story { margin: 0; font-family: inherit; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; background: rgba(255,255,255,.03); padding: 8px; border-radius: 6px; }
.swt-input-row { display: flex; gap: 8px; padding: 10px 16px; border-top: 1px solid rgba(255,255,255,.08); }
.swt-input { flex: 1; resize: none; background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.1); border-radius: 8px; color: inherit; padding: 8px; font-size: 13px; font-family: inherit; }
.swt-send { background: rgba(96,165,250,.9); color: #fff; border: none; border-radius: 8px; padding: 0 18px; font-size: 13px; cursor: pointer; }
.swt-send:disabled { opacity: .5; cursor: not-allowed; }
.swt-err { padding: 0 16px 10px; font-size: 12px; color: #f87171; }
</style>
