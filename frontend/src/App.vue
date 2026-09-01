<template>
  <router-view v-if="$route.meta.fullscreen" />
  <el-container v-else class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon size="26"><Monitor /></el-icon>
        <span>Agent Eval</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        class="menu"
      >
        <el-menu-item index="/run">
          <el-icon><VideoPlay /></el-icon>
          <span>评测运行</span>
        </el-menu-item>
        <el-menu-item index="/result">
          <el-icon><DataAnalysis /></el-icon>
          <span>评测结果</span>
        </el-menu-item>
        <el-menu-item index="/discovery">
          <el-icon><Grid /></el-icon>
          <span>Skill / Agent</span>
        </el-menu-item>
        <el-menu-item index="/skills">
          <el-icon><UploadFilled /></el-icon><span>Skill 版本</span>
        </el-menu-item>
        <el-menu-item index="/schematic">
          <el-icon><Connection /></el-icon><span>原理图评测</span>
        </el-menu-item>
        <el-menu-item index="/model-eval">
          <el-icon><Collection /></el-icon><span>模型与题库评测</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <span class="title">Agent Skill 评测平台</span>
        <el-tag type="success" size="small">本地评测 · PostgreSQL 过程数据</el-tag>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
</script>

<style>
* {
  box-sizing: border-box;
}
html,
body,
#app {
  height: 100%;
  margin: 0;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.layout {
  height: 100%;
}
.aside {
  background: #1f2d3d;
  color: #fff;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px;
  font-size: 18px;
  font-weight: 700;
  color: #fff;
}
.menu {
  border-right: none;
  background: #1f2d3d;
}
.menu .el-menu-item {
  color: #c0c4cc;
}
.menu .el-menu-item.is-active {
  color: #fff;
  background: #409eff;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e4e7ed;
  background: #fff;
}
.header .title {
  font-size: 16px;
  font-weight: 600;
}
.main {
  background: #f5f7fa;
}
</style>
