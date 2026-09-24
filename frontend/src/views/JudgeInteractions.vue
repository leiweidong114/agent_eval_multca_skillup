<template>
  <div class="page-stack">
    <section class="hero compact">
      <div><span class="eyebrow">JUDGE OBSERVABILITY</span><h1>Judge 交互记录</h1><p>按 Judge 类型集中查看指标计算、任务评估、任务分类及原理图质量分析的完整输入输出。</p></div>
      <div class="judge-test-actions">
        <div class="judge-model-state" :title="currentJudgeModel||'尚未配置 Judge 模型'">
          <span>当前 Judge 模型</span><b>{{currentJudgeModel||'未配置'}}</b>
          <em><i :class="judgeAvailability.type"/>{{judgeAvailability.label}}</em>
        </div>
        <el-button type="primary" @click="testingJudge?testVisible=true:testJudge()"><el-icon><Connection/></el-icon> {{testingJudge?'查看 Judge 测试进度':'Judge 模型可用性测试'}}</el-button>
      </div>
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
    <el-dialog v-model="testVisible" width="820px" title="Judge 模型可用性测试" append-to-body>
      <div class="judge-test-result">
        <div v-if="testJob" class="judge-test-live-head">
          <div><b>{{testingJudge?'正在测试 Judge 模型':'Judge 测试已结束'}}</b><span>已耗时 {{duration(testElapsedMs)}} · {{testJob.events?.length||0}} 条过程记录</span></div>
          <el-tag :type="testingJudge?'primary':testJob.status==='completed'?'success':'danger'">{{testingJudge?'运行中':testJob.status==='completed'?'通过':'失败'}}</el-tag>
        </div>
        <div v-if="testJob" class="judge-test-phases">
          <article v-for="phase in testPhases" :key="phase.key" :class="['judge-test-phase',phaseState(phase.key)]">
            <i class="phase-dot"/><div><b>{{phase.label}}</b><p>{{phaseMessage(phase.key)}}</p></div>
            <span>{{phaseDuration(phase.key)}}</span>
          </article>
        </div>
        <div v-if="testJob?.events?.length" class="judge-test-events">
          <b>实时测试过程</b>
          <div class="judge-test-event-list">
            <div v-for="event in testJob.events" :key="event.sequence" class="judge-test-event">
              <span>{{duration(event.elapsed_ms)}}</span><div><b>{{event.message}}</b><small v-if="eventNote(event)">{{eventNote(event)}}</small></div>
            </div>
          </div>
        </div>
        <el-alert v-if="testingJudge" type="info" :closable="false" show-icon title="正在等待真实模型响应" description="两项测试串行执行；单次网关请求最长等待 120 秒，限流或上游错误最多尝试 4 次。阶段和耗时持续更新，关闭弹窗或切换页面不会停止后端测试。"/>
        <el-alert v-if="testResult" :type="testResult.ok?'success':'error'" :closable="false" show-icon :title="testResult.message" :description="testResult.error||''"/>
        <template v-if="testResult">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="整体状态"><el-tag :type="testResult.ok?'success':'danger'">{{testResult.ok?'可用':'不可用'}}</el-tag></el-descriptions-item>
          <el-descriptions-item label="总耗时">{{duration(testResult.duration_ms)}}</el-descriptions-item>
          <el-descriptions-item label="测试 Session"><code>{{testResult.context_id||'—'}}</code></el-descriptions-item>
          <el-descriptions-item label="Judge 模型">{{testedJudgeModel}}</el-descriptions-item>
          <el-descriptions-item label="任务分类">{{testCheckStatus(testResult.checks?.task_classification)}}</el-descriptions-item>
          <el-descriptions-item label="会话指标 Judge">{{testCheckStatus(testResult.checks?.session_metric_judge)}}</el-descriptions-item>
          <el-descriptions-item label="分类失败原因" :span="2">{{testResult.checks?.task_classification?.error||'—'}}</el-descriptions-item>
          <el-descriptions-item label="指标失败原因" :span="2">{{testResult.checks?.session_metric_judge?.error||'—'}}</el-descriptions-item>
        </el-descriptions>
        <el-alert type="warning" :closable="false" title="通过代表当前配置和短会话 Judge 链路正常；长会话仍可能受到上下文长度、限流或后续额度变化影响。"/>
        <details><summary>查看完整测试返回</summary><pre>{{JSON.stringify(testResult,null,2)}}</pre></details></template>
      </div>
      <template #footer><el-button @click="testVisible=false">关闭</el-button><el-button type="primary" :loading="testingJudge" :disabled="testingJudge" @click="testJudge">重新测试</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import {computed,onBeforeUnmount,onMounted,ref} from 'vue'
