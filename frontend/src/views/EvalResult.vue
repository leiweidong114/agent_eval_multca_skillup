<template>
  <div class="eval-result">
    <el-card shadow="never">
      <template #header>
        <div class="header-row">
          <span class="card-title">历史评测记录</span>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>

      <el-table v-if="runs.length" :data="runs" stripe style="width: 100%" @row-click="openDetail">
        <el-table-column prop="run_id" label="Run ID" min-width="280" show-overflow-tooltip />
        <el-table-column label="Agent" min-width="120">
          <template #default="{ row }">{{ row.report.agent }}</template>
        </el-table-column>
        <el-table-column label="模型" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.report.model }}</template>
        </el-table-column>
        <el-table-column label="Task Score" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="scoreType(row.report.scores?.task_score)">
              {{ row.report.scores?.task_score ?? '—' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Skill Gain" width="120" align="center">
          <template #default="{ row }">
            {{ row.report.scores?.skill_gain ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column label="迭代数" width="90" align="center">
          <template #default="{ row }">{{ row.report.iterations }}</template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="暂无评测记录" />
    </el-card>

    <el-drawer
      v-model="drawer"
      :title="selected?.run_id || '评测详情'"
      size="60%"
      destroy-on-close
    >
      <template v-if="selected">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="Agent">{{ selected.report.agent }}</el-descriptions-item>
          <el-descriptions-item label="模型">{{ selected.report.model }}</el-descriptions-item>
          <el-descriptions-item label="Skill" :span="2">{{ selected.report.skill }}</el-descriptions-item>
          <el-descriptions-item label="Run ID" :span="2">{{ selected.report.run_id }}</el-descriptions-item>
          <el-descriptions-item label="结果目录" :span="2">
            <el-text size="small">{{ selected.report.result_dir }}</el-text>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="selected.report.scores" style="margin-top: 16px">
          <h4>评分</h4>
          <ScoreCard :scores="selected.report.scores" />
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchRuns } from '../api'
import ScoreCard from '../components/ScoreCard.vue'

const runs = ref([])
const drawer = ref(false)
const selected = ref(null)

function scoreType(v) {
  if (v === null || v === undefined) return 'info'
  if (v >= 80) return 'success'
  if (v >= 60) return 'warning'
  return 'danger'
}

async function load() {
  try {
    runs.value = await fetchRuns()
  } catch (e) {
    ElMessage.error(`获取记录失败: ${e.message}`)
  }
}

function openDetail(row) {
  selected.value = row
  drawer.value = true
}

onMounted(load)
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
