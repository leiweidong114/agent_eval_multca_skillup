<template>
  <div class="page-stack">
    <section class="hero compact">
      <div><span class="eyebrow">JUDGE OBSERVABILITY</span><h1>Judge 交互记录</h1><p>按 Judge 类型集中查看指标计算、任务评估、任务分类及原理图质量分析的完整输入输出。</p></div>
    </section>

    <el-card shadow="never" class="panel search-panel">
      <el-form label-position="top" @submit.prevent="search">
        <div class="search-grid">
          <el-form-item label="时间范围"><el-select v-model="timeRangePreset"><el-option v-for="item in timeRangeOptions" :key="item.value" :label="item.label" :value="item.value"/></el-select></el-form-item>
          <el-form-item label="Judge 类型"><el-select v-model="judgeType" clearable placeholder="全部类型"><el-option v-for="item in judgeTypeOptions" :key="item.value" :label="item.label" :value="item.value"/></el-select></el-form-item>
          <el-form-item label="用户 / 工号"><el-select v-model="userId" filterable clearable allow-create placeholder="输入或选择用户"><el-option v-for="item in filters.users" :key="item" :label="item" :value="item"/></el-select></el-form-item>
          <el-form-item label="任务 / Session ID"><el-input v-model="contextId" clearable placeholder="输入任务或会话 ID" @keyup.enter="search"/></el-form-item>
          <el-form-item label="Judge 模型"><el-select v-model="model" filterable clearable allow-create placeholder="全部模型"><el-option v-for="item in filters.models" :key="item" :label="item" :value="item"/></el-select></el-form-item>
          <el-form-item label="状态"><el-select v-model="status" clearable placeholder="全部状态"><el-option label="成功" value="success"/><el-option label="失败" value="failed"/></el-select></el-form-item>
          <el-form-item label="检索"><el-button native-type="submit" type="primary" :loading="loading">搜索</el-button></el-form-item>
        </div>
      </el-form>
      <p class="search-note">当前时间范围：{{ timeRangeLabel }}。Judge 类型来自请求写入的结构化标识，旧记录会根据 purpose 自动归类。</p>
    </el-card>

    <el-card shadow="never" class="panel">
      <el-table v-if="items.length" :data="items" v-loading="loading" row-class-name="clickable-row" @row-click="openDetail">
        <el-table-column label="启动时间" width="180"><template #default="{row}">{{formatTime(row.started_at)}}</template></el-table-column>
        <el-table-column label="Judge 类型" width="155"><template #default="{row}"><el-tag effect="plain">{{judgeTypeLabel(row.judge_type)}}</el-tag></template></el-table-column>
        <el-table-column prop="context_id" label="任务 / Session ID" min-width="230" show-overflow-tooltip/>
        <el-table-column prop="model" label="Judge 模型" min-width="180" show-overflow-tooltip/>
        <el-table-column prop="user_id" label="用户" width="120"/>
        <el-table-column label="Token" width="95" align="right"><template #default="{row}">{{number(row.total_tokens)}}</template></el-table-column>
        <el-table-column label="耗时" width="105"><template #default="{row}">{{duration(row.duration_ms)}}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="row.status==='success'?'success':'danger'">{{row.status==='success'?'成功':'失败'}}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{row}"><el-button link type="primary" @click.stop="openDetail(row)">查看</el-button></template></el-table-column>
      </el-table>
      <el-empty v-else-if="!loading&&hasSearched" description="没有找到匹配的 Judge 交互"/>
      <el-empty v-else-if="!loading" description="请设置查询条件后点击搜索"/>
      <el-pagination v-if="total" class="pagination" background layout="total, sizes, prev, pager, next, jumper" :total="total" v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20,50,100]" @current-change="load" @size-change="resetAndLoad"/>
    </el-card>
    <InteractionDetailDialog v-model="detailVisible" :item="detailItem" :turn-index="1" :loading="detailLoading" eyebrow="JUDGE INTERACTION" dialog-title="Judge LLM 交互详情" context-label="任务 / Session" :actor-label-override="detail?judgeTypeLabel(detail.judge_type):'Judge LLM'"/>
  </div>
</template>