import {useRoute} from 'vue-router'
import {ElMessage} from 'element-plus'
import {fetchJudgeInteraction,fetchJudgeInteractionFilters,fetchJudgeInteractions,fetchSettings,startJudgeTestJob,fetchJudgeTestJob} from '../api'
import InteractionDetailDialog from '../components/InteractionDetailDialog.vue'

const route=useRoute()
const items=ref([]),total=ref(0),page=ref(1),pageSize=ref(20),loading=ref(false),hasSearched=ref(false)
const judgeType=ref(''),model=ref(''),contextId=ref(String(route.query.context_id||'')),userId=ref(''),status=ref('')
const filters=ref({models:[],users:[]}),detailVisible=ref(false),detailLoading=ref(false),detail=ref(null)
const testingJudge=ref(false),testVisible=ref(false),testResult=ref(null),testJob=ref(null),clock=ref(Date.now())
const currentJudgeModel=ref('')
const testJobStorageKey='agent-eval-judge-test-job'
const testResultStorageKey='agent-eval-judge-test-result'
let pollTimer=null,clockTimer=null
const testPhases=[{key:'setup',label:'准备测试数据'},{key:'classification',label:'任务分类 Judge 推理'},{key:'metrics',label:'会话指标 Judge 推理'},{key:'finish',label:'汇总测试结果'}]
const timeRangeOptions=[{label:'最近 1 天',value:'1d',days:1},{label:'最近 1 周',value:'7d',days:7},{label:'最近 1 个月',value:'30d',days:30},{label:'不限时间',value:'all',days:null}]
const timeRangePreset=ref('1d')
const timeRangeLabel=computed(()=>timeRangeOptions.find(item=>item.value===timeRangePreset.value)?.label||'最近 1 天')
const judgeTypeOptions=[{label:'指标计算',value:'metric_calculation'},{label:'任务评估',value:'task_evaluation'},{label:'任务分类',value:'task_classification'},{label:'原理图质量指标分析',value:'schematic_rationality'}]
const detailItem=computed(()=>{const value=detail.value;if(!value)return null;const input=value.input||{},system=input.system||[],history=input.history||[],tools=input.tool||[],users=input.user||[],messages=[...system,...history,...tools,...users];const rawResponse=value.output?.response;const response=rawResponse&&Object.keys(rawResponse).length?rawResponse:(value.output?.content?{content:value.output.content}:{});return{request_id:value.interaction_id,model:value.model,model_group:value.model,session_id:value.context_id,start_time:value.started_at,request_duration_ms:value.duration_ms,prompt_tokens:value.usage?.prompt_tokens,completion_tokens:value.usage?.completion_tokens,total_tokens:value.usage?.total_tokens,status:value.status,error:value.error,proxy_server_request:{body:{messages}},current_input_messages:[...tools,...users],history_message_count:system.length+history.length,response}})
const testedJudgeModel=computed(()=>testResult.value?.checks?.task_classification?.model||testResult.value?.checks?.session_metric_judge?.models?.join('、')||'未返回')
const judgeAvailability=computed(()=>testingJudge.value?{type:'testing',label:'测试中'}:testResult.value?.ok===true?{type:'available',label:'连通正常'}:testResult.value?.ok===false?{type:'unavailable',label:'连通失败'}:{type:'untested',label:'未测试'})
const testElapsedMs=computed(()=>{if(testResult.value?.duration_ms!=null)return testResult.value.duration_ms;const start=testJob.value?.started_at||testJob.value?.created_at;return start?Math.max(0,clock.value-new Date(start).getTime()):0})
function phaseState(key){const events=(testJob.value?.events||[]).filter(item=>item.phase===key);if(!events.length)return'pending';const terminal=events.findLast(item=>item.stage==='completed'||item.stage==='failed');return terminal?.stage==='completed'?'success':terminal?.stage==='failed'?'failed':'running'}
function phaseMessage(key){const events=(testJob.value?.events||[]).filter(item=>item.phase===key);return events.at(-1)?.message||'等待开始'}
function phaseDuration(key){const events=(testJob.value?.events||[]).filter(item=>item.phase===key);const last=events.at(-1);if(last?.details?.duration_ms!=null)return duration(last.details.duration_ms);if(events.length&&phaseState(key)==='running')return duration(Math.max(0,testElapsedMs.value-events[0].elapsed_ms));return'—'}
function eventNote(event){const value=event.details||{};return [value.model?`模型 ${value.model}`:'',value.status_code?`HTTP ${value.status_code}`:'',value.attempt?`第 ${value.attempt}/${value.max_attempts||4} 次`:'',value.duration_ms!=null?`本次 ${duration(value.duration_ms)}`:'',value.wait_seconds!=null?`等待 ${value.wait_seconds} 秒`:'',value.error||value.result?.error||value.failure?.detail||''].filter(Boolean).join(' · ')}
function rememberTestResult(result){if(!result)return;localStorage.setItem(testResultStorageKey,JSON.stringify({model:currentJudgeModel.value||testedJudgeModel.value,result}))}

