<template>
  <div class="page-stack">
    <div class="back-row"><el-button link @click="$router.push('/results')"><el-icon><ArrowLeft/></el-icon> 返回结果中心</el-button><el-button @click="load" :loading="loading"><el-icon><Refresh/></el-icon> 刷新</el-button></div>
    <el-skeleton v-if="loading&&!detail" :rows="8" animated/>
    <template v-else-if="detail">
      <section class="hero compact"><div><span class="eyebrow">{{typeLabel}} RESULT</span><h1>{{title}}</h1><p>评测 ID {{route.params.id}} · {{formatTime(detail.created_at)}} · {{statusText(detail.status||'completed')}}</p></div><el-tag size="large" :type="statusType(detail.status)">{{statusText(detail.status||'completed')}}</el-tag></section>

      <template v-if="route.params.type==='batch'">
        <div class="metric-grid"><div><span>评测组合</span><b>{{detail.total_jobs||0}}</b></div><div><span>已完成</span><b>{{detail.completed_jobs||0}}</b></div><div><span>最高得分</span><b>{{score(detail.best?.overall_score)}}</b></div><div><span>最佳组合</span><b class="compact-value">{{detail.best?`${detail.best.agent} × ${detail.best.model}`:'—'}}</b></div></div>
        <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>Agent × 模型对比报告</b><span>按综合得分排序，可下钻查看每个组合的完整轨迹与证据</span></div><el-tag effect="plain">{{detail.skills?.join('、')}}</el-tag></div></template>
          <el-table :data="batchRows" row-key="job_id"><el-table-column label="排名" width="76"><template #default="{row}"><span class="rank" :class="{top:row.rank===1}">{{row.rank||'—'}}</span></template></el-table-column><el-table-column prop="agent" label="Agent" width="120"/><el-table-column prop="model" label="模型" min-width="210"/><el-table-column label="综合" width="90"><template #default="{row}"><b>{{score(row.overall_score)}}</b></template></el-table-column><el-table-column label="结果" width="80"><template #default="{row}">{{score(row.result_score)}}</template></el-table-column><el-table-column label="过程" width="80"><template #default="{row}">{{score(row.process_score)}}</template></el-table-column><el-table-column label="Skill" width="80"><template #default="{row}">{{score(row.skill_quality_score)}}</template></el-table-column><el-table-column label="耗时" width="100"><template #default="{row}">{{durationText(row.duration_ms)}}</template></el-table-column><el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="statusType(row.status)">{{statusText(row.status)}}</el-tag></template></el-table-column><el-table-column label="操作" width="100"><template #default="{row}"><el-button link type="primary" @click="$router.push(`/results/${detail.evaluation_type||'skill'}/${row.job_id}`)">查看详情</el-button></template></el-table-column></el-table>
        </el-card>
        <el-card shadow="never" class="panel"><template #header><b>对比结论</b></template><div class="comparison-conclusion" v-if="detail.best"><el-icon><Trophy/></el-icon><div><b>{{detail.best.agent}} × {{detail.best.model}} 当前排名第一</b><p>综合得分 {{score(detail.best.overall_score)}}，结果质量 {{score(detail.best.result_score)}}，过程质量 {{score(detail.best.process_score)}}。排名基于同一任务、同一 Skill 与相同运行参数下的结果。</p></div></div><el-empty v-else :description="detail.status === 'completed' ? '所有组合均失败，无有效排名' : '组合仍在评测中，完成后生成对比结论'"/></el-card>
      </template>
      <template v-else-if="route.params.type==='question'">
        <div class="metric-grid"><div><span>执行结果</span><b>{{questionMetrics.count}}</b></div><div><span>通过率</span><b>{{percent(questionMetrics.score)}}</b></div><div><span>错误率</span><b>{{percent(questionMetrics.error_rate)}}</b></div><div><span>Token</span><b>{{number(questionMetrics.tokens)}}</b></div></div>
        <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>逐题评测过程</b><span>查看题目、响应、评分与错误证据</span></div><el-tag effect="plain">{{results.length}} 条</el-tag></div></template>
          <el-collapse class="case-list">
            <el-collapse-item v-for="(row,index) in results" :key="row.id||`${row.item_key}-${index}`" :name="index">
              <template #title><div class="case-title"><span class="case-index">{{index+1}}</span><b>{{row.item_key||row.benchmark_item_key||`题目 ${index+1}`}}</b><el-tag size="small" :type="row.passed?'success':'danger'">{{row.passed?'通过':'未通过'}}</el-tag><span class="case-meta">{{row.provider_name}} · {{durationText(row.duration_ms||row.latency_ms)}}</span></div></template>
              <div class="case-body"><article><span>模型响应</span><p>{{readable(row.output||row.response||row.error)}}</p></article><article><span>评分结果</span><p>得分 {{row.score??(row.passed?1:0)}}；{{row.error?`错误：${row.error}`:'评分执行完成'}}</p></article><div class="case-stats"><span>输入 {{number(row.input_tokens)}} tokens</span><span>输出 {{number(row.output_tokens)}} tokens</span><span>重复轮次 {{row.repeat??1}}</span></div></div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
        <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>模型表现汇总</b><span>通过率、时延与成本覆盖</span></div></div></template>
          <el-table :data="summaryRows"><el-table-column prop="provider_name" label="模型 / Agent" min-width="180"/><el-table-column prop="benchmark_name" label="题库" min-width="180"/><el-table-column label="通过率" width="100"><template #default="{row}">{{percent(row.score)}}</template></el-table-column><el-table-column prop="completed" label="完成" width="80"/><el-table-column label="P50 时延" width="110"><template #default="{row}">{{durationText(row.p50_latency_ms)}}</template></el-table-column><el-table-column label="P95 时延" width="110"><template #default="{row}">{{durationText(row.p95_latency_ms)}}</template></el-table-column><el-table-column label="证据量" width="110"><template #default="{row}">{{evidenceLabel(row.sample_size_status)}}</template></el-table-column></el-table>
          <el-empty v-if="!summaryRows.length" description="暂无汇总数据"/>
        </el-card>
        <el-card v-if="pairedRows.length" shadow="never" class="panel"><template #header><b>成对对比</b></template><el-table :data="pairedRows"><el-table-column label="对比对象" min-width="260"><template #default="{row}">{{row.left_name}} vs {{row.right_name}}</template></el-table-column><el-table-column prop="left_wins" label="左胜" width="80"/><el-table-column prop="right_wins" label="右胜" width="80"/><el-table-column prop="ties" label="持平" width="80"/><el-table-column label="显著性" width="140"><template #default="{row}"><el-tag :type="row.significant_005?'success':'info'">{{row.significant_005?'显著':'暂无显著差异'}}</el-tag></template></el-table-column></el-table></el-card>
      </template>

      <template v-else>
        <div class="metric-grid"><div><span>总体得分</span><b>{{score(detail.scores?.overall_score??detail.scoring?.overall_score)}}</b></div><div><span>结果质量</span><b>{{score(detail.scores?.result_dimension_score??dimensions.result?.score)}}</b></div><div><span>过程质量</span><b>{{score(detail.scores?.process_dimension_score??dimensions.process?.score)}}</b></div><div><span>Skill 质量</span><b>{{score(detail.scores?.skill_quality_dimension_score??detail.skill_quality?.score)}}</b></div></div>

        <el-card shadow="never" class="panel overview-card">
          <div class="run-overview"><div><span>Agent</span><b>{{detail.agent||'-'}}</b></div><div><span>模型</span><b>{{detail.provider_model||detail.model||'-'}}</b></div><div><span>评测 Skill</span><b>{{skillNames.join('、')||'-'}}</b></div><div><span>总耗时</span><b>{{durationText(detail.scores?.total_duration_ms)}}</b></div><div><span>总 Token</span><b>{{number(detail.scores?.total_tokens)}}</b></div><div><span>迭代次数</span><b>{{detail.iterations||1}}</b></div></div>
        </el-card>

        <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>评测过程与轨迹分析</b><span>从任务执行、工具调用和模型轨迹中提取</span></div><el-tag :type="traceStatus.type" effect="plain">{{traceStatus.label}}</el-tag></div></template>
          <div class="trajectory-grid">
            <div><span>工具调用</span><b>{{process.tool_calls??0}}</b><small>完成率 {{percent(process.tool_completion_rate)}}</small></div>
            <div><span>子 Agent 调用</span><b>{{process.subagent_calls??0}}</b><small>{{process.subagent_detection==='best_effort_tool_name_heuristic'?'启发式识别':'运行时记录'}}</small></div>
            <div><span>模型调用</span><b>{{process.model_call_count??'—'}}</b><small>成功率 {{percent(process.model_call_success_rate)}}</small></div>
            <div><span>错误事件</span><b>{{process.error_event_count??0}}</b><small>工具失败 {{process.tool_failures??0}}</small></div>
          </div>
          <el-timeline class="run-timeline">
            <el-timeline-item v-for="(item,index) in caseRows" :key="`${item.case_id}-${index}`" :type="caseType(item.status)" :timestamp="durationText(item.duration_ms)" placement="top">
              <div class="timeline-card"><div class="timeline-title"><b>{{item.title||item.case_id||`用例 ${index+1}`}}</b><el-tag size="small" :type="caseType(item.status)">{{caseStatus(item.status)}}</el-tag></div><p>{{item.prompt||'未记录任务输入'}}</p><div class="case-stats"><span>{{item.turns||0}} 轮</span><span>{{number(item.input_tokens)}} 输入 tokens</span><span>{{number(item.output_tokens)}} 输出 tokens</span><span>{{item.configuration==='without_skill'?'无 Skill 基线':'使用 Skill'}}</span></div></div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-if="!caseRows.length" description="本次运行未生成逐用例轨迹"/>
        </el-card>

        <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>结果与评分证据</b><span>逐用例查看输入、最终输出和判分依据</span></div></div></template>
          <el-collapse class="case-list" :model-value="caseRows.length===1?[0]:[]">
            <el-collapse-item v-for="(item,index) in caseRows" :key="`${item.case_id}-result-${index}`" :name="index">
              <template #title><div class="case-title"><span class="case-index">{{index+1}}</span><b>{{item.title||item.case_id}}</b><el-tag size="small" :type="caseType(item.status)">{{caseStatus(item.status)}}</el-tag><span class="case-meta">得分 {{caseScore(item)}} · {{durationText(item.duration_ms)}}</span></div></template>
              <div class="case-body"><article><span>任务输入</span><p>{{item.prompt||'—'}}</p></article><article class="answer"><span>Agent 最终输出</span><p>{{item.response||'未记录最终输出'}}</p></article><article v-if="item.error" class="error-box"><span>执行错误</span><p>{{item.error}}</p></article><article><span>评分结论</span><p>{{gradingText(item.grading)}}</p></article></div>
            </el-collapse-item>
          </el-collapse>
        </el-card>

        <el-card v-if="judge.status" shadow="never" class="panel">
          <template #header><div class="section-head"><div><b>LLM Judge</b><span>{{judge.model||judge.profile||'未记录 Judge 模型'}}</span></div><el-tag :type="judgeStatus.type">{{judgeStatus.label}}</el-tag></div></template>
          <el-alert v-if="judge.status==='unavailable'" type="warning" :closable="false" :title="judge.error||'Judge 当前不可用，综合评分已退回规则评分'"/>
          <el-descriptions v-else-if="judge.status==='completed'" :column="3" border>
            <el-descriptions-item label="Profile">{{judge.profile||'—'}}</el-descriptions-item><el-descriptions-item label="模型">{{judge.model||'—'}}</el-descriptions-item><el-descriptions-item label="Token">{{number(judge.usage?.total_tokens)}}</el-descriptions-item>
          </el-descriptions>
          <el-table v-if="judgeRows.length" :data="judgeRows" style="margin-top:14px"><el-table-column prop="name" label="维度" width="130"/><el-table-column label="分数" width="90"><template #default="{row}"><b>{{score(row.score)}}</b></template></el-table-column><el-table-column label="置信度" width="100"><template #default="{row}">{{percent(row.confidence)}}</template></el-table-column><el-table-column prop="reason" label="Judge 理由" min-width="260"/></el-table>
          <p v-if="judge.summary" class="judge-summary">{{judge.summary}}</p>
        </el-card>

        <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>Skill 质量</b><span>{{detail.skill_quality?.method||'结构化质量检查'}}</span></div><el-progress type="circle" :width="54" :stroke-width="6" :percentage="Number(detail.skill_quality?.score||0)"/></div></template>
          <div class="quality-list"><div v-for="item in detail.skill_quality?.details||[]" :key="item.check" :class="{passed:item.passed}"><el-icon><CircleCheckFilled v-if="item.passed"/><CircleCloseFilled v-else/></el-icon><span><b>{{qualityLabel(item.check)}}</b><small>{{qualityDescription(item)}}</small></span><em>+{{item.passed?item.weight:0}} / {{item.weight}}</em></div></div>
          <el-empty v-if="!detail.skill_quality?.details?.length" description="本次报告没有 Skill 质量检查数据"/>
        </el-card>
      </template>
    </template>
    <el-result v-else icon="error" title="无法读取评测结果" :sub-title="error"/>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchBatch, fetchExperiment, fetchExperimentComparison, fetchExperimentResults, fetchRun } from '../api'

