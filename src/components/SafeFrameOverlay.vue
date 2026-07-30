<template>
  <div class="sfo" :title="`安全框 ${safeW}×${safeH}（框外上下细条会被裁掉）`">
    <div class="sfo-safe"></div>
    <div class="sfo-mask top"></div>
    <div class="sfo-mask bottom"></div>
  </div>
</template>

<script setup lang="ts">
/**
 * 裁切安全框：图片接口只能出 1312×736（ratio 1.7826），视频输出 1280×704（ratio 1.8181）。
 * 中心裁切到 1.8181 = 保留全宽、高度 1312/1.8181≈721.65，上下各裁 ~7.2px（≈0.97%）。
 * 框外（上下细条）半透明遮罩标「将被裁掉」，绿色虚线=安全区。
 */
defineProps<{
  imgW?: number;   // 图片宽，默认 1312
  imgH?: number;   // 图片高，默认 736
  safeW?: number;  // 安全宽，默认 1280
  safeH?: number;  // 安全高，默认 704
}>();
</script>

<style lang="scss" scoped>
.sfo {
  position: absolute; inset: 0; pointer-events: none;
}
/* 安全区：全宽，上下各内缩 cropPct。cropPct = (1 - safeH/imgH * imgW/safeW)/2 ≈ 0.97% */
.sfo-safe {
  position: absolute; left: 0; right: 0; top: 0.97%; bottom: 0.97%;
  border: 1px dashed rgba(74, 222, 128, 0.85);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.2);
}
.sfo-mask {
  position: absolute; left: 0; right: 0; height: 0.97%;
  background: repeating-linear-gradient(45deg,
    rgba(0, 0, 0, 0.5), rgba(0, 0, 0, 0.5) 4px,
    rgba(0, 0, 0, 0.3) 4px, rgba(0, 0, 0, 0.3) 8px);
}
.sfo-mask.top { top: 0; }
.sfo-mask.bottom { bottom: 0; }
</style>
