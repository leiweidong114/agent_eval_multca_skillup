<template>
  <div class="page-stack">
    <section class="hero compact">
      <div>
        <span class="eyebrow">CREATE EVALUATION</span>
        <h1>新建评测</h1>
        <p>在同一流程中选择评测类型、评测对象、Agent 与实际运行模型。</p>
      </div>
      <el-steps :active="step" simple class="steps">
        <el-step title="选择类型" />
        <el-step title="配置任务" />
        <el-step title="执行评测" />
      </el-steps>
    </section>

    <div class="type-grid">
      <button v-for="item in types" :key="item.id" class="type-card" :class="{ active: form.type === item.id }" @click="selectType(item.id)">
        <span class="type-icon"><el-icon><component :is="item.icon" /></el-icon></span>
        <strong>{{ item.name }}</strong>
        <small>{{ item.description }}</small>
        <el-icon class="check"><CircleCheckFilled /></el-icon>
      </button>
    </div>

    <el-card v-if="step >= 1" shadow="never" class="panel form-panel">
      <template #header>
        <div class="panel-title"><span>评测配置</span><el-tag effect="plain">{{ currentType.name }}</el-tag></div>
      </template>
      <el-form label-position="top" :model="form">
        <div class="form-grid">
          <el-form-item label="任务名称">
            <el-input v-model="form.name" placeholder="用于在结果中心快速识别" />
          </el-form-item>
          <el-form-item label="执行 Agent">
            <el-select v-model="form.agent" filterable placeholder="选择本机 Agent">
              <el-option v-for="agent in availableAgents" :key="agent.agent" :value="agent.agent" :label="agentLabel(agent)" :disabled="agentDisabled(agent)" />
            </el-select>
          </el-form-item>
          <el-form-item label="运行模型">
            <el-select v-model="form.modelKey" filterable placeholder="选择模型" @change="onModelChange">
              <el-option v-for="model in availableModels" :key="modelKey(model)" :value="modelKey(model)" :label="modelLabel(model)">
                <span>{{ model.id }}</span><span class="option-meta">{{ model.source }} · {{ model.profile }}</span>
              </el-option>
            </el-select>
          </el-form-item>
        </div>

        <template v-if="form.type === 'skill'">
          <el-divider content-position="left">Skill 组合</el-divider>
          <el-form-item label="参与评测的 Skill（最多 8 个）">
            <el-select v-model="form.skills" multiple filterable collapse-tags :max-collapse-tags="3" placeholder="选择一个或多个 Skill" @change="onSkillsChange">
              <el-option v-for="skill in skills" :key="skill.name" :value="skill.name" :label="skill.name" />
            </el-select>
            <div class="field-help">选择多个 Skill 时，运行器会生成只读组合包，让 Agent 在一次任务中联合使用。</div>
          </el-form-item>
          <el-form-item label="评测 Prompt">
            <el-input v-model="form.prompt" type="textarea" :rows="5" placeholder="描述要完成的任务，以及多个 Skill 应如何协同。单 Skill 也可以直接使用 Prompt。" />
          </el-form-item>
          <el-form-item v-if="form.skills.length === 1 && cases.length" label="或选择 Skill 自带用例">
            <el-select v-model="form.cases" multiple placeholder="可选择一个或多个 YAML 用例">
              <el-option v-for="item in cases" :key="item.path" :label="item.name" :value="item.path" />
            </el-select>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="输出必须包含"><el-select v-model="form.mustContain" multiple filterable allow-create default-first-option /></el-form-item>
            <el-form-item label="输出禁止包含"><el-select v-model="form.mustNotContain" multiple filterable allow-create default-first-option /></el-form-item>
          </div>
        </template>

        <template v-else-if="form.type === 'schematic'">
          <el-divider content-position="left">原理图任务</el-divider>
          <el-form-item label="原理图需求 Prompt">
            <el-input v-model="form.prompt" type="textarea" :rows="6" placeholder="例如：设计一套 24V 转 5V/3A 的降压电源，包含输入保护、状态指示和测试点……" />
          </el-form-item>
          <el-alert type="info" :closable="false" show-icon title="系统会使用 schematic-generation Skill，并执行结构、规则及产物完整性评测。" />
        </template>

        <template v-else>
          <el-divider content-position="left">题库与采样</el-divider>
          <div class="form-grid">
            <el-form-item label="题库">
              <el-select v-model="form.benchmarkId" filterable placeholder="选择已安装题库" @change="onBenchmarkChange">
                <el-option v-for="bank in installedBenchmarks" :key="bank.id" :value="bank.id" :label="`${bank.name}（${bank.item_count} 题）`" />
              </el-select>
            </el-form-item>
            <el-form-item label="抽样题数"><el-input-number v-model="form.sampleLimit" :min="1" :max="selectedBenchmark?.item_count || 10000" /></el-form-item>
            <el-form-item label="重复次数"><el-input-number v-model="form.repeats" :min="1" :max="10" /></el-form-item>
          </div>
          <el-alert v-if="selectedBenchmark" :closable="false" show-icon :type="selectedBenchmark.task_type === 'repository_agent' ? 'warning' : 'info'">
            <template #title>{{ selectedBenchmark.description || selectedBenchmark.task_type }}</template>
          </el-alert>
        </template>

        <el-divider content-position="left">运行参数</el-divider>
        <div class="form-grid runtime-grid">
          <el-form-item label="并行度"><el-input-number v-model="form.concurrency" :min="1" :max="16" /></el-form-item>
          <el-form-item v-if="form.type !== 'question'" label="迭代次数"><el-input-number v-model="form.iterations" :min="1" :max="20" /></el-form-item>
          <el-form-item label="单任务超时（秒）"><el-input-number v-model="form.timeout" :min="30" :max="7200" :step="30" /></el-form-item>
          <el-form-item v-if="form.type !== 'question'" label="运行无 Skill 基线"><el-switch v-model="form.baseline" /></el-form-item>
        </div>

        <div class="submit-row">
          <div class="selection-summary">
            <b>{{ currentType.name }}</b><span>{{ form.agent || '未选 Agent' }}</span><span>{{ selectedModel?.id || '未选模型' }}</span>
          </div>
          <el-button type="primary" size="large" :loading="running" @click="submit">{{ running ? '评测运行中' : '开始评测' }}</el-button>
        </div>
      </el-form>
    </el-card>

    <el-card v-if="job" shadow="never" class="panel progress-panel">
      <div class="progress-head"><div><span class="eyebrow">RUN STATUS</span><h3>{{ job.phase || statusText(job.status) }}</h3><p>{{ job.message || '任务已提交，正在等待执行。' }}</p></div><el-tag :type="statusType(job.status)" size="large">{{ statusText(job.status) }}</el-tag></div>
      <el-progress :percentage="Number(job.progress || 0)" :status="job.status === 'failed' ? 'exception' : job.status === 'completed' ? 'success' : ''" />
      <div v-if="isTerminal" class="result-actions">
        <el-button type="primary" @click="openResult">查看评测结果</el-button>
        <el-button @click="resetRun">再建一个评测</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, markRaw, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Cpu, DataAnalysis, MagicStick } from '@element-plus/icons-vue'