const route=useRoute(),loading=ref(false),detail=ref(null),results=ref([]),comparison=ref({}),error=ref('')
const typeLabel=computed(()=>({question:'QUESTION BANK',schematic:'SCHEMATIC',skill:'SKILL',batch:'BATCH COMPARISON'}[route.params.type]||'EVALUATION'))
const title=computed(()=>detail.value?.name||detail.value?.task_name||`${({question:'题库',schematic:'原理图',skill:'Skill',batch:'批量对比'}[route.params.type])}评测结果`)
const batchRows=computed(()=>[...(detail.value?.results||[])].sort((a,b)=>(a.rank||999)-(b.rank||999)))
const summaryRows=computed(()=>Array.isArray(comparison.value?.summary)?comparison.value.summary:[])
const pairedRows=computed(()=>Array.isArray(comparison.value?.paired)?comparison.value.paired:[])
const questionMetrics=computed(()=>{const x=summaryRows.value.reduce((acc,row)=>({count:acc.count+Number(row.completed||0),passed:acc.passed+Number(row.passed||0),errors:acc.errors+Number(row.error_rate||0)*Number(row.completed||0),tokens:acc.tokens+Number(row.tokens||0)}),{count:0,passed:0,errors:0,tokens:0});return{...x,score:x.count?x.passed/x.count:0,error_rate:x.count?x.errors/x.count:0}})
const dimensions=computed(()=>detail.value?.scoring?.dimensions||{})
const process=computed(()=>detail.value?.process_metrics||{})
const judge=computed(()=>detail.value?.scoring?.llm_judge||{})
const judgeRows=computed(()=>Object.entries(judge.value.dimensions||{}).map(([name,value])=>({name:({result:'结果',process:'过程',skill_quality:'Skill 质量'}[name]||name),...value})))
const judgeStatus=computed(()=>({completed:{label:'已完成',type:'success'},disabled:{label:'已关闭',type:'info'},unavailable:{label:'不可用',type:'warning'}}[judge.value.status]||{label:judge.value.status||'未知',type:'info'}))
const skillNames=computed(()=>detail.value?.skills||[String(detail.value?.skill||'').split(/[\\/]/).filter(Boolean).pop()].filter(Boolean))
const caseRows=computed(()=>{const rows=[];for(const run of detail.value?.results||[])for(const item of run.case_results||[])rows.push(item);return rows})
const traceStatus=computed(()=>{const trace=detail.value?.database_trace||{};if(trace.status==='ok')return{label:'精确轨迹已关联',type:'success'};if(trace.status==='disabled')return{label:'数据库轨迹未启用',type:'info'};return{label:'基于运行报告分析',type:'warning'}})

