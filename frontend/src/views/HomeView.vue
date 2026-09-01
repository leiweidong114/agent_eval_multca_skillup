<template>
  <div class="page-grid">
    <section class="welcome">
      <div><span class="eyebrow">UNIFIED EVALUATION CONSOLE</span><h2>从一个入口评测 Agent、模型与 Skill</h2><p>统一编排原理图生成、标准题库以及单个或多个 Skill 的联合能力。</p></div>
      <el-button type="primary" size="large" @click="$router.push('/evaluations/new')"><el-icon><CirclePlus /></el-icon>创建一次评测</el-button>
    </section>

    <div class="metric-grid" v-loading="loading">
      <div class="metric-card" style="--metric-bg:#eef2ff"><span class="metric-label">累计评测</span><b class="metric-value">{{ totalEvaluations }}</b><span class="metric-note">Skill 与题库实验</span></div>
      <div class="metric-card" style="--metric-bg:#dcfce7"><span class="metric-label">运行中</span><b class="metric-value">{{ runningCount }}</b><span class="metric-note">队列与执行任务</span></div>
      <div class="metric-card" style="--metric-bg:#e0f2fe"><span class="metric-label">可用题库</span><b class="metric-value">{{ installedBenchmarks }}</b><span class="metric-note">{{ benchmarkItems.toLocaleString() }} 道题目</span></div>
      <div class="metric-card" style="--metric-bg:#fef3c7"><span class="metric-label">Skill</span><b class="metric-value">{{ skills.length }}</b><span class="metric-note">支持单项与联合评测</span></div>
    </div>

    <div class="home-columns">
      <el-card shadow="never" class="surface">
        <template #header><div class="section-title"><div><h2>快速开始</h2><p>选择一种评测类型进入统一配置流程</p></div></div></template>
        <div class="quick-types">
          <button v-for="item in types" :key="item.value" @click="$router.push({path:'/evaluations/new',query:{type:item.value}})">
            <span :class="['quick-icon',item.value]"><el-icon><component :is="item.icon" /></el-icon></span>
            <span><b>{{ item.title }}</b><small>{{ item.description }}</small></span><el-icon class="arrow"><ArrowRight /></el-icon>
          </button>
        </div>
      </el-card>

      <el-card shadow="never" class="surface">
        <template #header><div class="section-title"><div><h2>最近评测</h2><p>跨类型显示最新运行状态</p></div><el-button link type="primary" @click="$router.push('/results')">全部结果</el-button></div></template>
        <div v-if="recent.length" class="recent-list">
          <div v-for="item in recent" :key="`${item.type}-${item.id}`" @click="openResult(item)">
            <span :class="['type-tag',`type-${item.type}`]">{{ typeName(item.type) }}</span>
            <span class="recent-main"><b>{{ item.name }}</b><small>{{ item.agent }} · {{ item.model }}</small></span>
            <el-tag size="small" :type="statusType(item.status)" effect="plain">{{ statusName(item.status) }}</el-tag>
          </div>
        </div>
        <div v-else class="empty-copy">暂无评测记录</div>
      </el-card>
    </div>

    <el-card shadow="never" class="surface">
      <template #header><div class="section-title"><div><h2>运行环境</h2><p>本地 Agent、LiteLLM 模型与任务容量</p></div><el-button link type="primary" @click="$router.push('/runtimes')">查看详情</el-button></div></template>
      <div class="environment-strip">
        <div><span class="env-dot ok"/><b>{{ detectedAgents }}</b><small>个本地 Agent 已检测</small></div>
        <div><span class="env-dot" :class="modelsAvailable?'ok':'warn'"/><b>{{ modelCount }}</b><small>个模型可选择</small></div>
        <div><span class="env-dot ok"/><b>{{ capacity.top_level_workers || '—' }}</b><small>个顶层并发任务</small></div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, markRaw, onMounted, ref } from 'vue'
import { Cpu, Connection, MagicStick } from '@element-plus/icons-vue'
import { fetchAgents, fetchBenchmarks, fetchCapacity, fetchExperiments, fetchJobs, fetchModels, fetchRuns, fetchSkills } from '../api'

