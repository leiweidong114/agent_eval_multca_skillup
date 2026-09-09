<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">SCHEMATIC CONVERSATIONS</span><h1>原理图生成总览</h1><p>按 LiteLLM End User、会话 ID 或模型检索完整用户、Agent 与工具交互记录。</p></div></section>

    <el-card shadow="never" class="panel search-panel">
      <el-form label-position="top" @submit.prevent="search">
        <div class="search-grid"><el-form-item label="LiteLLM End User"><el-select v-model="endUser" filterable clearable allow-create placeholder="选择或输入 End User"><el-option v-for="item in filterOptions.end_users" :key="item" :label="item" :value="item"/></el-select></el-form-item><el-form-item label="会话 ID"><el-input v-model="sessionId" clearable placeholder="输入完整 session_id" @keyup.enter="search"/></el-form-item><el-form-item label="模型"><el-select v-model="selectedModel" filterable clearable placeholder="全部模型"><el-option v-for="item in filterOptions.models" :key="item" :label="item" :value="item"/></el-select></el-form-item><el-form-item label="检索"><el-button native-type="submit" type="primary" :loading="loading">搜索</el-button></el-form-item></div>
      </el-form>
      <p class="search-note">默认显示最近请求；多个条件同时填写时按交集检索。交互每页 50 条，可继续加载；会话卡片统计匹配条件下最近 500 条数据库记录。原始请求和响应保留数据库中记录的内容并隐藏鉴权字段；未记录的内容无法补回。</p>
    </el-card>

    <template v-if="searched">
      <div class="overview-metrics"><div><span>匹配交互</span><b>{{data.count||0}}</b></div><div><span>会话数量</span><b>{{data.sessions?.length||0}}</b></div><div><span>总 Token</span><b>{{number(totalTokens)}}</b></div><div><span>涉及模型</span><b>{{models.length}}</b></div></div>
      <el-button v-if="data.has_more" :loading="loading" @click="search(true)">加载更多交互</el-button>

      <el-card v-if="data.sessions?.length" shadow="never" class="panel"><template #header><div class="section-head"><div><b>匹配会话</b><span>选择会话可继续精确检索</span></div></div></template>
        <div class="session-grid"><article v-for="session in data.sessions" :key="session.session_id" @click="selectSession(session)"><div><span>SESSION</span><b>{{session.session_id}}</b></div><p>{{session.interaction_count}} 轮 · {{number(session.total_tokens)}} tokens</p><div class="session-stats"><span>运行 {{duration(session.duration_ms)}}</span><span>工具 {{number(session.tool_call_count)}} 次</span><span>Subagent {{number(session.subagent_start_count)}} 次</span></div><small>End User：{{session.end_user||'未记录'}}<br/>{{session.models?.join('、')||'模型未记录'}} · {{formatTime(session.started_at)}}</small></article></div>
      </el-card>

      <el-card shadow="never" class="panel"><template #header><div class="section-head"><div><b>用户与 Agent 完整交互</b><span>按模型请求时间顺序展示输入消息、工具调用和 Agent 输出</span></div><el-tag effect="plain">{{data.interactions?.length||0}} 条</el-tag></div></template>
        <div v-if="data.interactions?.length" class="interaction-list">
          <article v-for="(item,index) in data.interactions" :key="item.request_id||index" class="interaction-card">
            <header><span class="sequence">{{index+1}}</span><div><b>{{item.model_group||item.model||'未知模型'}}</b><small>{{formatTime(item.start_time)}} · {{item.session_id||'未记录会话 ID'}}</small></div><div class="request-meta"><el-tag size="small" :type="item.status==='success'?'success':'danger'">{{item.status||'未知状态'}}</el-tag><span>{{duration(item.request_duration_ms)}}</span><span>{{number(item.total_tokens)}} tokens</span></div></header>
            <div class="conversation">
              <div v-for="(message,mIndex) in requestMessages(item)" :key="`m-${mIndex}`" class="message" :class="roleClass(message.role)"><span class="role">{{roleLabel(message.role)}}</span><div><p>{{contentText(message.content)}}</p><div v-if="message.tool_calls?.length" class="tool-list"><div v-for="tool in message.tool_calls" :key="tool.id||tool.function?.name"><b>工具调用 · {{tool.function?.name||tool.name}}</b><pre>{{readable(tool.function?.arguments||tool.arguments)}}</pre></div></div></div></div>
              <div v-for="(message,rIndex) in responseMessages(item)" :key="`r-${rIndex}`" class="message assistant"><span class="role">AGENT</span><div><p>{{contentText(message.content)}}</p><div v-if="message.tool_calls?.length" class="tool-list"><div v-for="tool in message.tool_calls" :key="tool.id||tool.function?.name"><b>工具调用 · {{tool.function?.name||tool.name}}</b><pre>{{readable(tool.function?.arguments||tool.arguments)}}</pre></div></div></div></div>
              <div v-if="errorText(item)" class="message error"><span class="role">ERROR</span><div><p>{{errorText(item)}}</p></div></div>
            </div>
            <footer><span>请求 {{item.request_id}}</span><span>Agent {{item.agent_id||'未记录'}}</span><span>输入 {{number(item.prompt_tokens)}} / 输出 {{number(item.completion_tokens)}} tokens</span><span>工具 {{number(item.tool_call_count)}} / Subagent {{number(item.subagent_start_count)}}</span></footer>
            <details class="raw-log"><summary>完整请求 / 响应 / 元数据（含工具参数与推理字段）</summary><pre>{{JSON.stringify(item,null,2)}}</pre></details>
          </article>
        </div>
        <el-empty v-else description="没有找到匹配的交互记录"/>
      </el-card>
    </template>
    <el-empty v-else description="选择筛选条件开始检索"/>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchSchematicInteractionFilters, fetchSchematicInteractions } from '../api'

