<template>
  <div class="eval-run">
    <el-card shadow="never">
      <template #header>
        <span class="card-title">运行一次 Agent Skill 评测</span>
      </template>

      <el-form :model="form" label-width="140px" label-position="left">
        <el-form-item label="Skill">
          <el-select
            v-model="form.skill"
            placeholder="选择 Skill"
            filterable
            style="width: 320px"
            @change="loadCases"
          >
            <el-option
              v-for="s in skills"
              :key="s.name"
              :label="s.name"
              :value="s.name"
            />
          </el-select>
          <el-button style="margin-left: 12px" @click="refreshSkills">刷新</el-button>
        </el-form-item>

        <el-form-item label="Agent">
          <el-select
            v-model="form.agent"
            placeholder="选择 Agent"
            filterable
            style="width: 320px"
          >
            <el-option
              v-for="a in agents"
              :key="a.agent"
              :label="a.agent"
              :value="a.agent"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="模型配置">
          <el-select v-model="form.profile" style="width: 320px" @change="applyProfileModel">
            <el-option v-for="name in modelConfig.profiles" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>

        <el-form-item label="模型覆盖">
          <el-input v-model="form.model" placeholder="留空则使用配置文件默认模型" style="width: 320px" />
        </el-form-item>

        <el-form-item label="Agent 可执行文件">
          <el-input v-model="form.agent_executable" placeholder="留空则从 PATH 查找" style="width: 320px" />
        </el-form-item>

        <el-form-item label="用例文件">
          <el-select
            v-model="form.case"
            multiple
            placeholder="选择已有用例 YAML"
            style="width: 320px"
          >
            <el-option
              v-for="c in cases"
              :key="c.name"
              :label="c.name"
              :value="c.path"
            />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">或直接使用 Prompt 生成临时用例</el-divider>

        <el-form-item label="Prompt">
          <el-input
            v-model="form.prompt"
            type="textarea"
            :rows="3"
            placeholder="评测任务描述，将生成单条用例"
          />
        </el-form-item>

        <el-form-item label="必须包含">
          <el-select
            v-model="form.must_contain"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输出必须包含的字符串"
            style="width: 320px"
          />
        </el-form-item>

        <el-form-item label="禁止包含">
          <el-select
            v-model="form.must_not_contain"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输出禁止包含的字符串"
            style="width: 320px"
          />
        </el-form-item>

        <el-divider content-position="left">运行参数</el-divider>

        <el-form-item label="并行度">
          <el-input-number v-model="form.parallelism" :min="1" :max="16" />
        </el-form-item>
        <el-form-item label="迭代次数">
          <el-input-number v-model="form.iterations" :min="1" :max="20" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="form.timeout_seconds" :min="1" :step="60" />
        </el-form-item>
        <el-form-item label="最大轮数">
          <el-input-number v-model="form.max_turns" :min="1" />
        </el-form-item>
        <el-form-item label="基准对照">
          <el-switch v-model="form.benchmark" active-text="同时运行无 Skill 基线" />
        </el-form-item>
        <el-form-item label="数据库轨迹">
          <el-switch v-model="form.collect_database_trace" active-text="读取 LiteLLM 模型交互记录" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="running" @click="run">
            <el-icon style="margin-right: 4px"><VideoPlay /></el-icon>
            运行评测
          </el-button>
          <el-button :loading="validating" @click="validate">仅校验</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="result" shadow="never" style="margin-top: 16px">
      <template #header>
        <span class="card-title">运行结果</span>
      </template>
      <pre class="result-pre">{{ JSON.stringify(result, null, 2) }}</pre>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  fetchAgents,
  fetchModelConfig,
  fetchSkills,
  fetchSkillCases,
  triggerRun,
  triggerValidate,
} from '../api'

