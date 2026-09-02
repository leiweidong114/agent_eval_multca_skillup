<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">EVALUATION HISTORY</span><h1>评测结果</h1><p>统一查看原理图、题库与 Skill 评测记录，并进入对应结果子页。</p></div><el-button @click="load" :loading="loading"><el-icon><Refresh /></el-icon> 刷新结果</el-button></section>
    <div class="result-metrics"><div v-for="metric in metrics" :key="metric.label"><span>{{metric.label}}</span><b>{{metric.value}}</b><small>{{metric.note}}</small></div></div>
    <el-card shadow="never" class="panel">
      <div class="toolbar"><el-segmented v-model="type" :options="types"/><el-input v-model="keyword" clearable :prefix-icon="Search" placeholder="搜索任务名称、模型或 ID"/></div>
      <el-table :data="filtered" v-loading="loading" @row-click="open" row-class-name="clickable-row">
        <el-table-column label="评测任务" min-width="260"><template #default="{row}"><div class="title-cell"><b>{{row.name}}</b><span>{{row.id}} · {{formatTime(row.created_at)}}</span></div></template></el-table-column>
        <el-table-column label="类型" width="120"><template #default="{row}"><el-tag effect="plain" :type="typeTag(row.type)">{{typeName(row.type)}}</el-tag></template></el-table-column>
        <el-table-column prop="agent" label="Agent" width="110"/>
        <el-table-column prop="model" label="模型" min-width="160"/>
        <el-table-column label="进度/得分" width="130"><template #default="{row}"><b>{{scoreText(row)}}</b></template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{statusText(row.status)}}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{row}"><el-button link type="primary" @click.stop="open(row)">查看</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!loading&&!filtered.length" description="暂无匹配的评测结果"/>
    </el-card>
  </div>
</template>
<script setup>
import{computed,onMounted,ref}from'vue';import{useRouter}from'vue-router';import{ElMessage}from'element-plus';import{Search}from'@element-plus/icons-vue';import{fetchBatches,fetchExperiments,fetchRuns}from'../api'
const router=useRouter(),rows=ref([]),loading=ref(false),type=ref('all'),keyword=ref('');const types=[{label:'全部',value:'all'},{label:'原理图',value:'schematic'},{label:'题库',value:'question'},{label:'Skill',value:'skill'},{label:'批量对比',value:'batch'}]
const runType=r=>(r.report?.skills||[]).includes('schematic-generation')?'schematic':(r.report?.evaluation_type||'skill')
const filtered=computed(()=>rows.value.filter(r=>(type.value==='all'||r.type===type.value)&&`${r.name} ${r.model} ${r.id}`.toLowerCase().includes(keyword.value.toLowerCase())))
const metrics=computed(()=>[{label:'全部记录',value:rows.value.length,note:'跨三类评测'},{label:'已完成',value:rows.value.filter(r=>r.status==='completed').length,note:'可查看完整证据'},{label:'运行中',value:rows.value.filter(r=>['running','queued'].includes(r.status)).length,note:'自动刷新状态'},{label:'成功率',value:`${rows.value.length?Math.round(rows.value.filter(r=>r.status==='completed').length/rows.value.length*100):0}%`,note:'完成记录占比'}])
async function load(){loading.value=true;try{const[a,b,c]=await Promise.allSettled([fetchRuns(),fetchExperiments(),fetchBatches()]);const runs=a.status==='fulfilled'?a.value:[],exps=b.status==='fulfilled'?b.value:[],batches=c.status==='fulfilled'?c.value:[];rows.value=[...batches.map(x=>({id:x.batch_id,name:x.name||'批量对比评测',type:'batch',agent:`${x.total_jobs||0} 个组合`,model:x.best?.model||'多模型',status:x.status,score:x.best?.overall_score,completed:x.completed_jobs,total:x.total_jobs,created_at:x.created_at})),...runs.map(r=>({id:r.run_id,name:r.task_name||r.report?.task_name||'Agent 评测',type:runType(r),agent:r.report?.agent||'-',model:r.report?.provider_model||r.report?.model||'-',status:r.report?.status||'completed',score:r.report?.scores?.overall_score??r.report?.summary?.overall_score??r.report?.overall_score,created_at:r.report?.started_at||r.report?.created_at})),...exps.map(e=>({id:e.id,name:e.name||'题库评测',type:'question',agent:e.track==='model_direct'?'direct':'codex',model:'题库评测',status:e.status,score:null,completed:e.completed_jobs,total:e.total_jobs,created_at:e.created_at}))].sort((x,y)=>String(y.created_at||'').localeCompare(String(x.created_at||'')))}catch(e){ElMessage.error(e.message)}finally{loading.value=false}}
function open(r){router.push(`/results/${r.type}/${r.id}`)}function typeName(t){return{schematic:'原理图',question:'题库',skill:'Skill',batch:'批量对比'}[t]}function typeTag(t){return{schematic:'success',question:'warning',skill:'primary',batch:'danger'}[t]||'info'}function statusText(s){return{completed:'已完成',running:'运行中',queued:'排队中',failed:'失败',cancelled:'已取消',canceled:'已取消'}[s]||s}function statusType(s){return s==='completed'?'success':s==='failed'?'danger':['queued','running'].includes(s)?'warning':'info'}function scoreText(r){if(r.score!==null&&r.score!==undefined)return Number(r.score)<=1?`${Math.round(r.score*100)}%`:Number(r.score).toFixed(1);if(r.total)return`${r.completed||0}/${r.total}`;return'-'}function formatTime(v){return v?new Date(v).toLocaleString('zh-CN'):'时间未知'}onMounted(load)
</script>
<style scoped>
.result-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.result-metrics div{display:flex;flex-direction:column;padding:18px 20px;background:var(--surface);border:1px solid var(--line);border-radius:13px}.result-metrics span,.result-metrics small,.title-cell span{color:var(--muted);font-size:12px}.result-metrics b{font-size:26px;margin:6px 0}.toolbar{display:flex;justify-content:space-between;gap:14px;margin-bottom:18px}.toolbar .el-input{max-width:340px}.title-cell{display:flex;flex-direction:column;gap:5px}@media(max-width:800px){.result-metrics{grid-template-columns:repeat(2,1fr)}.toolbar{flex-direction:column}.toolbar .el-input{max-width:none}}
</style>
