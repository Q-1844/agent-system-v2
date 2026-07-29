<template>
  <router-view />
</template>

<script setup lang="ts">
</script>

<style lang="scss">
html, body {
  margin: 0;
  padding: 0;
  height: 100%;
  background-color: #111111;
  color: #e5e5e5;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
}

#app {
  height: 100%;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

a {
  text-decoration: none;
  color: inherit;
}
</style>
