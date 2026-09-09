<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">EVALUATION SETTINGS</span><h1>设置</h1><p>选择默认模型，以及原理图评测需要注入并按顺序执行的 Skill。</p></div></section>
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
        <el-form-item label="原理图生成评测 Skill（按选择顺序执行）">
          <el-select v-model="form.schematic_skills" multiple filterable collapse-tags :max-collapse-tags="4" placeholder="选择 1 至 8 个已导入 Skill">
            <el-option v-for="skill in skills" :key="skill.identifier||skill.name" :label="skill.name" :value="skill.identifier||skill.name"/>
          </el-select>
          <div class="help">在 Skill 管理页导入或修改 Skill 后，到这里选择。启动原理图评测时，这些 Skill 会被组合并复制到任务工作区，Agent 按此顺序读取执行。当前顺序：{{form.schematic_skills.join(' → ')||'未选择'}}</div>
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon title="这里只保存模型 ID，不修改 LiteLLM 地址、密钥、Agent 注入方式或鉴权配置。"/>
        <div class="actions"><el-button type="primary" :loading="saving" @click="save">保存设置</el-button><el-button @click="load">恢复当前设置</el-button></div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchModels, fetchSettings, fetchSkills, saveSettings } from '../api'

const loading=ref(false),saving=ref(false),models=ref([]),skills=ref([])
const form=reactive({judge_model:'',agent_test_model:'',schematic_skills:[]})
const selectableModels=computed(()=>[...models.value].sort((a,b)=>Number(a.connectivity?.available!==true)-Number(b.connectivity?.available!==true)||a.id.localeCompare(b.id)))
const modelLabel=model=>`${model.id}${model.connectivity?.available===true?' · 已测试可用':model.connectivity?.available===false?' · 测试失败':' · 未测试'}`
async function load(){loading.value=true;try{const[settings,catalog,skillCatalog]=await Promise.all([fetchSettings(),fetchModels(),fetchSkills()]);Object.assign(form,settings);models.value=catalog.models||[];skills.value=skillCatalog.skills||[]}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{loading.value=false}}
async function save(){if(!form.judge_model||!form.agent_test_model)return ElMessage.warning('请选择两个默认模型');if(!form.schematic_skills.length)return ElMessage.warning('请至少选择一个原理图评测 Skill');saving.value=true;try{Object.assign(form,await saveSettings({...form}));ElMessage.success('评测设置已保存')}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{saving.value=false}}
onMounted(load)
</script>

<style scoped>
.settings-card{max-width:820px}.settings-card :deep(.el-select){width:100%}.settings-card :deep(.el-form-item){margin-bottom:28px}.help{margin-top:7px;color:var(--muted);font-size:13px;line-height:1.55}.actions{display:flex;gap:10px;margin-top:24px}
</style>