import { fetchAgents, fetchBenchmarks, fetchJob, fetchModels, fetchSkillCases, fetchSkills, triggerRun, ensureAutoProvider, createExperiment, fetchExperiment } from '../api'

const route = useRoute()
const router = useRouter()
const types = [
  { id: 'schematic', name: '原理图评测', description: '验证原理图生成质量与工程规则', icon: markRaw(Cpu) },
  { id: 'question', name: '题库评测', description: '使用标准题库测量模型与 Agent 能力', icon: markRaw(DataAnalysis) },
  { id: 'skill', name: 'Skill 评测', description: '单 Skill 或多 Skill 联合任务评测', icon: markRaw(MagicStick) },
]
const normalizeType = (value) => ['schematic', 'question', 'skill'].includes(value) ? value : ''
const form = reactive({ type: normalizeType(route.query.type), name: '', agent: '', modelKey: '', skills: [], prompt: '', cases: [], mustContain: [], mustNotContain: [], benchmarkId: '', sampleLimit: 20, repeats: 1, concurrency: 1, iterations: 1, timeout: 600, baseline: true })
const agents = ref([])
const models = ref([])
const skills = ref([])
const benchmarks = ref([])
const cases = ref([])
const running = ref(false)
const job = ref(null)
const resultId = ref(null)
let timer

const step = computed(() => job.value ? 2 : form.type ? 1 : 0)
const currentType = computed(() => types.find(item => item.id === form.type) || types[0])
const installedBenchmarks = computed(() => benchmarks.value.filter(item => item.status === 'installed' && Number(item.item_count) > 0))
const selectedBenchmark = computed(() => benchmarks.value.find(item => item.id === form.benchmarkId))
const modelKey = model => `${model.profile || 'default'}::${model.id}`
const selectedModel = computed(() => models.value.find(item => modelKey(item) === form.modelKey))
const availableModels = computed(() => form.type === 'question' ? models.value.filter(item => item.source === 'litellm' || item.source === 'configured') : models.value)
const availableAgents = computed(() => {
  if (form.type !== 'question') return agents.value
  const direct = { agent: 'direct', detected_executable: 'LiteLLM API', capabilities: {} }
  return [direct, ...agents.value.filter(item => item.agent === 'codex')]
})
const isTerminal = computed(() => ['completed', 'failed', 'cancelled', 'canceled'].includes(job.value?.status))