<script setup>
import {computed,onMounted,ref} from 'vue'
import {useRoute} from 'vue-router'
import {ElMessage} from 'element-plus'
import {fetchJudgeInteraction,fetchJudgeInteractionFilters,fetchJudgeInteractions} from '../api'
import InteractionDetailDialog from '../components/InteractionDetailDialog.vue'

const route=useRoute()
const items=ref([]),total=ref(0),page=ref(1),pageSize=ref(20),loading=ref(false),hasSearched=ref(false)
const judgeType=ref(''),model=ref(''),contextId=ref(String(route.query.context_id||'')),userId=ref(''),status=ref('')
const filters=ref({models:[],users:[]}),detailVisible=ref(false),detailLoading=ref(false),detail=ref(null)
const timeRangeOptions=[{label:'最近 1 天',value:'1d',days:1},{label:'最近 1 周',value:'7d',days:7},{label:'最近 1 个月',value:'30d',days:30},{label:'不限时间',value:'all',days:null}]
const timeRangePreset=ref('1d')
const timeRangeLabel=computed(()=>timeRangeOptions.find(item=>item.value===timeRangePreset.value)?.label||'最近 1 天')
const judgeTypeOptions=[{label:'指标计算',value:'metric_calculation'},{label:'任务评估',value:'task_evaluation'},{label:'任务分类',value:'task_classification'},{label:'原理图质量指标分析',value:'schematic_rationality'}]
const detailItem=computed(()=>{const value=detail.value;if(!value)return null;const input=value.input||{},system=input.system||[],history=input.history||[],tools=input.tool||[],users=input.user||[],messages=[...system,...history,...tools,...users];const rawResponse=value.output?.response;const response=rawResponse&&Object.keys(rawResponse).length?rawResponse:(value.output?.content?{content:value.output.content}:{});return{request_id:value.interaction_id,model:value.model,model_group:value.model,session_id:value.context_id,start_time:value.started_at,request_duration_ms:value.duration_ms,prompt_tokens:value.usage?.prompt_tokens,completion_tokens:value.usage?.completion_tokens,total_tokens:value.usage?.total_tokens,status:value.status,error:value.error,proxy_server_request:{body:{messages}},current_input_messages:[...tools,...users],history_message_count:system.length+history.length,response}})

function timeRangeParams(){const option=timeRangeOptions.find(item=>item.value===timeRangePreset.value);if(!option?.days)return{};const end=new Date();return{start_time:new Date(end.getTime()-option.days*86400000).toISOString(),end_time:end.toISOString()}}
async function load(){loading.value=true;try{const data=await fetchJudgeInteractions({limit:pageSize.value,offset:(page.value-1)*pageSize.value,judge_type:judgeType.value||undefined,model:model.value.trim()||undefined,context_id:contextId.value.trim()||undefined,user_id:userId.value.trim()||undefined,status:status.value||undefined,...timeRangeParams()});items.value=data.items||[];total.value=data.total||0;hasSearched.value=true}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{loading.value=false}}
function search(){page.value=1;load()}
function resetAndLoad(){page.value=1;load()}
async function openDetail(row){detailVisible.value=true;detailLoading.value=true;detail.value=null;try{detail.value=await fetchJudgeInteraction(row.interaction_id)}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{detailLoading.value=false}}
const judgeTypeLabel=value=>({task_evaluation:'任务评估',metric_calculation:'指标计算',task_classification:'任务分类',schematic_rationality:'原理图质量指标分析'}[value]||value||'其他 Judge')
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN'):'—',number=value=>Number(value||0).toLocaleString(),duration=value=>value==null?'—':Number(value)<1000?`${Math.round(value)} ms`:`${(Number(value)/1000).toFixed(2)} s`
onMounted(async()=>{try{filters.value=await fetchJudgeInteractionFilters()}catch(error){ElMessage.warning(error.response?.data?.detail||error.message)}if(contextId.value)search()})
</script>

<style scoped>
.search-grid{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:4px 14px}.search-note{margin:4px 0 0;color:var(--muted);font-size:12px}.pagination{display:flex;justify-content:flex-end;margin-top:18px}@media(max-width:900px){.search-grid{grid-template-columns:1fr}}
</style>
