<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">JUDGE OBSERVABILITY</span><h1>Judge 交互记录</h1><p>集中查看 Judge LLM 的完整输入、输出、Token 与耗时；这些记录不会混入原理图生成总览。</p></div><el-button :loading="loading" @click="load"><el-icon><Refresh/></el-icon>刷新</el-button></section>
    <el-card shadow="never" class="panel">
      <div class="filters"><el-select v-model="purpose" clearable placeholder="全部用途" @change="resetAndLoad"><el-option label="评测结果 Judge" value="evaluation_judge"/><el-option label="历史指标 Judge" value="session_metric_judge"/></el-select><el-input v-model="model" clearable placeholder="筛选模型" @keyup.enter="resetAndLoad"/><el-input v-model="contextId" clearable placeholder="搜索任务或 Session ID" @keyup.enter="resetAndLoad"/><el-button type="primary" @click="resetAndLoad">查询</el-button></div>
      <el-table :data="items" v-loading="loading" row-class-name="clickable-row" @row-click="openDetail">
        <el-table-column label="启动时间" width="180"><template #default="{row}">{{formatTime(row.started_at)}}</template></el-table-column>
        <el-table-column label="用途" width="140"><template #default="{row}"><el-tag effect="plain">{{purposeLabel(row.purpose)}}</el-tag></template></el-table-column>
        <el-table-column prop="context_id" label="任务 / Session ID" min-width="230" show-overflow-tooltip/>
        <el-table-column prop="model" label="Judge 模型" min-width="180" show-overflow-tooltip/>
        <el-table-column prop="user_id" label="用户" width="120"/>
        <el-table-column label="Token" width="95" align="right"><template #default="{row}">{{number(row.total_tokens)}}</template></el-table-column>
        <el-table-column label="耗时" width="105"><template #default="{row}">{{duration(row.duration_ms)}}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="row.status==='success'?'success':'danger'">{{row.status==='success'?'成功':'失败'}}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{row}"><el-button link type="primary" @click.stop="openDetail(row)">查看</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!loading&&!items.length" description="暂无 Judge 交互；新发起的 Judge 请求会自动记录在这里"/>
      <el-pagination v-if="total" class="pagination" background layout="total, sizes, prev, pager, next" :total="total" v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20,50,100]" @current-change="load" @size-change="resetAndLoad"/>
    </el-card>
    <el-dialog v-model="detailVisible" width="min(1180px,94vw)" top="4vh" destroy-on-close>
      <template #header><div class="dialog-title"><div><b>Judge LLM 交互详情</b><small>{{detail?.interaction_id}}</small></div><el-tag :type="detail?.status==='success'?'success':'danger'">{{detail?.status==='success'?'成功':'失败'}}</el-tag></div></template>
      <div v-loading="detailLoading" class="judge-detail" v-if="detail">
        <div class="facts"><div><span>用途</span><b>{{purposeLabel(detail.purpose)}}</b></div><div><span>模型</span><b>{{detail.model}}</b></div><div><span>任务 / Session</span><b>{{detail.context_id||'未记录'}}</b></div><div><span>耗时 / Token</span><b>{{duration(detail.duration_ms)}} / {{number(detail.usage?.total_tokens)}}</b></div></div>
        <section class="io input"><header><span>INPUT</span><b>Judge 模型输入</b></header><div class="input-grid"><article v-for="group in inputGroups" :key="group.key"><header><b>{{group.label}}</b><span>{{group.items.length}} 条</span></header><div class="messages"><pre v-for="(message,index) in group.items" :key="index">{{content(message)}}</pre></div></article></div></section>
        <section class="io output"><header><span>OUTPUT</span><b>Judge 模型输出</b></header><pre>{{detail.output?.content||detail.error||'没有输出内容'}}</pre></section>
        <details><summary>查看原始响应</summary><pre>{{pretty(detail.output?.response||{})}}</pre></details>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import {computed,onMounted,ref} from 'vue'
