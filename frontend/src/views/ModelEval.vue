<template>
  <div class="model-eval-shell">
    <div class="integration-bar">
      <button type="button" class="back-button" @click="router.push('/run')">
        <el-icon><ArrowLeft /></el-icon>
        返回 Skill 评测
      </button>
      <div>
        <strong>模型与题库评测</strong>
        <span>Prism Eval · 标准题库、模型与 Agent、公平实验及逐题证据</span>
      </div>
      <el-tag type="success" effect="plain">已合入当前服务</el-tag>
    </div>
    <iframe
      class="model-eval-frame"
      src="/prism/"
      title="模型与题库评测"
      allow="clipboard-read; clipboard-write"
    />
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.model-eval-shell {
  display: grid;
  grid-template-rows: 48px minmax(0, 1fr);
  width: 100%;
  height: 100vh;
  background: #f4f7f5;
}
.integration-bar {
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 18px;
  color: #e8f3ed;
  background: #132b25;
  box-shadow: 0 2px 12px rgb(20 50 42 / 18%);
}
.integration-bar > div {
  display: flex;
  flex: 1;
  align-items: baseline;
  gap: 12px;
}
.integration-bar strong {
  color: #fff;
  font-size: 15px;
}
.integration-bar span {
  color: #9cb5ac;
  font-size: 12px;
}
.back-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 11px;
  color: #e8f3ed;
  border: 1px solid #44665c;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
}
.back-button:hover {
  border-color: #65b895;
  background: #1d4036;
}
.model-eval-frame {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}
</style>
