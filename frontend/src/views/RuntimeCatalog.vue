<template>
  <div class="page-stack">
    <section class="hero compact">
      <div><span class="eyebrow">RUNTIME CATALOG</span><h1>模型与 Agent</h1><p>Agent 从本机环境发现，模型由 LiteLLM 同步；可在这里执行实际可用性检测。</p></div>
      <el-button @click="load" :loading="loading"><el-icon><Refresh/></el-icon> 重新发现</el-button>
    </section>

    <el-alert v-if="modelData.errors?.length" type="warning" show-icon :closable="false">
      <template #title>部分 LiteLLM 网关不可用，已展示配置文件中的回退模型</template>
      <div v-for="item in modelData.errors" :key="JSON.stringify(item)">{{item.base_url||item.gateway}}：{{item.error}}</div>
    </el-alert>

    <div class="runtime-summary">
      <div><span class="dot" :class="{on:detectedAgents.length}"/><p><b>{{detectedAgents.length}} / {{agents.length}}</b><small>本机发现 Agent</small></p></div>
      <div><span class="dot" :class="{on:verifiedAgentCount}"/><p><b>{{verifiedAgentCount}}</b><small>可用 Agent</small></p></div>
      <div><span class="dot" :class="{on:verifiedModelCount}"/><p><b>{{verifiedModelCount}} / {{models.length}}</b><small>可用模型</small></p></div>
      <div><span class="dot" :class="{on:databaseConnected}"/><p><b>{{databaseConnected?'正常':'未连接'}}</b><small>评测轨迹数据库</small></p></div>
    </div>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>本地 Agent</b><span>可用项自动排在前面</span></div></div></template>
      <el-table :data="sortedAgents" v-loading="loading">
        <el-table-column label="Agent" width="150"><template #default="{row}"><b>{{row.agent}}</b></template></el-table-column>
        <el-table-column label="可用状态" width="130"><template #default="{row}"><el-tag :type="row.detected_executable?'success':'info'">{{row.detected_executable?'可用':'未发现'}}</el-tag></template></el-table-column>
        <el-table-column prop="detected_executable" label="本地路径" min-width="260"><template #default="{row}"><code>{{row.detected_executable||row.default_command}}</code></template></el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>可用模型</b><span>{{modelData.litellm_available?'已从 LiteLLM 实时同步':'当前为本地配置回退'}}</span></div></div></template>
      <div class="toolbar"><el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索模型或 Profile"/><el-tag :type="modelData.litellm_available?'success':'warning'">{{modelData.litellm_available?'LiteLLM 在线':'使用回退配置'}}</el-tag></div>
      <el-table :data="filteredModels" max-height="560">
        <el-table-column prop="id" label="模型" min-width="250"><template #default="{row}"><b>{{row.id}}</b></template></el-table-column>
        <el-table-column label="可用状态" width="130"><template #default="{row}"><el-tag :type="row.source==='litellm'?'success':'warning'">{{row.source==='litellm'?'可用':'本地配置'}}</el-tag></template></el-table-column>
        <el-table-column label="来源" width="120"><template #default="{row}"><el-tag effect="plain">{{row.source}}</el-tag></template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { fetchAgents, fetchDatabaseHealth, fetchModels } from '../api'

const agents=ref([]),modelData=ref({models:[],gateways:[],errors:[]}),database=ref({}),keyword=ref(''),loading=ref(false)
const models=computed(()=>modelData.value.models||[])
const detectedAgents=computed(()=>agents.value.filter(x=>x.detected_executable))
const verifiedAgentCount=computed(()=>detectedAgents.value.length)
const verifiedModelCount=computed(()=>models.value.filter(x=>x.source==='litellm').length)
const databaseConnected=computed(()=>database.value.status==='ok'||database.value.ok===true)
const sortedAgents=computed(()=>[...agents.value].sort((a,b)=>Number(!a.detected_executable)-Number(!b.detected_executable)||a.agent.localeCompare(b.agent)))
const filteredModels=computed(()=>[...models.value].filter(x=>`${x.id} ${x.profile} ${x.owned_by}`.toLowerCase().includes(keyword.value.toLowerCase())).sort((a,b)=>Number(a.source!=='litellm')-Number(b.source!=='litellm')||a.id.localeCompare(b.id)))

async function load(){loading.value=true;const r=await Promise.allSettled([fetchAgents(),fetchModels(),fetchDatabaseHealth()]);agents.value=r[0].status==='fulfilled'?r[0].value:[];modelData.value=r[1].status==='fulfilled'?r[1].value:{models:[],gateways:[],errors:[{error:r[1].reason?.message}]};database.value=r[2].status==='fulfilled'?r[2].value:{};if(r.every(x=>x.status==='rejected'))ElMessage.error('无法读取运行环境');loading.value=false}
onMounted(load)
</script>

<style scoped>
.runtime-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.runtime-summary>div{display:flex;align-items:center;gap:13px;padding:18px;background:var(--surface);border:1px solid var(--line);border-radius:13px}.runtime-summary p{display:flex;flex-direction:column;margin:0}.runtime-summary b{font-size:20px}.runtime-summary small,.panel-title span{color:var(--muted)}.dot{width:12px;height:12px;border-radius:50%;background:#a6adba;box-shadow:0 0 0 5px rgba(166,173,186,.12)}.dot.on{background:#25a66a;box-shadow:0 0 0 5px rgba(37,166,106,.12)}.panel-title,.toolbar,.tags{display:flex;align-items:center;justify-content:space-between;gap:10px}.panel-title>div{display:flex;flex-direction:column;gap:3px}.toolbar{margin-bottom:16px}.toolbar .el-input{max-width:360px}.tags{justify-content:flex-start;flex-wrap:wrap}code{font-size:12px;color:var(--muted)}.health-message{display:block;margin-top:5px;color:var(--muted);line-height:1.4;word-break:break-word}@media(max-width:800px){.runtime-summary{grid-template-columns:repeat(2,1fr)}}
</style>
