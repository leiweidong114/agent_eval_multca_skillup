<template>
  <aside class="sidebar">
    <div class="brand"><span class="brand-mark">A</span><div><strong>AGENT EVAL</strong><small>MODEL × SKILL LAB</small></div></div>
    <el-menu :default-active="activeMenu" router class="navigation">
      <el-menu-item index="/"><el-icon><House /></el-icon><span>首页</span></el-menu-item>
      <el-menu-item index="/evaluations/new"><el-icon><Plus /></el-icon><span>新建评测</span></el-menu-item>
      <el-menu-item index="/schematic-overview"><el-icon><Connection /></el-icon><span>原理图生成总览</span></el-menu-item>
      <el-menu-item index="/benchmarks"><el-icon><Collection /></el-icon><span>题库管理</span></el-menu-item>
      <el-menu-item index="/skills"><el-icon><MagicStick /></el-icon><span>Skill 管理</span></el-menu-item>
      <el-menu-item index="/results"><el-icon><Clock /></el-icon><span>评测结果</span></el-menu-item>
      <el-menu-item index="/runtimes"><el-icon><Cpu /></el-icon><span>模型与 Agent</span></el-menu-item>
    </el-menu>
    <div class="side-foot"><span class="dot ok"></span><div>服务运行状态<small>本地评测服务已连接</small></div></div>
  </aside>
  <main class="app-main">
    <header class="page-header">
      <div><span class="eyebrow">UNIFIED EVALUATION</span><h1>{{ $route.meta.title }}</h1><p>{{ $route.meta.description }}</p></div>
      <el-button v-if="$route.path !== '/evaluations/new'" type="primary" @click="$router.push('/evaluations/new')">＋ 发起评测</el-button>
    </header>
    <router-view />
  </main>
</template>
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const activeMenu = computed(() => route.path.startsWith('/results/') ? '/results' : route.path)
</script>
<style scoped>
.sidebar{position:fixed;z-index:10;inset:0 auto 0 0;width:218px;background:var(--nav);border-right:1px solid var(--line);padding:25px 14px 18px;display:flex;flex-direction:column}.brand{display:flex;gap:11px;align-items:center;padding:0 10px 25px}.brand-mark{display:grid;place-items:center;width:34px;height:34px;background:var(--primary);color:var(--panel);border-radius:9px;font:700 18px Georgia}.brand strong{display:block;font-size:13px;letter-spacing:1.5px}.brand small,.side-foot small{display:block;color:var(--muted);font-size:9px;letter-spacing:1.2px}.navigation{display:grid;gap:3px;border:0;background:transparent}.navigation :deep(.el-menu-item){height:40px;margin:0;padding:0 12px!important;border-radius:8px;background:transparent;color:#61666d;font-size:13px}.navigation :deep(.el-menu-item:hover){background:rgba(255,255,255,.7);color:var(--ink)}.navigation :deep(.el-menu-item.is-active){background:var(--panel);color:var(--ink);font-weight:650;box-shadow:0 1px 4px rgba(20,24,31,.07)}.side-foot{margin-top:auto;border-top:1px solid var(--line);padding:16px 10px 0;display:flex;align-items:center;gap:9px;font-size:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#abb0b6;flex:0 0 auto}.dot.ok{background:#36a474}.app-main{margin-left:218px;min-height:100vh;padding:27px 34px 46px;max-width:1800px}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:25px}.page-header h1{font-size:24px;line-height:1.2;margin:3px 0 2px;letter-spacing:-.4px}.page-header p{margin:0;color:var(--muted);font-size:12px}@media(max-width:780px){.sidebar{position:static;width:auto;padding:12px}.brand,.side-foot{display:none}.navigation{display:flex;overflow:auto}.navigation :deep(.el-menu-item){min-width:max-content}.app-main{margin:0;padding:20px}.page-header{align-items:flex-start;flex-direction:column;gap:14px}}
</style>
