<template>
  <div class="collapsible" :class="{ open: isOpen }">
    <button class="coll-head" type="button" :aria-expanded="isOpen" :aria-controls="contentId" @click="isOpen = !isOpen">
      <span class="coll-chevron" aria-hidden="true">{{ isOpen ? '▾' : '▸' }}</span>
      <span class="coll-title">{{ title }}</span>
      <span class="coll-summary" v-if="summary">{{ summary }}</span>
    </button>
    <div :id="contentId" class="coll-body" v-show="isOpen">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
defineProps<{ title: string; summary?: string }>();
const isOpen = ref(false);
let _cid = 0;
const contentId = `coll-${++_cid}`;
</script>

<style lang="scss" scoped>
.collapsible { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-surface); overflow: hidden; margin-bottom: 12px; }
.coll-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 11px 14px; background: none; border: none; cursor: pointer; text-align: left; font-family: inherit; &:hover { background: var(--bg-hover); } &:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; } }
.coll-chevron { font-size: 11px; color: var(--text-muted); width: 12px; flex-shrink: 0; }
.coll-title { font-size: 12px; font-weight: 600; color: var(--text-secondary); }
.coll-summary { margin-left: auto; font-size: 11px; color: var(--text-muted); white-space: nowrap; }
.coll-body { padding: 12px 14px; border-top: 1px solid var(--border); }
</style>