const endUser=ref(''),sessionId=ref(''),selectedModel=ref(''),loading=ref(false),searched=ref(false),data=ref({sessions:[],interactions:[]}),filterOptions=ref({end_users:[],models:[]})
const totalTokens=computed(()=>data.value.interactions?.reduce((sum,row)=>sum+Number(row.total_tokens||0),0)||0)
const models=computed(()=>[...new Set((data.value.interactions||[]).map(row=>row.model_group||row.model).filter(Boolean))])

async function search(append=false){
  if(loading.value)return
  append=append===true;loading.value=true
  try{
    const next=await fetchSchematicInteractions({end_user:endUser.value.trim()||undefined,session_id:sessionId.value.trim()||undefined,model:selectedModel.value||undefined,limit:50,offset:append?data.value.next_offset:0})
    if(append){
      next.interactions=[...new Map([...data.value.interactions,...next.interactions].map(row=>[row.request_id,row])).values()]
      next.count=next.interactions.length
      const sessions=new Map()
      for(const row of next.interactions){
        const id=row.session_id||'未记录会话 ID'
        const session=sessions.get(id)||{session_id:id,user_id:row.user_id,end_user:row.end_user,agent_id:row.agent_id,models:[],interaction_count:0,total_tokens:0,tool_call_count:0,subagent_start_count:0,started_at:row.start_time,finished_at:row.end_time}
        session.models=[...new Set([...session.models,row.model_group||row.model].filter(Boolean))]
        session.interaction_count++;session.total_tokens+=Number(row.total_tokens||0);session.tool_call_count+=Number(row.tool_call_count||0);session.subagent_start_count+=Number(row.subagent_start_count||0)
        if(row.start_time&&(!session.started_at||new Date(row.start_time)<new Date(session.started_at)))session.started_at=row.start_time
        if(row.end_time&&(!session.finished_at||new Date(row.end_time)>new Date(session.finished_at)))session.finished_at=row.end_time
        session.duration_ms=session.started_at&&session.finished_at?Math.max(0,new Date(session.finished_at)-new Date(session.started_at)):null
        sessions.set(id,session)
      }
      next.sessions=[...sessions.values()]
    }
    data.value=next;searched.value=true
  }catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{loading.value=false}
}
onMounted(async()=>{try{filterOptions.value=await fetchSchematicInteractionFilters()}catch{}await search()})
function selectSession(session){sessionId.value=session.session_id==='未记录会话 ID'?'':session.session_id;if(sessionId.value)search()}
function objectValue(value){if(typeof value==='string'){try{return JSON.parse(value)}catch{return value}}return value}
function requestMessages(item){const raw=objectValue(item.proxy_server_request)||{};const request=objectValue(raw.body)||raw;const messages=objectValue(request.messages||item.messages||request.input);return Array.isArray(messages)?messages.map(m=>typeof m==='string'?{role:'user',content:m}:m):typeof messages==='string'?[{role:'user',content:messages}]:[]}
function responseMessages(item){const response=objectValue(item.response)||{};if(Array.isArray(response.choices))return response.choices.map(choice=>choice.message||choice.delta).filter(Boolean);if(Array.isArray(response.output))return response.output.map(item=>({role:item.role||'assistant',content:item.content||item.summary||item,tool_calls:item.type==='function_call'?[item]:[]}));return response.content?[{role:'assistant',content:response.content}]:[]}
function contentText(value){if(value==null||value==='')return'（无文本内容）';if(typeof value==='string')return value;if(Array.isArray(value))return value.map(part=>typeof part==='string'?part:(part.text||part.content||part.type||readable(part))).join('\n');return readable(value)}
function readable(value){if(value==null)return'—';if(typeof value==='string'){try{return readable(JSON.parse(value))}catch{return value}}if(Array.isArray(value))return value.map(readable).join('\n');return Object.entries(value).map(([key,item])=>`${key}: ${typeof item==='object'?readable(item):item}`).join('\n')}
function errorText(item){return item.metadata?.error_information?.error_message||item.metadata?.error_information?.error||''}
function roleLabel(role){return({user:'用户',assistant:'Agent',system:'系统',tool:'工具'}[role]||role||'消息').toUpperCase()}
function roleClass(role){return['user','assistant','system','tool'].includes(role)?role:'system'}
const number=value=>value==null?'—':Number(value).toLocaleString()
const duration=value=>value==null?'—':Number(value)>=1000?`${(Number(value)/1000).toFixed(1)} 秒`:`${value} ms`
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN'):'时间未记录'
</script>

