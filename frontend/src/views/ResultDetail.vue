<template>
  <div class="page-stack">
    <div class="back-row"><el-button link @click="$router.push('/results')"><el-icon><ArrowLeft/></el-icon> 返回结果中心</el-button><el-button @click="load" :loading="loading"><el-icon><Refresh/></el-icon> 刷新</el-button></div>
    <el-skeleton v-if="loading&&!detail" :rows="8" animated/>
    <template v-else-if="detail">
      <section class="hero compact"><div><span class="eyebrow">{{typeLabel}} RESULT</span><h1>{{title}}</h1><p>评测 ID {{route.params.id}} · {{statusText(detail.status||'completed')}}</p></div><el-tag size="large" :type="detail.status==='failed'?'danger':detail.status==='completed'||!detail.status?'success':'warning'">{{statusText(detail.status||'completed')}}</el-tag></section>
      <template v-if="route.params.type==='question'">
        <div class="metric-grid"><div><span>执行结果</span><b>{{summary.count||results.length}}</b></div><div><span>通过率</span><b>{{percent(summary.pass_rate??summary.accuracy)}}</b></div><div><span>平均得分</span><b>{{percent(summary.average_score??summary.mean_score)}}</b></div><div><span>已选题目</span><b>{{detail.selected_items||'-'}}</b></div></div>
        <el-card shadow="never" class="panel"><template #header><b>逐题结果与评分证据</b></template><el-table :data="results" max-height="580"><el-table-column prop="item_key" label="题目" width="130"/><el-table-column prop="provider_name" label="Provider" width="170"/><el-table-column label="得分" width="90"><template #default="{row}">{{row.score??'-'}}</template></el-table-column><el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.passed?'success':'danger'">{{row.passed?'通过':'未通过'}}</el-tag></template></el-table-column><el-table-column label="响应/错误" min-width="300"><template #default="{row}"><div class="evidence">{{row.output||row.response||row.error||'-'}}</div></template></el-table-column></el-table></el-card>
        <el-card shadow="never" class="panel"><template #header><b>对比统计</b></template><pre class="json">{{JSON.stringify(comparison,null,2)}}</pre></el-card>
      </template>
      <template v-else>
        <div class="metric-grid"><div><span>总体得分</span><b>{{runScore}}</b></div><div><span>Agent</span><b class="small-value">{{detail.agent||'-'}}</b></div><div><span>模型</span><b class="small-value">{{detail.model||'-'}}</b></div><div><span>Skills</span><b>{{detail.skills?.length||1}}</b></div></div>
        <el-card shadow="never" class="panel" v-if="detail.skills?.length"><template #header><b>参与评测的 Skill</b></template><div class="tags"><el-tag v-for="s in detail.skills" :key="s" effect="plain">{{s}}</el-tag></div></el-card>
        <el-card shadow="never" class="panel"><template #header><b>评测报告</b></template><pre class="json">{{JSON.stringify(detail,null,2)}}</pre></el-card>
      </template>
    </template>
    <el-result v-else icon="error" title="无法读取评测结果" :sub-title="error"/>
  </div>
</template>
<script setup>
import{computed,onMounted,ref}from'vue';import{useRoute}from'vue-router';import{ElMessage}from'element-plus';import{fetchExperiment,fetchExperimentComparison,fetchExperimentResults,fetchRun}from'../api'
const route=useRoute(),loading=ref(false),detail=ref(null),results=ref([]),comparison=ref({}),error=ref('');const typeLabel=computed(()=>({question:'QUESTION BANK',schematic:'SCHEMATIC',skill:'SKILL'}[route.params.type]||'EVALUATION'));const title=computed(()=>detail.value?.name||detail.value?.task_name||`${({question:'题库',schematic:'原理图',skill:'Skill'}[route.params.type])}评测结果`);const summary=computed(()=>detail.value?.summary||comparison.value?.summary||{});const runScore=computed(()=>{const n=detail.value?.summary?.overall_score??detail.value?.overall_score;return n===undefined?'-':Number(n)<=1?`${Math.round(n*100)}%`:Number(n).toFixed(1)})
async function load(){loading.value=true;error.value='';try{if(route.params.type==='question'){[detail.value,results.value,comparison.value]=await Promise.all([fetchExperiment(route.params.id),fetchExperimentResults(route.params.id),fetchExperimentComparison(route.params.id)])}else detail.value=await fetchRun(route.params.id)}catch(e){detail.value=null;error.value=e.response?.data?.detail||e.message;ElMessage.error(error.value)}finally{loading.value=false}}
const percent=n=>n===undefined||n===null?'-':`${Math.round((Number(n)<=1?Number(n):Number(n)/100)*100)}%`;const statusText=s=>({completed:'已完成',running:'运行中',queued:'排队中',failed:'失败',cancelled:'已取消'}[s]||s);onMounted(load)
</script>
<style scoped>
.back-row{display:flex;justify-content:space-between}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric-grid div{padding:20px;background:var(--surface);border:1px solid var(--line);border-radius:13px;display:flex;flex-direction:column;gap:8px}.metric-grid span{color:var(--muted);font-size:12px}.metric-grid b{font-size:25px}.metric-grid .small-value{font-size:15px;word-break:break-all}.evidence{white-space:pre-wrap;max-height:130px;overflow:auto}.json{margin:0;padding:18px;background:#111827;color:#d8e1f0;border-radius:10px;max-height:620px;overflow:auto;white-space:pre-wrap}.tags{display:flex;gap:8px;flex-wrap:wrap}@media(max-width:800px){.metric-grid{grid-template-columns:repeat(2,1fr)}}
</style>
