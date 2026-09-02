<template>
  <div class="page-stack">
    <section class="hero compact">
      <div><span class="eyebrow">RUNTIME CATALOG</span><h1>模型与 Agent</h1><p>统一管理模型 Provider，并将模型别名按任务注入 21 个可评测 Agent。</p></div>
      <div class="hero-actions"><el-button @click="load" :loading="loading"><el-icon><Refresh/></el-icon> 重新发现</el-button><el-button type="primary" @click="createProfile">新建 Provider</el-button></div>
    </section>

    <el-alert v-if="modelData.errors?.length" type="warning" show-icon :closable="false">
      <template #title>部分模型网关不可用，已展示配置文件中的回退模型</template>
      <div v-for="item in modelData.errors" :key="JSON.stringify(item)">{{item.base_url||item.gateway}}：{{item.error}}</div>
    </el-alert>

    <div class="runtime-summary">
      <div><span class="dot" :class="{on:supportedAgents.length===21}"/><p><b>{{supportedAgents.length}} / 21</b><small>模型适配 Agent</small></p></div>
      <div><span class="dot" :class="{on:detectedAgents.length}"/><p><b>{{detectedAgents.length}} / {{agents.length}}</b><small>本机发现 Agent</small></p></div>
      <div><span class="dot" :class="{on:profiles.length}"/><p><b>{{profiles.length}}</b><small>Provider Profile</small></p></div>
      <div><span class="dot" :class="{on:verifiedModelCount}"/><p><b>{{verifiedModelCount}} / {{models.length}}</b><small>网关模型</small></p></div>
      <div><span class="dot" :class="{on:databaseConnected}"/><p><b>{{databaseConnected?'正常':'未连接'}}</b><small>评测轨迹数据库</small></p></div>
    </div>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>Provider 配置</b><span>自定义项写入本机忽略配置；API Key 不在页面回显</span></div><el-tag effect="plain">CC Switch 风格</el-tag></div></template>
      <el-table :data="profiles" v-loading="loading">
        <el-table-column label="Profile" min-width="170"><template #default="{row}"><div class="name-cell"><b>{{row.name}}</b><div><el-tag v-if="row.is_default" size="small" type="success">默认</el-tag><el-tag size="small" effect="plain">{{row.source==='local'?'本地':'内置'}}</el-tag></div></div></template></el-table-column>
        <el-table-column prop="model" label="默认模型" min-width="210" show-overflow-tooltip/>
        <el-table-column label="协议" width="170"><template #default="{row}"><el-tag effect="plain">{{row.protocol}}</el-tag></template></el-table-column>
        <el-table-column label="密钥" width="110"><template #default="{row}"><el-tag :type="row.type==='native'||row.api_key_configured?'success':'warning'">{{row.type==='native'?'原生登录':row.api_key_configured?'已配置':'未配置'}}</el-tag></template></el-table-column>
        <el-table-column label="Agent 覆盖" width="130"><template #default="{row}"><b>{{row.compatible_agents?.length||0}} / 21</b></template></el-table-column>
        <el-table-column label="操作" width="170" fixed="right"><template #default="{row}"><el-button link type="primary" :disabled="row.type==='native'" @click="editProfile(row)">编辑</el-button><el-button v-if="row.source==='local'" link type="danger" @click="removeProfile(row)">删除</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>本地 Agent</b><span>只有“指定模型 + Skill”均支持的 21 个 Agent 纳入本次适配</span></div></div></template>
      <el-table :data="sortedAgents" v-loading="loading" max-height="560">
        <el-table-column label="Agent" width="150"><template #default="{row}"><b>{{row.agent}}</b></template></el-table-column>
        <el-table-column label="开发范围" width="130"><template #default="{row}"><el-tag :type="row.capabilities?.specified_model_and_skill_evaluation?'success':'info'">{{row.capabilities?.specified_model_and_skill_evaluation?'已适配':'本次排除'}}</el-tag></template></el-table-column>
        <el-table-column label="模型注入" min-width="190"><template #default="{row}">{{row.capabilities?.model_adapter?.provider_injection||'—'}}</template></el-table-column>
        <el-table-column label="选择方式" min-width="180"><template #default="{row}">{{row.capabilities?.model_adapter?.model_selection||'—'}}</template></el-table-column>
        <el-table-column prop="detected_executable" label="本地路径" min-width="260"><template #default="{row}"><code>{{row.detected_executable||row.default_command}}</code></template></el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel">
      <template #header><div class="panel-title"><div><b>可用模型</b><span>{{modelData.litellm_available?'已从网关实时同步':'当前为本地配置回退'}}</span></div></div></template>
      <div class="toolbar"><el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索模型或 Profile"/><el-tag :type="modelData.litellm_available?'success':'warning'">{{modelData.litellm_available?'网关在线':'使用回退配置'}}</el-tag></div>
      <el-table :data="filteredModels" max-height="560">
        <el-table-column prop="id" label="模型" min-width="250"><template #default="{row}"><b>{{row.id}}</b></template></el-table-column>
        <el-table-column label="可用状态" width="130"><template #default="{row}"><el-tag :type="row.source==='litellm'?'success':'warning'">{{row.source==='litellm'?'已发现':'本地配置'}}</el-tag></template></el-table-column>
        <el-table-column label="Profile" min-width="180"><template #default="{row}">{{row.profiles?.join('、')||row.profile||'—'}}</template></el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing?'编辑 Provider':'新建 Provider'" width="760px" destroy-on-close>
      <el-alert type="info" :closable="false" title="要覆盖全部 21 个 Agent，请使用能同时提供 OpenAI/Anthropic 兼容入口的网关，并选择 openai_compatible。"/>
      <el-form label-position="top" class="provider-form">
        <div class="form-grid"><el-form-item label="Profile 名称"><el-input v-model="form.name" :disabled="editing" placeholder="例如 company_gateway"/></el-form-item><el-form-item label="协议"><el-select v-model="form.protocol"><el-option v-for="item in protocols" :key="item" :label="item" :value="item"/></el-select></el-form-item></div>
        <el-form-item label="API Base URL"><el-input v-model="form.api_base" placeholder="https://gateway.example.com/v1"/></el-form-item>
        <div class="form-grid"><el-form-item label="默认模型"><el-input v-model="form.model" placeholder="provider/model-id"/></el-form-item><el-form-item label="API Key 环境变量"><el-input v-model="form.api_key_env"/></el-form-item></div>
        <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password placeholder="留空则保留现有值，也可由进程环境提供"/></el-form-item>
        <div class="form-grid"><el-form-item label="上下文窗口"><el-input-number v-model="form.context_window" :min="1" :max="10000000" controls-position="right"/></el-form-item><el-form-item label="最大输出 Token"><el-input-number v-model="form.max_output_tokens" :min="1" :max="1000000" controls-position="right"/></el-form-item></div>
        <el-form-item label="Agent 模型别名（JSON）"><el-input v-model="form.agent_models_text" type="textarea" :rows="3" placeholder='{"claude":"sonnet","codebuddy":"custom-local:model"}'/></el-form-item>
        <el-form-item label="网关模型映射（JSON）"><el-input v-model="form.gateway_models_text" type="textarea" :rows="3" placeholder='{"claude":"model-anthropic","opencode":"model-no-thinking"}'/></el-form-item>
        <el-checkbox v-model="form.make_default">设为默认 Profile</el-checkbox>
      </el-form>
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="submitProfile">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { deleteModelProfile, fetchAgents, fetchDatabaseHealth, fetchModelConfig, fetchModelProfiles, fetchModels, saveModelProfile } from '../api'