async function load(){loading.value=true;error.value='';try{if(route.params.type==='question'){[detail.value,results.value,comparison.value]=await Promise.all([fetchExperiment(route.params.id),fetchExperimentResults(route.params.id),fetchExperimentComparison(route.params.id)])}else if(route.params.type==='batch')detail.value=await fetchBatch(route.params.id);else detail.value=await fetchRun(route.params.id)}catch(e){detail.value=null;error.value=e.response?.data?.detail||e.message;ElMessage.error(error.value)}finally{loading.value=false}}
const percent=n=>n===undefined||n===null?'-':`${Math.round((Number(n)<=1?Number(n):Number(n)/100)*100)}%`
const score=n=>n===undefined||n===null?'—':`${Math.round(Number(n)<=1?Number(n)*100:Number(n))}`
const number=n=>n===undefined||n===null?'—':Number(n).toLocaleString()
const durationText=n=>n===undefined||n===null?'—':Number(n)>=1000?`${(Number(n)/1000).toFixed(1)} 秒`:`${Math.round(Number(n))} ms`
const statusText=s=>({completed:'已完成',running:'运行中',queued:'排队中',failed:'失败',cancelled:'已取消'}[s]||s)
const statusType=s=>s==='failed'?'danger':s==='completed'||!s?'success':'warning'
const caseType=s=>String(s).toUpperCase()==='PASS'?'success':String(s).toUpperCase()==='ERROR'?'danger':'warning'
const caseStatus=s=>({PASS:'通过',FAIL:'未通过',ERROR:'执行错误'}[String(s).toUpperCase()]||s||'未知')
const caseScore=item=>item.grading?.summary?.pass_rate??(String(item.status).toUpperCase()==='PASS'?'100%':'0%')
const gradingText=g=>g?`判定：${caseStatus(g.status)}；执行 ${g.turns_executed??'—'} / ${g.turns_total??'—'} 轮；通过率 ${percent(g.summary?.pass_rate)}`:'未生成评分证据'
const readable=value=>value===undefined||value===null||value===''?'—':typeof value==='string'?value:Array.isArray(value)?value.map(readable).join('；'):Object.entries(value).map(([k,v])=>`${k}：${readable(v)}`).join('；')
const evidenceLabel=s=>({insufficient:'样本不足',exploratory:'探索性',adequate:'充分'}[s]||s||'—')
const qualityLabel=k=>({skill_md:'SKILL.md 完整性',name:'名称定义',description:'能力描述',workflow:'工作流程',constraints:'约束条件',output_contract:'输出契约',error_handling:'异常处理',verification:'验证方法'}[k]||k)
const qualityDescription=item=>({skill_md:'Skill 主说明文件存在且非空',name:'元数据中定义了明确名称',description:'元数据中描述了适用场景',workflow:'包含清晰的执行步骤',constraints:'明确说明边界与约束',output_contract:'定义输出或产物格式',error_handling:'说明失败与异常处理方式',verification:'说明如何验证执行结果'}[item.check]||item.description)
const formatTime=value=>value?new Date(value).toLocaleString():'时间未记录'
onMounted(load)
</script>