<style scoped>
.raw-log{padding:16px}.raw-log pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:700px;overflow:auto;font-size:12px}
.search-panel :deep(.el-form-item){margin:0}.search-grid{display:grid;grid-template-columns:1fr 1fr 150px;align-items:end;gap:14px}.search-grid .el-button{width:100%}.search-note{margin:12px 0 0;color:var(--muted);font-size:12px}.overview-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.overview-metrics>div{display:flex;flex-direction:column;padding:18px 20px;background:var(--surface);border:1px solid var(--line);border-radius:12px}.overview-metrics span{color:var(--muted);font-size:12px}.overview-metrics b{font-size:25px;margin-top:6px}.section-head{display:flex;justify-content:space-between;align-items:center}.section-head>div{display:flex;flex-direction:column;gap:4px}.section-head span{color:var(--muted);font-size:12px}.session-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.session-grid article{padding:14px;border:1px solid var(--line);border-radius:9px;cursor:pointer;transition:.18s}.session-grid article:hover{border-color:var(--brand);background:var(--brand-soft)}.session-grid article div{display:flex;flex-direction:column;gap:4px}.session-grid span,.session-grid small{color:var(--muted);font-size:11px}.session-grid b{font-size:12px;word-break:break-all}.session-grid p{margin:11px 0 5px}.interaction-list{display:grid;gap:14px}.interaction-card{border:1px solid var(--line);border-radius:11px;overflow:hidden}.interaction-card>header{display:grid;grid-template-columns:32px minmax(0,1fr) auto;align-items:center;gap:10px;padding:13px 16px;background:var(--surface-2)}.sequence{display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:var(--brand-soft);color:var(--brand);font-weight:700;font-size:12px}.interaction-card header div{display:flex;flex-direction:column}.interaction-card header small{color:var(--muted);font-size:11px;margin-top:3px}.request-meta{flex-direction:row!important;align-items:center;gap:12px;color:var(--muted);font-size:12px}.conversation{display:grid;gap:10px;padding:16px}.message{display:grid;grid-template-columns:72px minmax(0,1fr);gap:12px;padding:13px 15px;border-radius:9px;background:var(--surface-2)}.message.user{background:#f0f6ff}.message.assistant{background:var(--brand-soft)}.message.tool{background:#fff8e8}.message.error{background:#fff0f0;color:var(--danger)}.message .role{font-size:10px;font-weight:700;letter-spacing:.06em;color:var(--muted)}.message p{margin:0;white-space:pre-wrap;line-height:1.65;overflow-wrap:anywhere}.tool-list{display:grid;gap:8px;margin-top:10px}.tool-list>div{border-left:3px solid #d6a23b;padding:8px 10px;background:rgba(255,255,255,.58)}.tool-list pre{white-space:pre-wrap;margin:6px 0 0;font-size:12px}.interaction-card>footer{display:flex;gap:18px;flex-wrap:wrap;padding:10px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}@media(max-width:900px){.search-grid,.overview-metrics,.session-grid{grid-template-columns:1fr 1fr}.search-grid>:last-child{grid-column:1/-1}.interaction-card>header{grid-template-columns:32px 1fr}.request-meta{grid-column:2;justify-content:flex-start}.message{grid-template-columns:1fr}.session-grid{grid-template-columns:1fr}}@media(max-width:600px){.search-grid,.overview-metrics{grid-template-columns:1fr}.search-grid>:last-child{grid-column:auto}}
.search-panel :deep(.el-select){width:100%}.search-grid{grid-template-columns:1.15fr 1fr 1fr 110px}.session-grid article .session-stats{display:flex;flex-direction:row;gap:10px;flex-wrap:wrap;margin:7px 0}.session-stats span{padding:3px 6px;border-radius:5px;background:var(--surface-2)}
@media(max-width:900px){.search-grid{grid-template-columns:1fr 1fr}}@media(max-width:600px){.search-grid{grid-template-columns:1fr}}
</style>