function selectType(type) { form.type = type; job.value = null; router.replace({ query: { type } }); setDefaults() }
function agentLabel(agent) { return `${agent.agent}${agent.detected_executable ? ' · 可用' : ' · 未检测到'}` }
function agentDisabled(agent) { return agent.agent !== 'direct' && (!agent.detected_executable || (form.type !== 'question' && agent.capabilities?.skill_injection === false)) }
function modelLabel(model) { return `${model.id} · ${model.source === 'litellm' ? 'LiteLLM' : model.source}` }
function onModelChange() {}
function onBenchmarkChange() {
  if (selectedBenchmark.value) form.sampleLimit = Math.min(20, selectedBenchmark.value.item_count)
  if (selectedBenchmark.value?.task_type === 'repository_agent') form.agent = 'codex'
  else if (!['direct', 'codex'].includes(form.agent)) form.agent = 'direct'
}
async function onSkillsChange() {
  form.skills = form.skills.slice(0, 8); form.cases = []; cases.value = []
  if (form.skills.length === 1) {
    try { cases.value = (await fetchSkillCases(form.skills[0])).cases || [] } catch { cases.value = [] }
  }
}
function setDefaults() {
  const possibleAgents = availableAgents.value.filter(item => !agentDisabled(item))
  if (!possibleAgents.some(item => item.agent === form.agent)) form.agent = possibleAgents[0]?.agent || ''
  if (!availableModels.value.some(item => modelKey(item) === form.modelKey)) form.modelKey = availableModels.value[0] ? modelKey(availableModels.value[0]) : ''
  if (form.type === 'schematic') form.name ||= '原理图生成评测'
  if (form.type === 'skill') form.name ||= 'Skill 能力评测'
  if (form.type === 'question') form.name ||= '题库能力评测'
}
function validateForm() {
  if (!form.type) return '请先选择评测类型'
  if (!form.agent) return '请选择可用 Agent'
  if (!selectedModel.value) return '请选择运行模型'
  if (form.type === 'skill' && !form.skills.length) return '请至少选择一个 Skill'
  if (form.type === 'skill' && !form.prompt.trim() && !form.cases.length) return '请填写 Prompt 或选择 Skill 自带用例'
  if (form.type === 'schematic' && !form.prompt.trim()) return '请填写原理图需求 Prompt'
  if (form.type === 'question' && !form.benchmarkId) return '请选择题库'
  if (form.type === 'question' && selectedBenchmark.value?.task_type === 'repository_agent' && form.agent !== 'codex') return '仓库修复题库需要选择 Codex Agent'
  return ''
}
async function submit() {
  const error = validateForm(); if (error) return ElMessage.warning(error)
  running.value = true; job.value = { status: 'queued', progress: 0, phase: '正在提交', message: '' }
  try {
    if (form.type === 'question') await submitQuestion()
    else await submitAgentRun()
  } catch (error) {
    running.value = false; job.value = { status: 'failed', progress: 100, phase: '提交失败', message: error.response?.data?.detail || error.message }; ElMessage.error(job.value.message)
  }
}
async function submitAgentRun() {
  const selectedSkills = form.type === 'schematic' ? ['schematic-generation'] : form.skills
  const response = await triggerRun({ evaluation_type: form.type, user_id: 'local', task_name: form.name, skill: selectedSkills[0], skills: selectedSkills, agent: form.agent, profile: selectedModel.value.profile, model: selectedModel.value.id, case: form.cases, prompt: form.prompt.trim() || null, must_contain: form.mustContain, must_not_contain: form.mustNotContain, parallelism: form.concurrency, iterations: form.iterations, timeout_seconds: form.timeout, max_turns: 12, benchmark: form.baseline, collect_database_trace: selectedModel.value.source === 'litellm', require_model_verification: selectedModel.value.source === 'litellm', llm_judge: true })
  resultId.value = response.job_id; job.value = response; pollAgentJob(response.job_id)
}
async function submitQuestion() {
  const repo = selectedBenchmark.value?.task_type === 'repository_agent'
  const provider = await ensureAutoProvider({ agent: form.agent, model: selectedModel.value.id, profile: selectedModel.value.profile, task_kind: repo ? 'repo' : 'direct' })
  const experiment = await createExperiment({ name: form.name, provider_ids: [provider.id], benchmark_ids: [form.benchmarkId], repeats: form.repeats, sample_limit: form.sampleLimit, concurrency: form.concurrency, allow_unsafe_code: false, track: repo ? 'native_agent' : 'model_direct', random_seed: 42, sampling_strategy: 'stratified', budget: { timeout_seconds_per_task: form.timeout, max_output_tokens: 4096 } })
  resultId.value = experiment.id; job.value = experiment; pollExperiment(experiment.id)
}
function schedule(fn) { clearTimeout(timer); timer = setTimeout(fn, 1200) }
async function pollAgentJob(id) { try { job.value = await fetchJob(id); if (!isTerminal.value) schedule(() => pollAgentJob(id)); else running.value = false } catch (error) { running.value = false; ElMessage.error(error.message) } }
async function pollExperiment(id) { try { const data = await fetchExperiment(id); const total = Math.max(1, (data.selected_items || 1) * (data.repeats || 1)); data.progress = data.status === 'completed' ? 100 : Math.round(((data.completed_jobs || data.summary?.count || 0) / total) * 100); data.phase = '题库评测'; data.message = `已完成 ${data.completed_jobs || data.summary?.count || 0} / ${total} 个任务`; job.value = data; if (!isTerminal.value) schedule(() => pollExperiment(id)); else running.value = false } catch (error) { running.value = false; ElMessage.error(error.message) } }
function statusText(status) { return ({ queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', canceled: '已取消' })[status] || status || '准备中' }
function statusType(status) { return status === 'completed' ? 'success' : status === 'failed' ? 'danger' : status?.includes('cancel') ? 'info' : 'warning' }
function openResult() { router.push(`/results/${form.type}/${resultId.value}`) }
function resetRun() { job.value = null; resultId.value = null; running.value = false }

onMounted(async () => {
  const results = await Promise.allSettled([fetchAgents(), fetchModels(), fetchSkills(), fetchBenchmarks()])
  agents.value = results[0].status === 'fulfilled' ? results[0].value : []
  models.value = results[1].status === 'fulfilled' ? results[1].value.models || [] : []
  skills.value = results[2].status === 'fulfilled' ? results[2].value.skills || [] : []
  benchmarks.value = results[3].status === 'fulfilled' ? results[3].value : []
  form.benchmarkId = installedBenchmarks.value[0]?.id || ''
  setDefaults()
})
watch(() => route.query.type, value => { const type = normalizeType(value); if (type && type !== form.type) selectType(type) })
watch(() => form.agent, () => { if (form.type === 'question' && selectedBenchmark.value?.task_type === 'repository_agent' && form.agent !== 'codex') form.agent = 'codex' })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.steps{width:440px;background:transparent}.type-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.type-card{position:relative;display:grid;grid-template-columns:50px 1fr;grid-template-rows:auto auto;text-align:left;gap:3px 14px;padding:20px;border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--text);cursor:pointer;transition:.2s}.type-card:hover,.type-card.active{border-color:var(--brand);box-shadow:0 8px 24px rgba(39,91,225,.1);transform:translateY(-1px)}.type-card strong{font-size:16px}.type-card small{color:var(--muted);line-height:1.5}.type-icon{grid-row:1/3;width:46px;height:46px;display:grid;place-items:center;border-radius:12px;background:var(--brand-soft);color:var(--brand);font-size:22px}.check{position:absolute;right:12px;top:12px;color:var(--brand);opacity:0}.active .check{opacity:1}.form-panel{margin-top:16px}.panel-title,.progress-head,.submit-row{display:flex;justify-content:space-between;align-items:center}.form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 18px}.form-grid>:first-child{grid-column:span 1}.runtime-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.field-help{font-size:12px;color:var(--muted);margin-top:6px}.option-meta{float:right;color:var(--muted);margin-left:20px;font-size:12px}.submit-row{margin-top:24px;padding-top:20px;border-top:1px solid var(--line)}.selection-summary{display:flex;gap:10px;align-items:center}.selection-summary span{font-size:13px;color:var(--muted);padding-left:10px;border-left:1px solid var(--line)}.progress-panel{margin-top:16px}.progress-head h3{margin:4px 0}.progress-head p{margin:0 0 18px;color:var(--muted)}.result-actions{margin-top:18px}@media(max-width:900px){.steps{display:none}.type-grid,.form-grid,.runtime-grid{grid-template-columns:1fr}.submit-row{align-items:flex-end}.selection-summary{flex-direction:column;align-items:flex-start}.type-grid{gap:10px}}
</style>