function timeRangeParams(){const option=timeRangeOptions.find(item=>item.value===timeRangePreset.value);if(!option?.days)return{};const end=new Date();return{start_time:new Date(end.getTime()-option.days*86400000).toISOString(),end_time:end.toISOString()}}
async function load(){loading.value=true;try{const data=await fetchJudgeInteractions({limit:pageSize.value,offset:(page.value-1)*pageSize.value,judge_type:judgeType.value||undefined,model:model.value.trim()||undefined,context_id:contextId.value.trim()||undefined,user_id:userId.value.trim()||undefined,status:status.value||undefined,...timeRangeParams()});items.value=data.items||[];total.value=data.total||0;hasSearched.value=true}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{loading.value=false}}
function search(){page.value=1;load()}
function resetAndLoad(){page.value=1;load()}
async function openDetail(row){detailVisible.value=true;detailLoading.value=true;detail.value=null;try{detail.value=await fetchJudgeInteraction(row.interaction_id)}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{detailLoading.value=false}}
async function pollJudgeTest(jobId){try{const job=await fetchJudgeTestJob(jobId);if(sessionStorage.getItem(testJobStorageKey)!==jobId)return;testJob.value=job;testResult.value=job.result||null;testingJudge.value=['queued','running'].includes(job.status);if(testingJudge.value){pollTimer=setTimeout(()=>pollJudgeTest(jobId),1000)}else{sessionStorage.removeItem(testJobStorageKey);rememberTestResult(job.result);ElMessage[job.result?.ok?'success':'error'](job.result?.message||'Judge 测试结束');fetchJudgeInteractionFilters().then(value=>{filters.value=value}).catch(()=>{});if(hasSearched.value)load()}}catch(error){if(error.response?.status===404){sessionStorage.removeItem(testJobStorageKey);testingJudge.value=false;testResult.value={ok:false,message:'测试任务已不存在，可能是后端重启',checks:{},error:error.response?.data?.detail||error.message};rememberTestResult(testResult.value);return}pollTimer=setTimeout(()=>pollJudgeTest(jobId),2000)}}
async function testJudge(){if(testingJudge.value)return;clearTimeout(pollTimer);testingJudge.value=true;testVisible.value=true;testResult.value=null;testJob.value=null;localStorage.removeItem(testResultStorageKey);try{const job=await startJudgeTestJob();testJob.value=job;sessionStorage.setItem(testJobStorageKey,job.job_id);await pollJudgeTest(job.job_id)}catch(error){testingJudge.value=false;testResult.value={ok:false,message:'无法启动 Judge 可用性测试',checks:{},error:error.response?.data?.detail||error.message};rememberTestResult(testResult.value);ElMessage.error(testResult.value.error)}}
const testCheckStatus=value=>value?.status==='completed'?'成功':value?.status==='unavailable'?'不可用':value?.status||'未执行'
const judgeTypeLabel=value=>({task_evaluation:'任务评估',metric_calculation:'指标计算',task_classification:'任务分类',schematic_rationality:'原理图质量指标分析'}[value]||value||'其他 Judge')
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN'):'—',number=value=>Number(value||0).toLocaleString(),duration=value=>value==null?'—':Number(value)<1000?`${Math.round(value)} ms`:`${(Number(value)/1000).toFixed(2)} s`
onMounted(async()=>{clockTimer=setInterval(()=>{clock.value=Date.now()},1000);const savedJobId=sessionStorage.getItem(testJobStorageKey);if(savedJobId){testingJudge.value=true;testVisible.value=true;pollJudgeTest(savedJobId)}const [filterResult,settingsResult]=await Promise.allSettled([fetchJudgeInteractionFilters(),fetchSettings()]);if(filterResult.status==='fulfilled')filters.value=filterResult.value;else ElMessage.warning(filterResult.reason?.response?.data?.detail||filterResult.reason?.message);if(settingsResult.status==='fulfilled')currentJudgeModel.value=settingsResult.value?.judge_model||'';else ElMessage.warning(settingsResult.reason?.response?.data?.detail||settingsResult.reason?.message);if(!savedJobId){try{const saved=JSON.parse(localStorage.getItem(testResultStorageKey)||'null');if(saved?.model&&saved.model===currentJudgeModel.value)testResult.value=saved.result||null}catch{localStorage.removeItem(testResultStorageKey)}}if(contextId.value)search()})
onBeforeUnmount(()=>{clearTimeout(pollTimer);clearInterval(clockTimer)})
</script>