const loading=ref(true),skills=ref([]),benchmarks=ref([]),jobs=ref([]),runs=ref([]),experiments=ref([]),agents=ref([]),models=ref({models:[]}),capacity=ref({})
const types=[
  {value:'schematic',title:'原理图评测',description:'使用原理图 Skill 验证生成质量',icon:markRaw(Connection)},
  {value:'question',title:'题库评测',description:'在标准或私有题库上比较模型',icon:markRaw(Cpu)},
  {value:'skill',title:'Skill 评测',description:'单 Skill 或多 Skill 联合执行',icon:markRaw(MagicStick)},
]
const installedBenchmarks=computed(()=>benchmarks.value.filter(x=>x.item_count>0).length)
const benchmarkItems=computed(()=>benchmarks.value.reduce((sum,x)=>sum+(x.item_count||0),0))
const totalEvaluations=computed(()=>runs.value.length+experiments.value.length)
const runningCount=computed(()=>jobs.value.filter(x=>['queued','running','cancelling'].includes(x.status)).length+experiments.value.filter(x=>['queued','running'].includes(x.status)).length)
const detectedAgents=computed(()=>agents.value.filter(x=>x.detected_executable).length)
const modelCount=computed(()=>models.value.models?.length||0)
const modelsAvailable=computed(()=>models.value.litellm_available)
const recent=computed(()=>[
  ...runs.value.map(x=>({type:x.report?.evaluation_type||((x.report?.skills||[]).includes('schematic-generation')?'schematic':'skill'),id:x.run_id,name:x.task_name||x.run_id,status:x.report?.status||'completed',agent:x.report?.agent||'—',model:x.report?.provider_model||x.report?.model||'—',created:x.report?.created_at||''})),
  ...experiments.value.map(x=>({type:'question',id:x.id,name:x.name,status:x.status,agent:x.track||'model_direct',model:'题库实验',created:x.created_at||''})),
].sort((a,b)=>String(b.created).localeCompare(String(a.created))).slice(0,6))
function typeName(type){return {skill:'Skill',schematic:'原理图',question:'题库'}[type]||type}
function statusName(status){return {completed:'已完成',running:'运行中',queued:'排队中',failed:'失败',cancelled:'已取消',interrupted:'已中断'}[status]||status}
function statusType(status){return status==='completed'?'success':['running','queued'].includes(status)?'primary':status==='failed'?'danger':'info'}
function openResult(item){location.href=`/results/${item.type}/${item.id}`}
onMounted(async()=>{try{const [skillData,benchmarkData,jobData,runData,experimentData,agentData,modelData,capacityData]=await Promise.all([fetchSkills(),fetchBenchmarks(),fetchJobs(),fetchRuns(),fetchExperiments(),fetchAgents(),fetchModels(),fetchCapacity()]);skills.value=skillData.skills||[];benchmarks.value=benchmarkData;jobs.value=jobData;runs.value=runData;experiments.value=experimentData;agents.value=agentData;models.value=modelData;capacity.value=capacityData}catch{}finally{loading.value=false}})
</script>

<style scoped>
.welcome{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:26px 28px;border-radius:16px;color:#fff;background:linear-gradient(115deg,#3036a7,#4f46e5 54%,#087ea4);box-shadow:0 16px 40px rgba(67,56,202,.18)}.welcome .eyebrow{color:#c7d2fe}.welcome h2{margin:8px 0 7px;font-size:24px}.welcome p{margin:0;color:#dbeafe;font-size:13px}.home-columns{display:grid;grid-template-columns:1fr 1.25fr;gap:18px}.quick-types{display:grid;gap:10px}.quick-types button{display:flex;align-items:center;gap:12px;width:100%;padding:13px;border:1px solid #edf0f5;border-radius:11px;background:#fff;text-align:left;cursor:pointer;transition:.18s}.quick-types button:hover{border-color:#c7d2fe;transform:translateY(-1px);box-shadow:0 8px 20px rgba(30,41,59,.06)}.quick-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;font-size:18px}.quick-icon.skill{color:#4f46e5;background:#eef2ff}.quick-icon.schematic{color:#0284c7;background:#e0f2fe}.quick-icon.question{color:#047857;background:#d1fae5}.quick-types b,.quick-types small{display:block}.quick-types b{font-size:13px}.quick-types small{margin-top:4px;color:#8b94a6;font-size:11px}.arrow{margin-left:auto;color:#a5adbc}.recent-list>div{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid #f0f2f6;cursor:pointer}.recent-list>div:last-child{border-bottom:0}.recent-main{min-width:0;flex:1}.recent-main b,.recent-main small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.recent-main b{font-size:13px}.recent-main small{margin-top:4px;color:#929bad;font-size:11px}.environment-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.environment-strip>div{display:grid;grid-template-columns:auto auto 1fr;align-items:center;gap:8px;padding:13px 16px;border-radius:10px;background:#f8fafc}.environment-strip b{font-size:18px}.environment-strip small{color:#8b94a6}.env-dot{width:8px;height:8px;border-radius:50%}.env-dot.ok{background:#22c55e}.env-dot.warn{background:#f59e0b}@media(max-width:1100px){.home-columns{grid-template-columns:1fr}}@media(max-width:680px){.welcome{align-items:flex-start;flex-direction:column}.environment-strip{grid-template-columns:1fr}}
</style>