import {useRoute} from 'vue-router'
import {ElMessage} from 'element-plus'
import {fetchJudgeInteraction,fetchJudgeInteractions} from '../api'
const route=useRoute()
const items=ref([]),total=ref(0),page=ref(1),pageSize=ref(20),loading=ref(false),purpose=ref(''),model=ref(''),contextId=ref(String(route.query.context_id||'')),detailVisible=ref(false),detailLoading=ref(false),detail=ref(null)
const inputGroups=computed(()=>[{key:'system',label:'System',items:detail.value?.input?.system||[]},{key:'user',label:'User',items:detail.value?.input?.user||[]},{key:'history',label:'History',items:detail.value?.input?.history||[]},{key:'tool',label:'Tool',items:detail.value?.input?.tool||[]}].filter(group=>group.items.length))
async function load(){loading.value=true;try{const data=await fetchJudgeInteractions({limit:pageSize.value,offset:(page.value-1)*pageSize.value,purpose:purpose.value||undefined,model:model.value.trim()||undefined,context_id:contextId.value.trim()||undefined});items.value=data.items||[];total.value=data.total||0}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{loading.value=false}}
function resetAndLoad(){page.value=1;load()}
async function openDetail(row){detailVisible.value=true;detailLoading.value=true;detail.value=null;try{detail.value=await fetchJudgeInteraction(row.interaction_id)}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{detailLoading.value=false}}
const purposeLabel=value=>({evaluation_judge:'评测结果 Judge',session_metric_judge:'历史指标 Judge'}[value]||value||'未记录'),formatTime=value=>value?new Date(value).toLocaleString('zh-CN'):'—',number=value=>Number(value||0).toLocaleString(),duration=value=>value==null?'—':Number(value)<1000?`${Math.round(value)} ms`:`${(Number(value)/1000).toFixed(2)} s`,pretty=value=>JSON.stringify(value,null,2),content=message=>typeof message?.content==='string'?message.content:pretty(message?.content??message)
onMounted(load)
</script>

<style scoped>
.filters{display:grid;grid-template-columns:180px minmax(180px,1fr) minmax(240px,1.2fr) 90px;gap:12px;margin-bottom:18px}.pagination{display:flex;justify-content:flex-end;margin-top:18px}.dialog-title{display:flex;align-items:center;justify-content:space-between}.dialog-title>div{display:flex;flex-direction:column;gap:4px}.dialog-title small{color:var(--muted)}.judge-detail{display:grid;gap:16px;max-height:78vh;overflow:auto;padding-right:5px}.facts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.facts>div{display:flex;min-width:0;flex-direction:column;gap:5px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2)}.facts span{color:var(--muted);font-size:11px}.facts b{overflow-wrap:anywhere}.io{overflow:hidden;border:1px solid var(--line);border-radius:10px}.io>header{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--line)}.io>header span{padding:3px 8px;border-radius:5px;background:#eaf2ff;color:#326bb5;font:700 11px monospace}.io.output>header span{background:#e9f8ef;color:#198552}.input-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line)}.input-grid>article{min-width:0;background:var(--surface)}.input-grid>article>header{display:flex;justify-content:space-between;padding:10px 13px;border-bottom:1px solid var(--line)}.input-grid span{color:var(--muted);font-size:11px}.messages{max-height:430px;overflow:auto}.messages pre,.io.output>pre,details pre{margin:0;padding:13px;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}.messages pre+pre{border-top:1px solid var(--line)}details{padding:12px;border:1px solid var(--line);border-radius:9px}details summary{cursor:pointer;color:var(--brand)}details pre{max-height:420px;overflow:auto;background:#151a18;color:#e7eee9;margin-top:10px;border-radius:7px}@media(max-width:850px){.filters,.facts,.input-grid{grid-template-columns:1fr}}
</style>