<style scoped>
.judge-test-actions{display:flex;align-items:center;justify-content:flex-end;gap:12px;min-width:0}.judge-model-state{display:grid;grid-template-columns:auto auto;gap:3px 10px;align-items:center;max-width:360px;padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.judge-model-state>span{font-size:11px;color:var(--muted)}.judge-model-state>b{grid-row:2;max-width:245px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.judge-model-state>em{grid-column:2;grid-row:1/3;display:inline-flex;align-items:center;gap:7px;white-space:nowrap;color:var(--muted);font-size:12px;font-style:normal}.judge-model-state i{width:9px;height:9px;border-radius:50%;background:#d59b2b;box-shadow:0 0 0 4px rgba(213,155,43,.12)}.judge-model-state i.available{background:#25a66a;box-shadow:0 0 0 4px rgba(37,166,106,.12)}.judge-model-state i.unavailable{background:#d84a4a;box-shadow:0 0 0 4px rgba(216,74,74,.11)}.judge-model-state i.testing{background:#d59b2b;box-shadow:0 0 0 4px rgba(213,155,43,.12);animation:live-pulse 1.3s infinite}.search-grid{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:4px 14px}.search-note{margin:4px 0 0;color:var(--muted);font-size:12px}.pagination{display:flex;justify-content:flex-end;margin-top:18px}.judge-test-result{display:grid;gap:14px}.judge-test-result code{overflow-wrap:anywhere}.judge-test-result details summary{cursor:pointer;color:var(--brand)}.judge-test-result pre{max-height:360px;overflow:auto;padding:12px;border-radius:8px;background:#151a18;color:#e7eee9;white-space:pre-wrap;overflow-wrap:anywhere}.judge-test-live-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-radius:10px;background:var(--surface-2)}.judge-test-live-head>div{display:flex;flex-direction:column;gap:4px}.judge-test-live-head span,.judge-test-phase p,.judge-test-event small{color:var(--muted);font-size:12px}.judge-test-phases{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.judge-test-phase{display:grid;grid-template-columns:12px 1fr auto;gap:10px;align-items:start;padding:12px;border:1px solid var(--line);border-radius:9px}.judge-test-phase p{margin:4px 0 0}.judge-test-phase>span{font-size:12px;color:var(--muted)}.phase-dot{width:9px;height:9px;margin-top:5px;border-radius:50%;background:#a9b2ba}.judge-test-phase.running .phase-dot{background:#3b82f6;box-shadow:0 0 0 4px #eaf2ff;animation:live-pulse 1.3s infinite}.judge-test-phase.success .phase-dot{background:#22a06b}.judge-test-phase.failed .phase-dot{background:#d94c4c}.judge-test-events>b{display:block;margin-bottom:10px}.judge-test-event-list{max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:9px}.judge-test-event{display:grid;grid-template-columns:75px 1fr;gap:10px;padding:9px 12px;border-bottom:1px solid var(--line)}.judge-test-event:last-child{border:0}.judge-test-event>span{font-size:12px;color:var(--muted)}.judge-test-event>div{display:flex;flex-direction:column;gap:3px}.judge-test-event b{font-size:12px}.judge-test-event small{overflow-wrap:anywhere}@keyframes live-pulse{50%{opacity:.35}}@media(max-width:900px){.hero,.judge-test-actions{align-items:stretch;flex-direction:column}.judge-model-state{max-width:none}.search-grid,.judge-test-phases{grid-template-columns:1fr}}
</style>
