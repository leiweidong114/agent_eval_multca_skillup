<template>
  <div class="eval-result">
    <el-card shadow="never">
      <template #header>
        <div class="header-row">
          <span class="card-title">历史评测记录</span>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>

      <el-input v-model="filter" placeholder="按 Run ID、Agent、模型或 Skill 过滤" clearable style="width:360px;margin-bottom:12px" />

      <el-table v-if="filtered.length" :data="paged" stripe style="width: 100%" @row-click="openDetail">
        <el-table-column prop="user_id" label="用户" min-width="100" />
        <el-table-column prop="task_name" label="任务" min-width="150" show-overflow-tooltip />
        <el-table-column prop="run_id" label="Run ID" min-width="220" show-overflow-tooltip />
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
        <el-table-column label="模型确认" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="row.report.model_verification?.verified ? 'success' : 'danger'">
              {{ row.report.model_verification?.verified ? '已确认' : '未确认' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="暂无评测记录" />
      <el-pagination v-if="filtered.length" v-model:current-page="page" :page-size="10" :total="filtered.length" layout="prev, pager, next, total" style="margin-top:12px" />
    </el-card>

    <el-drawer
      v-model="drawer"
      :title="selected?.run_id || '评测详情'"
      size="60%"
      destroy-on-close
    >
      <template v-if="selected">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="用户">{{ selected.report.user_id }}</el-descriptions-item>
          <el-descriptions-item label="任务">{{ selected.report.task_name }}</el-descriptions-item>
          <el-descriptions-item label="Agent">{{ selected.report.agent }}</el-descriptions-item>
          <el-descriptions-item label="模型">{{ selected.report.model }}</el-descriptions-item>
          <el-descriptions-item label="Skill" :span="2">{{ selected.report.skill }}</el-descriptions-item>
          <el-descriptions-item label="Run ID" :span="2">{{ selected.report.run_id }}</el-descriptions-item>
          <el-descriptions-item label="用户 ID">{{ selected.report.user_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="任务 ID">{{ selected.report.task_id || selected.report.run_id }}</el-descriptions-item>
          <el-descriptions-item label="结果目录" :span="2">
            <el-text size="small">{{ selected.report.result_dir }}</el-text>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="selected.report.scores" style="margin-top: 16px">
          <h4>评分</h4>
          <ScoreCard :scores="selected.report.scores" />
        </div>
        <div v-if="selected.report.database_trace" style="margin-top:16px">
          <h4>模型交互过程</h4>
          <el-alert :title="`关联方式：${selected.report.database_trace.correlation || 'unknown'}`" type="info" />
          <pre class="trace">{{JSON.stringify(selected.report.database_trace,null,2)}}</pre>
          <h4>指定模型数据库校验</h4>
          <el-alert
            :title="selected.report.model_verification?.verified ? '已确认 Agent 调用了指定模型' : '未能确认指定模型调用'"
            :type="selected.report.model_verification?.verified ? 'success' : 'error'"
            :closable="false"
          />
          <pre class="trace">{{JSON.stringify(selected.report.model_verification,null,2)}}</pre>
        </div>
        <div v-if="selected.report.scoring" style="margin-top:16px">
          <h4>三维综合评分</h4>
          <pre class="trace">{{JSON.stringify(selected.report.scoring,null,2)}}</pre>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchRuns } from '../api'
import ScoreCard from '../components/ScoreCard.vue'

const runs = ref([])
const drawer = ref(false)
const selected = ref(null)
const filter=ref(''); const page=ref(1)
const filtered=computed(()=>runs.value.filter(r=>JSON.stringify({id:r.run_id,user:r.user_id,task:r.task_name,agent:r.report.agent,model:r.report.model,skill:r.report.skill}).toLowerCase().includes(filter.value.toLowerCase())))
const paged=computed(()=>filtered.value.slice((page.value-1)*10,page.value*10))
watch(filter,()=>{page.value=1})

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
.trace{background:#f5f7fa;padding:12px;max-height:360px;overflow:auto}
</style>
