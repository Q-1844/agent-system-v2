<template>
  <!-- AI-6D.1 §7：紧贴选中 Shot 的内联 Director 行（非独立 toolbar）。
       无独立边框/阴影/底色——flush 在 shot-item 内。权重分层：
       生成修改提案 = primary CTA（accent）；chat 动作 = 次要静默文本。
       触发既有 Floating Agent 交互（ShotBoard.onDirectorAction），不开第二套聊天。 -->
  <div class="dct" @click.stop>
    <span class="dct-label">Director</span>
    <button class="dct-quiet" @click="emit('action', 'ask')" title="就这镜向 Director 提问">Ask</button>
    <span class="dct-sep" />
    <button class="dct-quiet" @click="emit('action', 'analyze')">分析</button>
    <button class="dct-quiet" @click="emit('action', 'pace')">节奏</button>
    <button class="dct-quiet" @click="emit('action', 'compose')">构图</button>
    <button class="dct-quiet" @click="emit('action', 'redesign')">重新设计</button>
    <button class="dct-propose" @click="emit('action', 'propose')" title="生成可审查、可选择应用的修改提案（经创作版本，官方分镜不变）">✂ 修改提案</button>
  </div>
</template>

<script setup lang="ts">
// 无 props：toolbar 纯触发；ShotBoard 持 shot/scene，在 onDirectorAction 里注入 context。
const emit = defineEmits<{ (e: 'action', key: string): void }>();
</script>

<style lang="scss" scoped>
/* AI-6D.1：flush attached——透明底、无边框、无阴影，读作 shot 的一部分。 */
.dct {
  display: flex; align-items: center; gap: 6px;
  height: 30px;
  margin: -2px -2px 8px;               /* 贴 shot-item 边缘，不另成一块 */
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  box-shadow: none;
  font-size: 12px;
  user-select: none;
}
.dct-label {
  font-size: 10px; font-weight: 600; letter-spacing: .5px;
  color: var(--text-faint);
  text-transform: lowercase;
}
.dct-sep { width: 1px; height: 12px; background: var(--border); flex-shrink: 0; }
/* chat 动作：静默文本（次要） */
.dct-quiet {
  padding: 2px 6px; border-radius: 4px; border: none;
  background: none; color: var(--text-muted);
  font-size: 11px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
/* 生成修改提案：primary CTA */
.dct-propose {
  margin-left: auto;
  padding: 3px 10px; border-radius: 5px; border: none;
  background: var(--accent); color: var(--accent-text);
  font-size: 11px; font-weight: 600; cursor: pointer;
  &:hover { background: var(--accent-hover); }
}
</style>