const agents=ref([]),profiles=ref([]),modelData=ref({models:[],gateways:[],errors:[]}),database=ref({}),modelConfig=ref({}),keyword=ref(''),loading=ref(false),saving=ref(false),dialogVisible=ref(false),editing=ref(false)
const form=reactive({name:'',model:'',api_base:'',api_key_env:'LITELLM_API_KEY',api_key:'',protocol:'openai_compatible',context_window:200000,max_output_tokens:32000,agent_models_text:'{}',gateway_models_text:'{}',make_default:false})
const protocols=computed(()=>modelConfig.value.supported_profile_protocols||['openai_compatible','openai_chat','openai_responses','anthropic_messages'])
const models=computed(()=>modelData.value.models||[])
const supportedAgents=computed(()=>agents.value.filter(x=>x.capabilities?.specified_model_and_skill_evaluation))
const detectedAgents=computed(()=>agents.value.filter(x=>x.detected_executable))
const verifiedModelCount=computed(()=>models.value.filter(x=>x.source==='litellm').length)
const databaseConnected=computed(()=>database.value.status==='ok'||database.value.ok===true)
const sortedAgents=computed(()=>[...agents.value].sort((a,b)=>Number(!a.capabilities?.specified_model_and_skill_evaluation)-Number(!b.capabilities?.specified_model_and_skill_evaluation)||Number(!a.detected_executable)-Number(!b.detected_executable)||a.agent.localeCompare(b.agent)))
const filteredModels=computed(()=>[...models.value].filter(x=>`${x.id} ${x.profile} ${x.owned_by}`.toLowerCase().includes(keyword.value.toLowerCase())).sort((a,b)=>Number(a.source!=='litellm')-Number(b.source!=='litellm')||a.id.localeCompare(b.id)))

