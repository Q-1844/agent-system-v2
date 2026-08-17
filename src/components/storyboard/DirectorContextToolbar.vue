<template>
  <!-- AI-6C §14：选中 Shot 后出现的 contextual floating toolbar。
       极紧凑（36–40px），极轻阴影+1px border+solid surface（§14）。
       只在选中态渲染（父控制 v-if），非永久 AI 按钮（§35）。
       动作经 emit 交给 ShotBoard → 设 agent=director + context=shot + 复用 Floating Bar（§15），
       不调 director.py、不开第二套聊天。 -->
  <div class="dct" @click.stop>
    <span class="dct-label">DIRECTOR</span>
    <button class="dct-btn" @click="emit('action', 'ask')">Ask Director</button>
    <span class="dct-sep" />
    <button class="dct-btn" @click="emit('action', 'analyze')">分析镜头</button>
    <button class="dct-btn" @click="emit('action', 'pace')">优化节奏</button>
    <button class="dct-btn" @click="emit('action', 'compose')">分析构图</button>
    <button class="dct-btn" @click="emit('action', 'redesign')">重新设计</button>
  </div>
</template>

<script setup lang="ts">
// 无 props：toolbar 纯触发；ShotBoard 持 shot/scene，在 onDirectorAction 里注入 context。
const emit = defineEmits<{ (e: 'action', key: string): void }>();
</script>

<style lang="scss" scoped>
.dct {
  display: flex; align-items: center; gap: 8px;
  height: 36px;
  padding: 0 10px;
  margin-bottom: 8px;                 /* 内联在 shot-item 顶部，下方留间隙 */
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.16);
  font-size: 12px;
  user-select: none;
}
.dct-label {
  font-size: 10px; font-weight: 700; letter-spacing: .6px;
  color: var(--text-muted);
}
.dct-sep { width: 1px; height: 16px; background: var(--border); }
.dct-btn {
  padding: 3px 9px; border-radius: 5px; border: none;
  background: var(--bg-root); color: var(--text-secondary);
  font-size: 11px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
</style>