<style scoped>
.back-row{display:flex;justify-content:space-between}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric-grid>div{padding:20px;background:var(--surface);border:1px solid var(--line);border-radius:13px;display:flex;flex-direction:column;gap:8px}.metric-grid span,.run-overview span,.trajectory-grid span{color:var(--muted);font-size:12px}.metric-grid b{font-size:25px}.section-head,.timeline-title,.case-title{display:flex;align-items:center;justify-content:space-between;gap:10px}.section-head>div{display:flex;flex-direction:column;gap:4px}.section-head span{color:var(--muted);font-size:12px}.run-overview{display:grid;grid-template-columns:repeat(3,1fr);gap:0}.run-overview>div{padding:10px 18px;border-right:1px solid var(--line);display:flex;flex-direction:column;gap:6px;min-width:0}.run-overview>div:nth-child(3n){border-right:0}.run-overview>div:nth-child(n+4){margin-top:18px}.run-overview b{overflow-wrap:anywhere}.trajectory-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:26px}.trajectory-grid>div{display:flex;flex-direction:column;padding:15px;background:var(--brand-soft);border-radius:9px;gap:5px}.trajectory-grid b{font-size:22px}.trajectory-grid small{color:var(--muted)}.run-timeline{padding:4px 8px}.timeline-card{border:1px solid var(--line);border-radius:9px;padding:14px 16px;background:var(--surface)}.timeline-card p{color:var(--muted);white-space:pre-wrap;line-height:1.6}.case-title{width:100%;padding-right:16px;justify-content:flex-start}.case-index{display:grid;place-items:center;width:25px;height:25px;border-radius:50%;background:var(--brand-soft);color:var(--brand);font-size:12px}.case-meta{margin-left:auto;color:var(--muted);font-size:12px}.case-body{padding:6px 10px 18px}.case-body article{padding:14px 0;border-bottom:1px solid var(--line)}.case-body article>span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}.case-body p{white-space:pre-wrap;line-height:1.65;margin:7px 0 0;overflow-wrap:anywhere}.case-body .answer{background:var(--brand-soft);padding:16px;border-radius:9px;border:0}.case-body .error-box{color:var(--danger)}.case-stats{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;color:var(--muted);font-size:12px}.quality-list{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.quality-list>div{display:grid;grid-template-columns:24px 1fr auto;align-items:center;gap:8px;padding:13px;border:1px solid var(--line);border-radius:9px;color:var(--danger)}.quality-list>div.passed{color:var(--accent)}.quality-list span{display:flex;flex-direction:column;color:var(--text)}.quality-list small{color:var(--muted);margin-top:3px}.quality-list em{font-style:normal;font-size:12px;color:var(--muted)}@media(max-width:900px){.metric-grid,.trajectory-grid{grid-template-columns:repeat(2,1fr)}.run-overview,.quality-list{grid-template-columns:1fr}.run-overview>div{border-right:0;border-bottom:1px solid var(--line);margin-top:0!important}.case-meta{display:none}}
.metric-grid .compact-value{font-size:14px;overflow-wrap:anywhere}.rank{display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:var(--surface-2);font-weight:700}.rank.top{background:#e7f5ed;color:#17834f}.comparison-conclusion{display:flex;align-items:flex-start;gap:15px;padding:18px;background:var(--brand-soft);border-radius:10px}.comparison-conclusion>.el-icon{font-size:28px;color:#b78716}.comparison-conclusion p{margin:6px 0 0;color:var(--muted);line-height:1.6}
.judge-summary{margin:14px 0 0;padding:14px;background:var(--brand-soft);border-radius:9px;line-height:1.65}
</style>
