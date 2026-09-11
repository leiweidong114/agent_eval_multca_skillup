<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">EVALUATION SETTINGS</span><h1>设置</h1><p>选择默认模型，并为三类原理图任务分别配置 Skill 流水线和评测插件。</p></div></section>
    <el-card shadow="never" class="panel settings-card" v-loading="loading">
      <el-form label-position="top">
        <el-form-item label="LLM Judge 默认模型">
          <el-select v-model="form.judge_model" filterable placeholder="选择用于评测打分的模型">
            <el-option v-for="model in selectableModels" :key="`judge-${model.id}`" :label="modelLabel(model)" :value="model.id"/>
          </el-select>
          <div class="help">Skill 与原理图评测完成后，系统使用该模型读取结果、过程和 Skill 质量证据并打分。</div>
        </el-form-item>
        <el-form-item label="Agent 可用性测试默认模型">
          <el-select v-model="form.agent_test_model" filterable placeholder="选择用于 Agent HI 测试的模型">
            <el-option v-for="model in selectableModels" :key="`agent-${model.id}`" :label="modelLabel(model)" :value="model.id"/>
          </el-select>
          <div class="help">“模型与 Agent”页面点击测试时，将要求对应 Agent 通过此模型完成一次 HI 请求。</div>
        </el-form-item>
        <el-divider content-position="left">原理图任务配置</el-divider>
        <div class="task-profiles">
          <el-card v-for="task in taskTypes" :key="task.id" shadow="never" class="task-profile">
            <template #header><div class="task-title"><div><b>{{task.name}}</b><span>{{contractLabel(task.input_contract)}} → {{contractLabel(task.output_contract)}}</span></div><el-tag effect="plain">{{task.id}}</el-tag></div></template>
            <el-form-item label="Skill 流水线（按选择顺序执行）">
              <el-select v-model="profile(task.id).skills" multiple filterable collapse-tags :max-collapse-tags="4" placeholder="选择 1 至 30 个已扫描 Skill">
                <el-option v-for="skill in skills" :key="skill.identifier||skill.name" :label="`${skill.name} · ${sourceLabel(skill.source)}`" :value="skill.identifier||skill.name"/>
              </el-select>
              <div class="help">当前顺序：{{profile(task.id).skills.join(' → ')||'未选择'}}</div>
            </el-form-item>
            <el-form-item label="评测插件">
              <el-select v-model="profile(task.id).evaluator_id" filterable placeholder="选择兼容的评测插件">
                <el-option v-for="item in compatibleEvaluators(task.id)" :key="item.id" :label="`${item.id} · v${item.version} · ${sourceLabel(item.source)}`" :value="item.id"/>
              </el-select>
            </el-form-item>
          </el-card>
        </div>
        <div class="scan-summary"><span>已扫描 {{skills.length}} 个 Skill、{{evaluators.length}} 个评测插件</span><el-button :loading="loading" @click="load">重新扫描</el-button></div>
        <el-alert type="info" :closable="false" show-icon title="这里只保存模型 ID，不修改 LiteLLM 地址、密钥、Agent 注入方式或鉴权配置。"/>
        <div class="actions"><el-button type="primary" :loading="saving" @click="save">保存设置</el-button><el-button @click="load">恢复当前设置</el-button></div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchEvaluators, fetchModels, fetchSchematicTaskTypes, fetchSettings, fetchSkills, saveSettings } from '../api'

const defaultTaskTypes=[{id:'block_to_schematic',name:'框图生成原理图',input_contract:'block_diagram',output_contract:'schematic_project'},{id:'block_to_signal_list',name:'框图生成信号接口列表',input_contract:'block_diagram',output_contract:'signal_interface_v1'},{id:'signal_list_to_schematic',name:'信号接口列表生成原理图',input_contract:'signal_interface_v1',output_contract:'schematic_project'}]
const defaultProfiles={block_to_schematic:{skills:['schematic-pipeline','signal-interface-generation','schematic-layout-codegen','schematic-web-apply'],evaluator_id:'schematic-default'},block_to_signal_list:{skills:['signal-interface-generation'],evaluator_id:'schematic-default'},signal_list_to_schematic:{skills:['schematic-layout-codegen','schematic-web-apply'],evaluator_id:'schematic-default'}}
const loading=ref(false),saving=ref(false),models=ref([]),skills=ref([]),evaluators=ref([]),taskTypes=ref(defaultTaskTypes)
const form=reactive({judge_model:'',agent_test_model:'',schematic_skills:[],schematic_task_profiles:structuredClone(defaultProfiles)})
const selectableModels=computed(()=>[...models.value].sort((a,b)=>Number(a.connectivity?.available!==true)-Number(b.connectivity?.available!==true)||a.id.localeCompare(b.id)))
const modelLabel=model=>`${model.id}${model.connectivity?.available===true?' · 已测试可用':model.connectivity?.available===false?' · 测试失败':' · 未测试'}`
const sourceLabel=source=>source==='built_in'?'内置':source==='bundled'?'项目插件':source==='uploaded'?'已导入':source==='external'||String(source||'').includes(':')?'本机扩展':'外部'
const contractLabel=value=>({block_diagram:'框图',signal_interface_v1:'信号接口列表',schematic_project:'原理图工程'}[value]||value)
const profile=id=>{if(!form.schematic_task_profiles[id])form.schematic_task_profiles[id]={skills:[],evaluator_id:''};return form.schematic_task_profiles[id]}
const compatibleEvaluators=id=>evaluators.value.filter(item=>item.evaluation_types?.includes('schematic')&&(!item.schematic_task_types?.length||item.schematic_task_types.includes(id)))
async function load(){loading.value=true;try{const[settings,catalog,skillCatalog,evaluatorCatalog,taskCatalog]=await Promise.all([fetchSettings(),fetchModels(),fetchSkills(),fetchEvaluators(),fetchSchematicTaskTypes()]);Object.assign(form,settings);models.value=catalog.models||[];skills.value=skillCatalog.skills||[];evaluators.value=evaluatorCatalog||[];taskTypes.value=taskCatalog?.length?taskCatalog:defaultTaskTypes;for(const task of taskTypes.value)profile(task.id)}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{loading.value=false}}
async function save(){if(!form.judge_model||!form.agent_test_model)return ElMessage.warning('请选择两个默认模型');for(const task of taskTypes.value){const item=profile(task.id);if(!item.skills.length)return ElMessage.warning(`请为“${task.name}”选择至少一个 Skill`);if(!item.evaluator_id)return ElMessage.warning(`请为“${task.name}”选择评测插件`)}form.schematic_skills=profile('block_to_schematic').skills;saving.value=true;try{Object.assign(form,await saveSettings({...form}));ElMessage.success('三类原理图任务设置已保存到根目录 .env')}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{saving.value=false}}
onMounted(load)
</script>

<style scoped>
.settings-card{max-width:980px}.settings-card :deep(.el-select){width:100%}.settings-card :deep(.el-form-item){margin-bottom:24px}.help{margin-top:7px;color:var(--muted);font-size:13px;line-height:1.55}.actions{display:flex;gap:10px;margin-top:24px}.task-profiles{display:grid;gap:14px}.task-profile{background:var(--surface-2)}.task-title{display:flex;align-items:center;justify-content:space-between;gap:12px}.task-title>div{display:flex;flex-direction:column;gap:5px}.task-title span,.scan-summary{color:var(--muted);font-size:12px}.scan-summary{display:flex;align-items:center;justify-content:space-between;margin-top:14px}
</style>
