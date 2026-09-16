<template>
  <el-dialog v-model="visible" width="92%" top="4vh" append-to-body destroy-on-close class="interaction-detail-dialog">
    <template #header>
      <div class="dialog-heading">
        <div><span>MODEL INTERACTION</span><b>第 {{ turnIndex || '—' }} 轮交互详情</b><small>{{ item?.request_id || '请求 ID 未记录' }}</small></div>
        <div class="heading-tags"><el-tag :type="statusType">{{ statusLabel }}</el-tag><el-tag effect="plain">{{ actorLabel }}</el-tag></div>
      </div>
    </template>

    <div v-if="item" class="dialog-body">
      <div class="facts">
        <div><span>模型</span><b>{{ item.model_group || item.model || '未记录' }}</b></div>
        <div><span>会话</span><code>{{ item.session_id || '未记录' }}</code></div>
        <div><span>开始时间</span><b>{{ formatTime(item.start_time) }}</b></div>
        <div><span>耗时</span><b>{{ duration(item.request_duration_ms) }}</b></div>
        <div><span>Token</span><b>{{ number(item.prompt_tokens) }} + {{ number(item.completion_tokens) }} = {{ number(item.total_tokens) }}</b></div>
      </div>

      <el-collapse v-model="sections" class="primary-sections">
        <el-collapse-item name="tools">
          <template #title><div class="section-title"><b>Tools</b><span>可用 {{ toolRows.length }} 个 · 本轮调用 {{ calledTools.length }} 次</span></div></template>
          <el-collapse v-if="toolRows.length" v-model="expandedTools" class="tool-rows">
            <el-collapse-item v-for="(tool,index) in toolRows" :key="`${tool.name}-${index}`" :name="`${tool.name}-${index}`">
              <template #title><div class="tool-title"><b>{{ tool.name }}</b><el-tag size="small" :type="tool.called?'success':'info'">{{ tool.called?'本轮已调用':'本轮未调用' }}</el-tag><span v-if="tool.calls.length">{{ tool.calls.length }} 次</span></div></template>
              <p v-if="tool.description" class="tool-description">{{ tool.description }}</p>
              <div v-if="tool.calls.length" class="tool-call-list"><article v-for="(call,callIndex) in tool.calls" :key="call.id||callIndex"><b>调用 {{ callIndex+1 }}</b><pre>{{ formatted(call.arguments) }}</pre></article></div>
              <div v-if="tool.results.length" class="tool-result-list"><article v-for="(result,resultIndex) in tool.results" :key="resultIndex"><b>工具返回 {{ resultIndex+1 }}</b><pre>{{ contentText(result.content??result.output??result) }}</pre></article></div>
              <details v-if="tool.parameters&&Object.keys(tool.parameters).length"><summary>查看工具参数定义</summary><pre>{{ formatted(tool.parameters) }}</pre></details>
            </el-collapse-item>
          </el-collapse>
          <el-empty v-else :image-size="52" description="该轮没有保存工具定义或工具调用"/>
        </el-collapse-item>

        <el-collapse-item name="request-response">
          <template #title><div class="section-title"><b>Request &amp; Response</b><span>Input / Output</span></div></template>
          <section class="io-block">
            <header><b>Input</b><span>{{ requestMessages.length }} 条消息 · {{ number(item.prompt_tokens) }} tokens</span></header>
            <el-collapse v-model="inputSections" class="nested-sections">
              <el-collapse-item v-if="classified.system.length" name="system"><template #title><div class="nested-title"><b>System</b><span>{{ classified.system.length }} 条 · 默认折叠</span></div></template><MessageList :messages="classified.system" tone="system"/></el-collapse-item>
              <el-collapse-item v-if="classified.history.length" name="history"><template #title><div class="nested-title"><b>History</b><span>{{ classified.history.length }} 条 · 默认折叠</span></div></template><MessageList :messages="classified.history" tone="history"/></el-collapse-item>
              <el-collapse-item v-if="classified.user.length" name="user"><template #title><div class="nested-title"><b>User</b><span>{{ classified.user.length }} 条</span></div></template><MessageList :messages="classified.user" tone="user"/></el-collapse-item>
              <el-collapse-item v-if="classified.tool.length" name="input-tools"><template #title><div class="nested-title"><b>Tool History</b><span>{{ classified.tool.length }} 条</span></div></template><MessageList :messages="classified.tool" tone="tool"/></el-collapse-item>
            </el-collapse>
            <el-empty v-if="!requestMessages.length" :image-size="48" description="本轮没有保存输入内容"/>
          </section>

          <section class="io-block output-block">
            <header><b>Output</b><span>{{ number(item.completion_tokens) }} tokens</span></header>
            <el-collapse v-model="outputSections" class="nested-sections">
              <el-collapse-item v-if="reasoningOutputs.length" name="reasoning"><template #title><div class="nested-title"><b>思考输出</b><span>{{ reasoningOutputs.length }} 条</span></div></template><MessageList :messages="reasoningOutputs" tone="reasoning"/></el-collapse-item>
              <el-collapse-item v-if="normalOutputs.length" name="answer"><template #title><div class="nested-title"><b>模型输出</b><span>{{ normalOutputs.length }} 条</span></div></template><MessageList :messages="normalOutputs" tone="assistant"/></el-collapse-item>
              <el-collapse-item v-if="calledTools.length" name="commands"><template #title><div class="nested-title"><b>工具调用命令</b><span>{{ calledTools.length }} 次</span></div></template><div class="tool-call-list"><article v-for="(call,index) in calledTools" :key="call.id||index"><b>{{ call.name }}</b><pre>{{ formatted(call.arguments) }}</pre></article></div></el-collapse-item>
            </el-collapse>
            <el-empty v-if="!reasoningOutputs.length&&!normalOutputs.length&&!calledTools.length" :image-size="48" description="本轮没有保存模型输出"/>
          </section>
        </el-collapse-item>
      </el-collapse>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, defineComponent, h, ref, watch } from 'vue'

