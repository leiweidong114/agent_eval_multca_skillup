<template>
  <div class="page-stack">
    <section class="hero compact">
      <div><span class="eyebrow">RUNTIME STATUS</span><h1>模型与 Agent</h1><p>查看本机 Agent 和 LiteLLM 模型的真实连通性，不展示内部模型注入配置。</p></div>
      <div class="hero-actions"><el-button @click="load" :loading="loading"><el-icon><Refresh/></el-icon> 刷新列表</el-button></div>
    </section>

    <el-alert v-if="modelData.errors?.length" type="warning" show-icon :closable="false" title="LiteLLM 模型目录读取不完整">
      <div v-for="item in modelData.errors" :key="JSON.stringify(item)">{{item.base_url||item.gateway}}：{{item.error}}</div>
    </el-alert>

    <div class="runtime-summary">
      <div><span class="dot" :class="{on:detectedAgents.length}"/><p><b>{{detectedAgents.length}}</b><small>本机发现 Agent</small></p></div>
      <div><span class="dot" :class="{on:modelData.litellm_available}"/><p><b>{{models.length}}</b><small>LiteLLM 可见模型</small></p></div>
      <div><span class="dot" :class="{on:availableModelCount}"/><p><b>{{availableModelCount}} / {{testedModelCount}}</b><small>实测可用 / 已测试</small></p></div>
      <div><span class="dot" :class="{on:databaseConnected}"/><p><b>{{databaseConnected?'正常':'未连接'}}</b><small>评测轨迹数据库</small></p></div>
    </div>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>本地 Agent</b><span>可用性测试会使用“设置”页面选择的默认模型发送 HI</span></div></div></template>
      <el-table :data="sortedAgents" v-loading="loading" max-height="560">
        <el-table-column label="Agent" width="170"><template #default="{row}"><b>{{row.agent}}</b></template></el-table-column>
        <el-table-column label="Agent 路径" min-width="460"><template #default="{row}"><el-input v-model="agentPathDrafts[row.agent]" clearable placeholder="输入可执行文件完整路径；留空恢复自动发现"/></template></el-table-column>
        <el-table-column label="是否可用" width="150"><template #default="{row}"><span class="state"><i class="state-dot" :class="agentState(row).type"/>{{agentState(row).label}}</span></template></el-table-column>
        <el-table-column label="操作" width="190"><template #default="{row}"><el-button type="primary" link :loading="savingAgent===row.agent" @click="savePath(row)">保存</el-button><el-button type="primary" link :loading="testingAgent===row.agent" :disabled="!row.detected_executable" @click="testAgent(row)">测试</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>可用模型</b><span>绿点和红点来自真实 HI 推理；仅出现在 /v1/models 不代表可调用</span></div><el-button type="primary" :loading="testingModels" @click="testAllModels">批量连通性测试</el-button></div></template>
      <div class="toolbar"><el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索模型"/><el-tag :type="modelData.litellm_available?'success':'warning'">{{modelData.litellm_available?'LiteLLM 目录在线':'目录读取失败'}}</el-tag></div>
      <el-table :data="filteredModels" max-height="620" v-loading="testingModels">
        <el-table-column label="连通性" width="120"><template #default="{row}"><span class="state"><i class="state-dot" :class="modelState(row).type"/>{{modelState(row).label}}</span></template></el-table-column>
        <el-table-column prop="id" label="模型" min-width="300"><template #default="{row}"><b>{{row.id}}</b></template></el-table-column>
        <el-table-column prop="owned_by" label="提供方" min-width="130"><template #default="{row}">{{row.owned_by||'—'}}</template></el-table-column>
        <el-table-column label="最近测试" min-width="190"><template #default="{row}">{{testTime(row.connectivity?.tested_at)}}<small v-if="row.connectivity?.probe_latency_ms"> · {{Math.round(row.connectivity.probe_latency_ms)}} ms</small></template></el-table-column>
        <el-table-column label="说明" min-width="260"><template #default="{row}">{{modelMessage(row)}}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{row}"><el-button link type="primary" :loading="testingModel===row.id" @click="testOneModel(row)">测试</el-button></template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { fetchAgents, fetchDatabaseHealth, fetchModels, saveAgentPath, testAgentAvailability, testAllModelAvailability, testModelAvailability } from '../api'

const agents=ref([]),modelData=ref({models:[],errors:[]}),database=ref({}),keyword=ref(''),loading=ref(false)
const agentTests=ref({}),agentPathDrafts=ref({}),savingAgent=ref(''),testingAgent=ref(''),testingModels=ref(false),testingModel=ref('')
const models=computed(()=>modelData.value.models||[])
const detectedAgents=computed(()=>agents.value.filter(x=>x.detected_executable))
const testedModelCount=computed(()=>models.value.filter(x=>typeof x.connectivity?.available==='boolean').length)
const availableModelCount=computed(()=>models.value.filter(x=>x.connectivity?.available===true).length)
const databaseConnected=computed(()=>database.value.status==='ok'||database.value.ok===true)
const sortedAgents=computed(()=>agents.value.slice().sort((a,b)=>a.agent.localeCompare(b.agent)))
const filteredModels=computed(()=>[...models.value].filter(x=>`${x.id} ${x.owned_by||''}`.toLowerCase().includes(keyword.value.toLowerCase())).sort((a,b)=>modelRank(a)-modelRank(b)||a.id.localeCompare(b.id)))

