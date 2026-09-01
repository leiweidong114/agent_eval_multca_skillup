<template>
  <div class="discovery">
    <el-alert
      :title="databaseTitle"
      :type="database.status === 'ok' ? 'success' : 'warning'"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />
    <el-alert v-if="database.trace_note" :title="database.trace_note" :type="database.exact_trace_available?'success':'info'" :closable="false" show-icon style="margin-bottom:16px" />
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div class="header-row">
              <span class="card-title">可用 Skill</span>
              <el-button size="small" @click="loadSkills">刷新</el-button>
            </div>
          </template>
          <el-table v-if="skills.length" :data="skills" stripe>
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="path" label="路径" min-width="260" show-overflow-tooltip />
            <el-table-column label="SKILL.md" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.has_skill_md !== false" type="success" size="small">有</el-tag>
                <el-tag v-else type="danger" size="small">无</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无 Skill" />
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div class="header-row">
              <span class="card-title">可用 Agent</span>
              <el-button size="small" @click="loadAgents">刷新</el-button>
            </div>
          </template>
          <el-table v-if="agents.length" :data="agents" stripe>
            <el-table-column prop="agent" label="Agent" min-width="140" />
            <el-table-column prop="default_command" label="默认命令" min-width="140" />
            <el-table-column label="本地检测" width="110" align="center">
              <template #default="{ row }">
                <el-tag :type="row.detected_executable ? 'success' : 'info'" size="small">
                  {{ row.detected_executable ? '已找到' : '未检测' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无 Agent" />
        </el-card>
      </el-col>
    </el-row>
    <el-card shadow="never" style="margin-top:16px">
      <template #header><div class="header-row"><span class="card-title">隐私与产物保留</span><el-button size="small" @click="loadRetention">刷新</el-button></div></template>
      <el-descriptions :column="3" border><el-descriptions-item label="保留天数">{{retention.retention_days??'—'}}</el-descriptions-item><el-descriptions-item label="待清理目录">{{retention.expired?.length??0}}</el-descriptions-item><el-descriptions-item label="内容采集">默认关闭</el-descriptions-item></el-descriptions>
      <el-button v-if="retention.expired?.length" type="danger" plain style="margin-top:12px" @click="cleanup">清理过期产物</el-button>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchAgents, fetchDatabaseHealth, fetchSkills, fetchRetention, cleanupRetention } from '../api'

const skills = ref([])
const agents = ref([])
const database = ref({ status: 'checking' })
const databaseTitle = ref('正在检查 PostgreSQL 连接')
const retention = ref({})

async function loadSkills() {
  try {
    const data = await fetchSkills()
    skills.value = data.skills || []
  } catch (e) {
    ElMessage.error(`获取 Skill 失败: ${e.message}`)
  }
}
async function loadAgents() {
  try {
    agents.value = await fetchAgents()
  } catch (e) {
    ElMessage.error(`获取 Agent 失败: ${e.message}`)
  }
}
async function loadDatabase() {
  try {
    database.value = await fetchDatabaseHealth()
    databaseTitle.value = database.value.status === 'ok'
      ? `PostgreSQL 已连接：${database.value.database}，交互记录 ${database.value.spend_log_count}`
      : `PostgreSQL 未连接：${database.value.error || database.value.status}`
  } catch (e) {
    database.value = { status: 'error' }
    databaseTitle.value = `PostgreSQL 检查失败：${e.message}`
  }
}
async function loadRetention(){retention.value=await fetchRetention()}
async function cleanup(){await ElMessageBox.confirm(`确定删除 ${retention.value.expired.length} 个过期运行目录？`,'确认');const data=await cleanupRetention();ElMessage.success(`已删除 ${data.deleted.length} 个目录`);await loadRetention()}
onMounted(() => {
  loadSkills()
  loadAgents()
  loadDatabase()
  loadRetention()
})
</script>

<style scoped>
.card-title {
  font-weight: 600;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
