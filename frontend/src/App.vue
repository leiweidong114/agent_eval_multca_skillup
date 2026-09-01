<template>
  <el-container class="app-shell">
    <el-aside width="244px" class="sidebar">
      <div class="brand">
        <div class="brand-mark"><el-icon><DataAnalysis /></el-icon></div>
        <div><strong>Agent Eval</strong><span>Unified Evaluation</span></div>
      </div>
      <div class="nav-label">评测工作台</div>
      <el-menu :default-active="$route.path" router class="navigation">
        <el-menu-item index="/"><el-icon><House /></el-icon><span>首页</span></el-menu-item>
        <el-menu-item index="/evaluations/new"><el-icon><CirclePlus /></el-icon><span>新建评测</span></el-menu-item>
        <el-menu-item index="/benchmarks"><el-icon><Collection /></el-icon><span>题库管理</span></el-menu-item>
        <el-menu-item index="/skills"><el-icon><MagicStick /></el-icon><span>Skill 管理</span></el-menu-item>
        <el-menu-item index="/results"><el-icon><Histogram /></el-icon><span>评测结果</span></el-menu-item>
        <el-menu-item index="/runtimes"><el-icon><Cpu /></el-icon><span>模型与 Agent</span></el-menu-item>
      </el-menu>
      <div class="sidebar-status"><span class="status-dot" /><div><b>本地服务运行中</b><small>Skill · Benchmark · Schematic</small></div></div>
    </el-aside>
    <el-container class="workspace">
      <el-header class="topbar">
        <div><h1>{{ $route.meta.title }}</h1><p>{{ $route.meta.description }}</p></div>
        <div class="topbar-actions">
          <el-tag type="success" effect="plain" round>本地评测</el-tag>
          <el-button type="primary" @click="$router.push('/evaluations/new')"><el-icon><Plus /></el-icon>新建评测</el-button>
        </div>
      </el-header>
      <el-main class="content"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell{min-height:100vh}.sidebar{position:fixed;inset:0 auto 0 0;display:flex;flex-direction:column;background:#10182a;color:#fff;z-index:20}.workspace{margin-left:244px;min-width:0;background:#f4f6fa}.brand{display:flex;align-items:center;gap:12px;padding:26px 22px 24px}.brand-mark{display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,#6366f1,#38bdf8);font-size:21px;box-shadow:0 8px 24px rgba(99,102,241,.35)}.brand strong,.brand span{display:block}.brand strong{font-size:18px;letter-spacing:.2px}.brand span{margin-top:3px;color:#8290ac;font-size:10px;letter-spacing:1.3px;text-transform:uppercase}.nav-label{padding:8px 24px;color:#5f6e8b;font-size:11px;font-weight:700;letter-spacing:1px}.navigation{border:0;background:transparent;padding:0 12px}.navigation :deep(.el-menu-item){height:46px;margin:4px 0;border-radius:10px;color:#9ba8c1}.navigation :deep(.el-menu-item:hover){color:#fff;background:#18243b}.navigation :deep(.el-menu-item.is-active){color:#fff;background:linear-gradient(90deg,#4f46e5,#5b6ee1);box-shadow:0 8px 20px rgba(79,70,229,.24)}.sidebar-status{display:flex;gap:11px;align-items:center;margin:auto 18px 20px;padding:14px;border:1px solid #24314b;border-radius:12px;background:#141f34}.status-dot{width:9px;height:9px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 4px rgba(34,197,94,.12)}.sidebar-status b,.sidebar-status small{display:block}.sidebar-status b{font-size:12px}.sidebar-status small{margin-top:3px;color:#73819d;font-size:10px}.topbar{height:82px;display:flex;align-items:center;justify-content:space-between;padding:0 30px;border-bottom:1px solid #e6e9f0;background:rgba(255,255,255,.94);backdrop-filter:blur(10px)}.topbar h1{margin:0;color:#172033;font-size:20px}.topbar p{margin:5px 0 0;color:#7a8498;font-size:12px}.topbar-actions{display:flex;align-items:center;gap:12px}.content{padding:26px 30px 40px;overflow:visible}@media(max-width:900px){.sidebar{width:76px!important}.workspace{margin-left:76px}.brand>div:last-child,.nav-label,.navigation :deep(.el-menu-item span),.sidebar-status div{display:none}.brand{padding:22px 18px}.navigation{padding:0 10px}.navigation :deep(.el-menu-item){justify-content:center}.sidebar-status{justify-content:center;margin:auto 12px 18px}.topbar{padding:0 18px}.topbar p{display:none}.content{padding:18px}}
</style>