const modelRank=row=>row.connectivity?.available===true?0:row.connectivity?.available===false?2:1
function agentState(row){const tested=agentTests.value[row.agent];if(tested)return tested.ok?{type:'available',label:'可用'}:{type:'unavailable',label:'不可用'};return row.detected_executable?{type:'unknown',label:'待测试'}:{type:'unavailable',label:'未安装'}}
function modelState(row){return row.connectivity?.available===true?{type:'available',label:'可用'}:row.connectivity?.available===false?{type:'unavailable',label:'不可用'}:{type:'unknown',label:'未测试'}}
function modelMessage(row){const failure=row.connectivity?.failure;return row.connectivity?.available===true?'真实推理成功':failure?.detail||failure?.summary||(row.connectivity?.available===false?'推理请求失败':'等待连通性测试')}
function testTime(value){return value?new Date(value).toLocaleString('zh-CN'):'—'}
async function savePath(row){savingAgent.value=row.agent;try{const result=await saveAgentPath(row.agent,agentPathDrafts.value[row.agent]||'');ElMessage.success(`${row.agent} 路径已保存`);agentTests.value={...agentTests.value,[row.agent]:undefined};await load();agentPathDrafts.value={...agentPathDrafts.value,[row.agent]:result.configured_path||result.detected_executable||result.default_command||''}}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{savingAgent.value=''}}
async function testAgent(row){testingAgent.value=row.agent;try{const result=await testAgentAvailability(row.agent);agentTests.value={...agentTests.value,[row.agent]:result};ElMessage[result.ok?'success':'error'](`${row.agent}：${result.message}`)}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{testingAgent.value=''}}
async function testAllModels(){testingModels.value=true;try{const result=await testAllModelAvailability();ElMessage[result.available_model_count?'success':'warning'](`测试完成：${result.available_model_count||0} 个可用，${result.unavailable_model_count||0} 个不可用`);await load()}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{testingModels.value=false}}
async function testOneModel(row){testingModel.value=row.id;try{const result=await testModelAvailability(row.id,'litellm');row.connectivity={available:!!result.ok,tested_at:new Date().toISOString(),probe_latency_ms:result.duration_ms,failure:result.failure};ElMessage[result.ok?'success':'error'](`${row.id}：${result.message}`)}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{testingModel.value=''}}
async function load(){loading.value=true;const result=await Promise.allSettled([fetchAgents(),fetchModels(),fetchDatabaseHealth()]);agents.value=result[0].status==='fulfilled'?result[0].value:[];agentPathDrafts.value=Object.fromEntries(agents.value.map(row=>[row.agent,row.configured_path||row.detected_executable||row.default_command||'']));modelData.value=result[1].status==='fulfilled'?result[1].value:{models:[],errors:[{error:result[1].reason?.message}]};database.value=result[2].status==='fulfilled'?result[2].value:{};if(result.every(x=>x.status==='rejected'))ElMessage.error('无法读取运行环境');loading.value=false}
onMounted(load)
</script>

<style scoped>
.runtime-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.runtime-summary>div{display:flex;align-items:center;gap:13px;padding:18px;background:var(--surface);border:1px solid var(--line);border-radius:13px}.runtime-summary p{display:flex;flex-direction:column;margin:0;gap:5px}.runtime-summary b{font-size:20px}.runtime-summary small,.panel-title span{color:var(--muted)}.dot{width:12px;height:12px;border-radius:50%;background:#a6adba;box-shadow:0 0 0 5px rgba(166,173,186,.12)}.dot.on{background:#25a66a;box-shadow:0 0 0 5px rgba(37,166,106,.12)}.panel-title,.toolbar,.hero-actions{display:flex;align-items:center;justify-content:space-between;gap:12px}.panel-title>div{display:flex;flex-direction:column;gap:3px}.toolbar{margin-bottom:16px}.toolbar .el-input{max-width:360px}.state{display:inline-flex;align-items:center;gap:8px}.state-dot{width:9px;height:9px;border-radius:50%;background:#a6adba}.state-dot.available{background:#25a66a;box-shadow:0 0 0 4px rgba(37,166,106,.12)}.state-dot.unavailable{background:#d84a4a;box-shadow:0 0 0 4px rgba(216,74,74,.11)}.state-dot.unknown{background:#d59b2b;box-shadow:0 0 0 4px rgba(213,155,43,.12)}code{font-size:12px;color:var(--muted);overflow-wrap:anywhere}@media(max-width:900px){.runtime-summary{grid-template-columns:repeat(2,1fr)}}
</style>