function resetForm(){Object.assign(form,{name:'',model:'',api_base:'',api_key_env:'LITELLM_API_KEY',api_key:'',protocol:'openai_compatible',context_window:200000,max_output_tokens:32000,agent_models_text:'{}',gateway_models_text:'{}',make_default:false})}
function createProfile(){editing.value=false;resetForm();dialogVisible.value=true}
function editProfile(row){editing.value=true;Object.assign(form,{name:row.name,model:row.model,api_base:row.api_base,api_key_env:row.api_key_env||'LITELLM_API_KEY',api_key:'',protocol:row.protocol||'openai_compatible',context_window:row.context_window||200000,max_output_tokens:row.max_output_tokens||32000,agent_models_text:JSON.stringify(row.agent_models||{},null,2),gateway_models_text:JSON.stringify(row.gateway_models||{},null,2),make_default:row.is_default});dialogVisible.value=true}
function parseMapping(value,label){try{const parsed=JSON.parse(value||'{}');if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw new Error();return parsed}catch{throw new Error(`${label} 必须是 JSON 对象`)}}
async function submitProfile(){try{saving.value=true;if(!form.name.trim())throw new Error('请填写 Profile 名称');const payload={model:form.model,api_base:form.api_base,api_key_env:form.api_key_env,protocol:form.protocol,context_window:form.context_window,max_output_tokens:form.max_output_tokens,agent_models:parseMapping(form.agent_models_text,'Agent 模型别名'),gateway_models:parseMapping(form.gateway_models_text,'网关模型映射'),make_default:form.make_default};if(form.api_key)payload.api_key=form.api_key;await saveModelProfile(form.name,payload);ElMessage.success('Provider 已保存');dialogVisible.value=false;await load()}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{saving.value=false}}
async function removeProfile(row){try{await ElMessageBox.confirm(`删除本地 Profile「${row.name}」？内置 Profile 的本地覆盖会恢复默认值。`,'删除 Provider',{type:'warning'});await deleteModelProfile(row.name);ElMessage.success('Provider 已删除');await load()}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(e.response?.data?.detail||e.message)}}
async function load(){loading.value=true;const r=await Promise.allSettled([fetchAgents(),fetchModels(),fetchDatabaseHealth(),fetchModelConfig(),fetchModelProfiles()]);agents.value=r[0].status==='fulfilled'?r[0].value:[];modelData.value=r[1].status==='fulfilled'?r[1].value:{models:[],gateways:[],errors:[{error:r[1].reason?.message}]};database.value=r[2].status==='fulfilled'?r[2].value:{};modelConfig.value=r[3].status==='fulfilled'?r[3].value:{};profiles.value=r[4].status==='fulfilled'?r[4].value:[];if(r.every(x=>x.status==='rejected'))ElMessage.error('无法读取运行环境');loading.value=false}
onMounted(load)
</script>

<style scoped>
.runtime-summary{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}.runtime-summary>div{display:flex;align-items:center;gap:13px;padding:18px;background:var(--surface);border:1px solid var(--line);border-radius:13px}.runtime-summary p,.name-cell{display:flex;flex-direction:column;margin:0;gap:5px}.runtime-summary b{font-size:20px}.runtime-summary small,.panel-title span{color:var(--muted)}.dot{width:12px;height:12px;border-radius:50%;background:#a6adba;box-shadow:0 0 0 5px rgba(166,173,186,.12)}.dot.on{background:#25a66a;box-shadow:0 0 0 5px rgba(37,166,106,.12)}.panel-title,.toolbar,.hero-actions{display:flex;align-items:center;justify-content:space-between;gap:10px}.panel-title>div{display:flex;flex-direction:column;gap:3px}.toolbar{margin-bottom:16px}.toolbar .el-input{max-width:360px}.name-cell>div{display:flex;gap:5px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.provider-form{margin-top:18px}.provider-form .el-select,.provider-form .el-input-number{width:100%}code{font-size:12px;color:var(--muted)}@media(max-width:900px){.runtime-summary{grid-template-columns:repeat(2,1fr)}.form-grid{grid-template-columns:1fr}}
</style>
