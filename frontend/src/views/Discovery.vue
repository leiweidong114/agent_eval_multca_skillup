<template>
  <div class="discovery">
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
                <el-tag v-if="row.has_skill_md" type="success" size="small">有</el-tag>
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchAgents, fetchSkills } from '../api'

const skills = ref([])
const agents = ref([])

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
onMounted(() => {
  loadSkills()
  loadAgents()
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
