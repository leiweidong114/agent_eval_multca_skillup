<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo"><el-icon size="26"><Monitor /></el-icon><span>Agent Eval</span></div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item index="/"><el-icon><House /></el-icon><span>首页</span></el-menu-item>
        <el-menu-item index="/evaluations/new"><el-icon><VideoPlay /></el-icon><span>新建评测</span></el-menu-item>
        <el-menu-item index="/benchmarks"><el-icon><Collection /></el-icon><span>题库管理</span></el-menu-item>
        <el-menu-item index="/skills"><el-icon><MagicStick /></el-icon><span>Skill 管理</span></el-menu-item>
        <el-menu-item index="/results"><el-icon><DataAnalysis /></el-icon><span>评测结果</span></el-menu-item>
        <el-menu-item index="/runtimes"><el-icon><Grid /></el-icon><span>模型与 Agent</span></el-menu-item>
      </el-menu>
    </el-aside>
    <el-container class="workspace">
      <el-header class="header">
        <div><span class="title">Agent Skill 评测平台</span><span class="separator">/</span><span class="page-title">{{ $route.meta.title }}</span></div>
        <el-tag type="success" size="small">本地评测 · PostgreSQL 过程数据</el-tag>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const activeMenu = computed(() => route.path.startsWith('/results/') ? '/results' : route.path)
</script>
<style scoped>
.layout{min-height:100vh}.aside{position:fixed;inset:0 auto 0 0;background:#1f2d3d;color:#fff;z-index:20}.workspace{min-width:0;margin-left:220px}.logo{display:flex;align-items:center;gap:10px;padding:20px;font-size:18px;font-weight:700;color:#fff}.menu{border-right:0;background:#1f2d3d}.menu :deep(.el-menu-item){color:#c0c4cc}.menu :deep(.el-menu-item:hover){color:#fff;background:#26394d}.menu :deep(.el-menu-item.is-active){color:#fff;background:#409eff}.header{height:60px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e4e7ed;background:#fff}.title{font-size:16px;font-weight:600}.separator{margin:0 10px;color:#c0c4cc}.page-title{color:#909399;font-size:14px}.main{padding:20px;background:#f5f7fa}@media(max-width:760px){.aside{width:64px!important}.workspace{margin-left:64px}.logo{justify-content:center;padding:20px 0}.logo span,.menu :deep(.el-menu-item span){display:none}.menu :deep(.el-menu-item){justify-content:center;padding:0!important}.header{padding:0 14px}.header .el-tag,.separator,.page-title{display:none}.main{padding:12px}}
</style>
