<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">HISTORICAL SESSION METRICS</span><h1>历史会话指标计算</h1><p>选择 LiteLLM 普通会话，异步计算工具、脚本、Skill、错误、重试和伪造输出指标。</p></div></section>
    <el-alert v-if="health.store?.status!=='ok'" type="warning" :closable="false" show-icon title="MongoDB 指标库尚未就绪" :description="health.store?.detail||'请通过 Nacos 配置指标数据库后再提交计算任务。'"/>
    <el-card shadow="never" class="panel">
      <el-form label-position="top" @submit.prevent="search"><div class="filters">
        <el-form-item label="时间范围"><el-date-picker v-model="timeRange" type="datetimerange" range-separator="至" :clearable="false"/></el-form-item>
        <el-form-item label="End User"><el-input v-model="endUser" clearable/></el-form-item>
        <el-form-item label="Session ID"><el-input v-model="sessionId" clearable/></el-form-item>
        <el-form-item label="模型"><el-input v-model="model" clearable/></el-form-item>
        <el-form-item label="查询"><el-button type="primary" native-type="submit" :loading="loading">搜索</el-button></el-form-item>
      </div></el-form>
      <div class="actions"><el-button type="primary" :disabled="!selected.length||health.store?.status!=='ok'" :loading="submitting" @click="calculate">计算所选 {{selected.length}} 个会话</el-button><el-switch v-model="useJudge" active-text="使用 LLM Judge"/><span>默认查询最近 24 小时；一次最多选择 100 个会话。</span></div>
    </el-card>
    <el-card v-if="job" shadow="never" class="panel"><template #header><b>计算任务 {{job.job_id}}</b></template><el-progress :percentage="jobProgress" :status="job.failed?'exception':undefined"/><p>{{jobStatus(job.status)}}：成功 {{job.completed||0}}，失败 {{job.failed||0}}，共 {{job.total||0}}</p></el-card>
    <el-card shadow="never" class="panel">
      <el-table ref="table" :data="rows" row-key="root_session_id" v-loading="loading" @selection-change="selected=$event">
        <el-table-column type="selection" width="48"/>
        <el-table-column label="开始时间" width="175"><template #default="{row}">{{formatTime(row.started_at)}}</template></el-table-column>
        <el-table-column label="Session ID" min-width="250" show-overflow-tooltip prop="root_session_id"/>
        <el-table-column label="Agent" width="110"><template #default="{row}">{{row.agent||'未记录'}}</template></el-table-column>
        <el-table-column label="模型" min-width="170" show-overflow-tooltip><template #default="{row}">{{row.models?.join('、')||'未记录'}}</template></el-table-column>
        <el-table-column label="用户" width="130"><template #default="{row}">{{row.end_user||row.user_id||'未记录'}}</template></el-table-column>
        <el-table-column label="交互" width="75" align="right" prop="interaction_count"/>
        <el-table-column label="Token" width="105" align="right"><template #default="{row}">{{number(row.total_tokens)}}</template></el-table-column>
        <el-table-column label="指标状态" width="125"><template #default="{row}"><el-tag :type="row.metric_status==='rules_completed'?'success':'info'">{{metricStatus(row.metric_status)}}</el-tag></template></el-table-column>
        <el-table-column label="计算时间" width="175"><template #default="{row}">{{formatTime(row.metric_calculated_at)}}</template></el-table-column>
      </el-table>
      <el-pagination v-if="total" v-model:current-page="page" v-model:page-size="pageSize" background layout="total, sizes, prev, pager, next" :page-sizes="[20,50,100]" :total="total" class="pagination" @current-change="load" @size-change="changeSize"/>
    </el-card>
  </div>
</template>

<script setup>
import {computed,onBeforeUnmount,onMounted,ref} from 'vue'
import {ElMessage} from 'element-plus'
import {createMetricJob,fetchMetricHealth,fetchMetricJob,fetchMetricSessions} from '../api'
const now=Date.now(),timeRange=ref([new Date(now-24*60*60*1000),new Date(now)])
const endUser=ref(''),sessionId=ref(''),model=ref(''),rows=ref([]),total=ref(0),page=ref(1),pageSize=ref(20),selected=ref([]),loading=ref(false),submitting=ref(false),useJudge=ref(true),health=ref({}),job=ref(null)
let pollTimer
const params=()=>({start_time:timeRange.value[0].toISOString(),end_time:timeRange.value[1].toISOString(),end_user:endUser.value||undefined,session_id:sessionId.value||undefined,model:model.value||undefined,limit:pageSize.value,offset:(page.value-1)*pageSize.value})
async function load(){loading.value=true;try{const data=await fetchMetricSessions(params());rows.value=data.conversations||[];total.value=data.total||0}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{loading.value=false}}
async function search(){page.value=1;await load()}
async function changeSize(){page.value=1;await load()}
async function calculate(){submitting.value=true;try{job.value=await createMetricJob({session_ids:selected.value.map(row=>row.root_session_id),start_time:timeRange.value[0].toISOString(),end_time:timeRange.value[1].toISOString(),use_llm_judge:useJudge.value});poll()}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{submitting.value=false}}
async function poll(){clearTimeout(pollTimer);if(!job.value)return;try{job.value=await fetchMetricJob(job.value.job_id)}catch{}if(['queued','running'].includes(job.value?.status))pollTimer=setTimeout(poll,1500);else await load()}
const jobProgress=computed(()=>job.value?.total?Math.round(((job.value.completed||0)+(job.value.failed||0))/job.value.total*100):0)
const metricStatus=value=>({not_calculated:'未计算',rules_completed:'规则已完成',completed:'已完成',failed:'失败'}[value]||value||'未知')
const jobStatus=value=>({queued:'排队中',running:'计算中',completed:'已完成',completed_with_errors:'完成但有失败'}[value]||value)
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN'):'—',number=value=>Number(value||0).toLocaleString()
onMounted(async()=>{try{health.value=await fetchMetricHealth()}catch{}await load()})
onBeforeUnmount(()=>clearTimeout(pollTimer))
</script>

<style scoped>
.filters{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr 100px;align-items:end;gap:12px}.filters :deep(.el-form-item){margin:0}.filters :deep(.el-date-editor){width:100%}.filters .el-button{width:100%}.actions{display:flex;align-items:center;gap:18px;margin-top:16px}.actions span{color:var(--muted);font-size:12px}.pagination{display:flex;justify-content:flex-end;margin-top:18px}@media(max-width:1000px){.filters{grid-template-columns:1fr 1fr}.filters>:last-child{grid-column:1/-1}}@media(max-width:620px){.filters{grid-template-columns:1fr}.filters>:last-child{grid-column:auto}.actions{align-items:flex-start;flex-direction:column}}
</style>