const props=defineProps({modelValue:Boolean,item:{type:Object,default:null},turnIndex:{type:Number,default:0}})
const emit=defineEmits(['update:modelValue'])
const visible=computed({get:()=>props.modelValue,set:value=>emit('update:modelValue',value)})
const sections=ref(['request-response']),inputSections=ref(['user']),outputSections=ref(['reasoning','answer','commands']),expandedTools=ref([])
watch(()=>props.item,()=>{sections.value=['request-response'];inputSections.value=['user'];outputSections.value=['reasoning','answer','commands'];expandedTools.value=[]})

const asObject=value=>{if(typeof value==='string'){try{return JSON.parse(value)}catch{return value}}return value}
const readable=value=>{if(value==null||value==='')return'';if(typeof value==='string')return value;if(Array.isArray(value))return value.map(readable).filter(Boolean).join('\n');return Object.entries(value).map(([key,item])=>`${key}: ${typeof item==='object'?readable(item):item}`).join('\n')}
const contentText=value=>{if(value==null||value==='')return'（无文本内容）';if(typeof value==='string')return value;if(Array.isArray(value)){const text=value.map(part=>typeof part==='string'?part:(part.text||part.content||part.output_text||readable(part))).filter(Boolean).join('\n');return text||'（无文本内容）'}const text=readable(value);return text||'（无文本内容）'}
const formatted=value=>{if(typeof value==='string'){try{return JSON.stringify(JSON.parse(value),null,2)}catch{return value}}return JSON.stringify(value??{},null,2)}
const requestPayload=computed(()=>{const raw=asObject(props.item?.proxy_server_request)||{};return asObject(raw.body)||raw})
const requestMessages=computed(()=>{const source=requestPayload.value.messages??props.item?.messages??requestPayload.value.input;const messages=asObject(source);const result=Array.isArray(messages)?messages.map(message=>typeof message==='string'?{role:'user',content:message}:message):typeof messages==='string'?[{role:'user',content:messages}]:[];return requestPayload.value.instructions?[{role:'system',content:requestPayload.value.instructions},...result]:result})
const currentMessages=computed(()=>Array.isArray(props.item?.current_input_messages)?props.item.current_input_messages:requestMessages.value.slice(-1))
const classified=computed(()=>{const all=requestMessages.value;const historyCount=Number.isInteger(props.item?.history_message_count)?props.item.history_message_count:Math.max(0,all.length-currentMessages.value.length);const historical=all.slice(0,historyCount);const current=currentMessages.value;const system=all.filter(message=>['system','developer'].includes(String(message.role||'').toLowerCase()));const user=current.filter(message=>String(message.role||'').toLowerCase()==='user');const tool=current.filter(isToolMessage);const history=[...historical,...current.filter(message=>!['user','system','developer','tool','function'].includes(String(message.role||'').toLowerCase()))].filter(message=>!['system','developer'].includes(String(message.role||'').toLowerCase())&&!isToolMessage(message));return{system,user,history,tool}})
function isToolMessage(message){return['tool','function'].includes(String(message?.role||'').toLowerCase())||message?.type==='function_call_output'}
const responsePayload=computed(()=>asObject(props.item?.response)||{})
const responseMessages=computed(()=>{const response=responsePayload.value;if(Array.isArray(response.choices))return response.choices.map(choice=>choice.message||choice.delta).filter(Boolean);if(Array.isArray(response.output))return response.output.filter(output=>output.type!=='function_call').map(output=>({role:output.role||'assistant',content:output.content||output.output_text||output.text||output.summary||output,reasoning_content:output.reasoning_content,_type:output.type}));return response.content?[{role:'assistant',content:response.content,reasoning_content:response.reasoning_content||response.reasoning}]:[]})
const reasoningOutputs=computed(()=>{const rows=[];for(const message of responseMessages.value){if(['reasoning','thinking','analysis'].includes(String(message._type||'').toLowerCase())){rows.push({role:'assistant',content:message.content});continue}const reasoning=message.reasoning_content??message.reasoning??message.thinking??message.thought;if(reasoning)rows.push({role:'assistant',content:reasoning});if(Array.isArray(message.content)){const blocks=message.content.filter(block=>['reasoning','thinking','analysis'].includes(String(block?.type||'').toLowerCase()));for(const block of blocks)rows.push({role:'assistant',content:block.text||block.content||block})}}const response=responsePayload.value;const top=response.reasoning_content??response.reasoning??response.thinking;if(top)rows.push({role:'assistant',content:top});return rows})
const normalOutputs=computed(()=>responseMessages.value.filter(message=>!['reasoning','thinking','analysis'].includes(String(message._type||'').toLowerCase())).map(message=>{if(!Array.isArray(message.content))return{...message,content:message.content};const content=message.content.filter(block=>!['reasoning','thinking','analysis'].includes(String(block?.type||'').toLowerCase()));return{...message,content}}).filter(message=>contentText(message.content)!=='（无文本内容）'))
const normalizeCall=tool=>({id:tool.id||tool.call_id,name:tool.function?.name||tool.name||tool.type||'未知工具',arguments:tool.function?.arguments??tool.arguments??tool.input??{}})
const calledTools=computed(()=>{const response=responsePayload.value;const chat=(Array.isArray(response.choices)?response.choices:[]).flatMap(choice=>(choice.message?.tool_calls||choice.delta?.tool_calls||[]).map(normalizeCall));const responses=Array.isArray(response.output)?response.output.filter(output=>output.type==='function_call').map(normalizeCall):[];return[...chat,...responses]})
const toolDefinitions=computed(()=>(Array.isArray(requestPayload.value.tools)?requestPayload.value.tools:[]).map(tool=>{const fn=tool.function||tool;return{name:fn.name||tool.name||tool.type||'未知工具',description:fn.description||tool.description||'',parameters:fn.parameters||tool.input_schema||tool.inputSchema||{}}}))
const toolResults=computed(()=>requestMessages.value.filter(isToolMessage))
const toolRows=computed(()=>{const map=new Map();for(const tool of toolDefinitions.value)map.set(tool.name,{...tool,calls:[],results:[],called:false});for(const call of calledTools.value){const row=map.get(call.name)||{name:call.name,description:'',parameters:{},calls:[],results:[],called:false};row.calls.push(call);row.called=true;map.set(call.name,row)}for(const result of toolResults.value){const name=result.name||result.tool_name||result.function?.name||calledTools.value.find(call=>call.id&&call.id===result.tool_call_id)?.name||'工具返回';const row=map.get(name)||{name,description:'',parameters:{},calls:[],results:[],called:false};row.results.push(result);map.set(name,row)}return[...map.values()].sort((a,b)=>Number(b.called)-Number(a.called)||a.name.localeCompare(b.name))})
const statusLabel=computed(()=>({success:'成功',failed:'失败',failure:'失败',error:'错误'}[String(props.item?.status||'').toLowerCase()]||props.item?.status||'未知'))
const statusType=computed(()=>String(props.item?.status||'').toLowerCase()==='success'?'success':'danger')
const actorLabel=computed(()=>props.item?.interaction_scope==='subagent'||Number(props.item?.depth)>0?`Subagent · ${props.item?.subagent_name||`L${props.item?.depth||1}`}`:'主 Agent')
const number=value=>value==null?'—':Number(value).toLocaleString()
const duration=value=>value==null?'—':Number(value)>=1000?`${(Number(value)/1000).toFixed(1)} 秒`:`${Math.round(Number(value))} ms`
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN'):'未记录'
const roleLabel=role=>({system:'系统',developer:'系统',user:'用户',assistant:'模型',tool:'工具',function:'工具'}[String(role||'').toLowerCase()]||role||'消息')
const MessageList=defineComponent({props:{messages:{type:Array,default:()=>[]},tone:{type:String,default:''}},setup(componentProps){return()=>h('div',{class:'message-list'},componentProps.messages.map((message,index)=>h('article',{class:['message-row',componentProps.tone],key:index},[h('strong',roleLabel(message.role)),h('pre',contentText(message.content??message.output??message))])))}})
</script>

