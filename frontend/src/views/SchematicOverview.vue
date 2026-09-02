<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">SCHEMATIC CONVERSATIONS</span><h1>原理图生成总览</h1><p>按用户 ID 或会话 ID 检索数据库中的完整用户、Agent 与工具交互记录。</p></div></section>

    <el-card shadow="never" class="panel search-panel">
      <el-form label-position="top" @submit.prevent="search">
        <div class="search-grid"><el-form-item label="用户 ID"><el-input v-model="userId" clearable placeholder="例如 default_user_id" @keyup.enter="search"/></el-form-item><el-form-item label="会话 ID"><el-input v-model="sessionId" clearable placeholder="输入完整 session_id" @keyup.enter="search"/></el-form-item><el-form-item label="检索"><el-button native-type="submit" type="primary" :loading="loading">搜索交互记录</el-button></el-form-item></div>
      </el-form>
      <p class="search-note">两个条件同时填写时按交集检索；为保护页面性能，单次最多展示 500 条模型交互。</p>
    </el-card>

    <template v-if="searched">
      <div class="overview-metrics"><div><span>匹配交互</span><b>{{data.count||0}}</b></div><div><span>会话数量</span><b>{{data.sessions?.length||0}}</b></div><div><span>总 Token</span><b>{{number(totalTokens)}}</b></div><div><span>涉及模型</span><b>{{models.length}}</b></div></div>
      <el-alert v-if="data.truncated" type="warning" :closable="false" show-icon title="结果达到 500 条上限，请增加会话 ID 缩小范围"/>

      <el-card v-if="data.sessions?.length" shadow="never" class="panel"><template #header><div class="section-head"><div><b>匹配会话</b><span>选择会话可继续精确检索</span></div></div></template>
        <div class="session-grid"><article v-for="session in data.sessions" :key="session.session_id" @click="selectSession(session)"><div><span>SESSION</span><b>{{session.session_id}}</b></div><p>{{session.interaction_count}} 次交互 · {{number(session.total_tokens)}} tokens</p><small>{{session.models?.join('、')||'模型未记录'}} · {{formatTime(session.started_at)}}</small></article></div>
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
            <footer><span>请求 {{item.request_id}}</span><span>Agent {{item.agent_id||'未记录'}}</span><span>输入 {{number(item.prompt_tokens)}} / 输出 {{number(item.completion_tokens)}} tokens</span></footer>
          </article>
        </div>
        <el-empty v-else description="没有找到匹配的交互记录"/>
      </el-card>
    </template>
    <el-empty v-else description="输入用户 ID 或会话 ID 开始检索"/>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchSchematicInteractions } from '../api'

const userId=ref(''),sessionId=ref(''),loading=ref(false),searched=ref(false),data=ref({sessions:[],interactions:[]})
const totalTokens=computed(()=>data.value.interactions?.reduce((sum,row)=>sum+Number(row.total_tokens||0),0)||0)
const models=computed(()=>[...new Set((data.value.interactions||[]).map(row=>row.model_group||row.model).filter(Boolean))])

async function search(){if(!userId.value.trim()&&!sessionId.value.trim())return ElMessage.warning('请填写用户 ID 或会话 ID');loading.value=true;try{data.value=await fetchSchematicInteractions({user_id:userId.value.trim()||undefined,session_id:sessionId.value.trim()||undefined,limit:500});searched.value=true}catch(error){ElMessage.error(error.response?.data?.detail||error.message)}finally{loading.value=false}}
function selectSession(session){sessionId.value=session.session_id==='未记录会话 ID'?'':session.session_id;if(sessionId.value)search()}
function requestMessages(item){const request=item.proxy_server_request||{};const messages=Array.isArray(request.messages)?request.messages:Array.isArray(item.messages)?item.messages:[];return messages}
function responseMessages(item){const choices=item.response?.choices;return Array.isArray(choices)?choices.map(choice=>choice.message||choice.delta).filter(Boolean):[]}
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
.search-panel :deep(.el-form-item){margin:0}.search-grid{display:grid;grid-template-columns:1fr 1fr 150px;align-items:end;gap:14px}.search-grid .el-button{width:100%}.search-note{margin:12px 0 0;color:var(--muted);font-size:12px}.overview-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.overview-metrics>div{display:flex;flex-direction:column;padding:18px 20px;background:var(--surface);border:1px solid var(--line);border-radius:12px}.overview-metrics span{color:var(--muted);font-size:12px}.overview-metrics b{font-size:25px;margin-top:6px}.section-head{display:flex;justify-content:space-between;align-items:center}.section-head>div{display:flex;flex-direction:column;gap:4px}.section-head span{color:var(--muted);font-size:12px}.session-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.session-grid article{padding:14px;border:1px solid var(--line);border-radius:9px;cursor:pointer;transition:.18s}.session-grid article:hover{border-color:var(--brand);background:var(--brand-soft)}.session-grid article div{display:flex;flex-direction:column;gap:4px}.session-grid span,.session-grid small{color:var(--muted);font-size:11px}.session-grid b{font-size:12px;word-break:break-all}.session-grid p{margin:11px 0 5px}.interaction-list{display:grid;gap:14px}.interaction-card{border:1px solid var(--line);border-radius:11px;overflow:hidden}.interaction-card>header{display:grid;grid-template-columns:32px minmax(0,1fr) auto;align-items:center;gap:10px;padding:13px 16px;background:var(--surface-2)}.sequence{display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:var(--brand-soft);color:var(--brand);font-weight:700;font-size:12px}.interaction-card header div{display:flex;flex-direction:column}.interaction-card header small{color:var(--muted);font-size:11px;margin-top:3px}.request-meta{flex-direction:row!important;align-items:center;gap:12px;color:var(--muted);font-size:12px}.conversation{display:grid;gap:10px;padding:16px}.message{display:grid;grid-template-columns:72px minmax(0,1fr);gap:12px;padding:13px 15px;border-radius:9px;background:var(--surface-2)}.message.user{background:#f0f6ff}.message.assistant{background:var(--brand-soft)}.message.tool{background:#fff8e8}.message.error{background:#fff0f0;color:var(--danger)}.message .role{font-size:10px;font-weight:700;letter-spacing:.06em;color:var(--muted)}.message p{margin:0;white-space:pre-wrap;line-height:1.65;overflow-wrap:anywhere}.tool-list{display:grid;gap:8px;margin-top:10px}.tool-list>div{border-left:3px solid #d6a23b;padding:8px 10px;background:rgba(255,255,255,.58)}.tool-list pre{white-space:pre-wrap;margin:6px 0 0;font-size:12px}.interaction-card>footer{display:flex;gap:18px;flex-wrap:wrap;padding:10px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}@media(max-width:900px){.search-grid,.overview-metrics,.session-grid{grid-template-columns:1fr 1fr}.search-grid>:last-child{grid-column:1/-1}.interaction-card>header{grid-template-columns:32px 1fr}.request-meta{grid-column:2;justify-content:flex-start}.message{grid-template-columns:1fr}.session-grid{grid-template-columns:1fr}}@media(max-width:600px){.search-grid,.overview-metrics{grid-template-columns:1fr}.search-grid>:last-child{grid-column:auto}}
</style>