const form = reactive({
  skill: '',
  agent: '',
  profile: '',
  model: '',
  agent_executable: '',
  case: [],
  prompt: '',
  must_contain: [],
  must_not_contain: [],
  parallelism: 1,
  iterations: 1,
  timeout_seconds: 1800,
  max_turns: 12,
  benchmark: true,
  collect_database_trace: true,
})

const agents = ref([])
const modelConfig = reactive({ profiles: [], profile_models: {}, default_profile: '', default_model: '' })
const skills = ref([])
const cases = ref([])
const result = ref(null)
const running = ref(false)
const validating = ref(false)

async function refreshSkills() {
  try {
    const data = await fetchSkills()
    skills.value = data.skills || []
  } catch (e) {
    ElMessage.error(`获取 Skill 失败: ${e.message}`)
  }
}

async function loadCases() {
  cases.value = []
  form.case = []
  if (!form.skill) return
  try {
    const data = await fetchSkillCases(form.skill)
    cases.value = data.cases || []
  } catch (e) {
    ElMessage.warning(`加载用例失败: ${e.message}`)
  }
}

async function loadAgents() {
  try {
    agents.value = await fetchAgents()
  } catch (e) {
    ElMessage.error(`获取 Agent 失败: ${e.message}`)
  }
}

async function loadModelConfig() {
  try {
    const data = await fetchModelConfig()
    Object.assign(modelConfig, data)
    form.profile = data.default_profile || ''
    form.model = data.default_model || ''
  } catch (e) {
    ElMessage.error(`获取模型配置失败: ${e.message}`)
  }
}

function applyProfileModel(name) {
  form.model = modelConfig.profile_models?.[name] || ''
}

function buildPayload() {
  return {
    skill: form.skill,
    agent: form.agent,
    profile: form.profile || null,
    model: form.model || null,
    case: form.case,
    prompt: form.prompt || null,
    agent_executable: form.agent_executable || null,
    must_contain: form.must_contain,
    must_not_contain: form.must_not_contain,
    parallelism: form.parallelism,
    iterations: form.iterations,
    timeout_seconds: form.timeout_seconds,
    max_turns: form.max_turns,
    benchmark: form.benchmark,
    collect_database_trace: form.collect_database_trace,
    extra_args: [],
  }
}

function validatePayload() {
  if (!form.skill) {
    ElMessage.warning('请选择 Skill')
    return false
  }
  if (!form.agent) {
    ElMessage.warning('请选择 Agent')
    return false
  }
  if (!form.profile) {
    ElMessage.warning('请选择模型配置')
    return false
  }
  if (!form.case.length && !form.prompt) {
    ElMessage.warning('至少选择一个用例文件或填写 Prompt')
    return false
  }
  return true
}

async function run() {
  if (!validatePayload()) return
  running.value = true
  result.value = null
  try {
    result.value = await triggerRun(buildPayload())
    ElMessage.success('评测完成')
  } catch (e) {
    ElMessage.error(`运行失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    running.value = false
  }
}

async function validate() {
  if (!validatePayload()) return
  validating.value = true
  try {
    result.value = await triggerValidate(buildPayload())
    ElMessage.success('校验通过')
  } catch (e) {
    ElMessage.error(`校验失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    validating.value = false
  }
}

function reset() {
  form.skill = ''
  form.agent = ''
  form.profile = modelConfig.default_profile || ''
  form.model = modelConfig.default_model || ''
  form.agent_executable = ''
  form.case = []
  form.prompt = ''
  form.must_contain = []
  form.must_not_contain = []
  form.parallelism = 1
  form.iterations = 1
  form.timeout_seconds = 1800
  form.max_turns = 12
  form.benchmark = true
  form.collect_database_trace = true
  result.value = null
}

onMounted(() => {
  loadAgents()
  loadModelConfig()
  refreshSkills()
})
</script>

<style scoped>
.card-title {
  font-weight: 600;
}
.result-pre {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  overflow: auto;
  max-height: 480px;
}
</style>