<style scoped>
.dialog-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-right:28px}.dialog-heading>div:first-child{display:flex;min-width:0;flex-direction:column;gap:4px}.dialog-heading span,.dialog-heading small{color:var(--muted);font-size:11px}.dialog-heading small{overflow-wrap:anywhere}.heading-tags{display:flex;gap:8px}.dialog-body{max-height:80vh;overflow:auto;padding-right:5px}.facts{display:grid;grid-template-columns:1.1fr 1.2fr 1fr .7fr 1.2fr;gap:1px;margin-bottom:16px;overflow:hidden;border:1px solid var(--line);border-radius:9px;background:var(--line)}.facts>div{display:flex;min-width:0;flex-direction:column;gap:5px;padding:10px 12px;background:var(--surface)}.facts span{color:var(--muted);font-size:10px}.facts b,.facts code{overflow-wrap:anywhere;font-size:12px}.section-title,.nested-title,.tool-title{display:flex;align-items:center;gap:10px;width:100%;padding-right:12px}.section-title span,.nested-title span,.tool-title span{margin-left:auto;color:var(--muted);font-size:11px}.primary-sections{border-top:1px solid var(--line)}.primary-sections :deep(>.el-collapse-item>.el-collapse-item__header){min-height:56px;padding:0 14px;font-size:15px}.primary-sections :deep(>.el-collapse-item>.el-collapse-item__wrap>.el-collapse-item__content){padding:0 14px 18px}.tool-rows,.nested-sections{border:1px solid var(--line);border-radius:8px;overflow:hidden}.tool-rows :deep(.el-collapse-item__header),.nested-sections :deep(.el-collapse-item__header){padding:0 13px;background:var(--surface-2)}.tool-rows :deep(.el-collapse-item__content),.nested-sections :deep(.el-collapse-item__content){padding:12px 14px}.tool-description{margin:0 0 10px;color:var(--muted);line-height:1.55}.tool-call-list,.tool-result-list{display:grid;gap:8px}.tool-result-list{margin-top:8px}.tool-call-list article,.tool-result-list article{padding:10px 12px;border-left:3px solid #c69232;border-radius:5px;background:#fff8e8}.tool-result-list article{border-left-color:#4b9b76;background:#edf8f2}.tool-call-list pre,.tool-result-list pre,details pre{margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.io-block{margin-top:12px;border:1px solid var(--line);border-radius:9px;overflow:hidden}.io-block>header{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:#eef5ff;border-bottom:1px solid var(--line)}.io-block>header span{color:var(--muted);font-size:11px}.output-block>header{background:#edf8f2}.message-list{display:grid;gap:8px}.message-row{padding:11px 13px;border-radius:7px;background:var(--surface-2)}.message-row.system{background:#f1f2f4}.message-row.user{background:#eef5ff}.message-row.history{border-left:3px solid #9ca8b5}.message-row.tool{background:#fff8e8}.message-row.reasoning{background:#f5f0ff;border-left:3px solid #8261b2}.message-row.assistant{background:#edf8f2}.message-row strong{display:block;margin-bottom:5px;color:var(--muted);font-size:10px}.message-row pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}.tool-rows details{margin-top:10px}.tool-rows summary{cursor:pointer;color:var(--brand);font-size:12px}@media(max-width:1000px){.facts{grid-template-columns:1fr 1fr}}@media(max-width:600px){.facts{grid-template-columns:1fr}.dialog-heading{align-items:flex-start;flex-direction:column}.heading-tags{display:none}}
</style>
