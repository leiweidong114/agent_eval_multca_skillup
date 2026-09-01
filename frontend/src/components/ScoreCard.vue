<template>
  <div class="score-grid">
    <div class="score-item">
      <div class="label">Task Score</div>
      <div class="value" :class="valueClass(scores.task_score)">{{ fmt(scores.task_score) }}</div>
    </div>
    <div class="score-item">
      <div class="label">Baseline Score</div>
      <div class="value muted">{{ fmt(scores.baseline_score) }}</div>
    </div>
    <div class="score-item">
      <div class="label">Skill Gain</div>
      <div class="value" :class="gainClass(scores.skill_gain)">{{ fmt(scores.skill_gain) }}</div>
    </div>
    <div class="score-item">
      <div class="label">Execution Stability</div>
      <div class="value">{{ fmt(scores.execution_stability) }}</div>
    </div>
    <div class="score-item">
      <div class="label">Skill Quality</div>
      <div class="value" :class="valueClass(scores.skill_quality_score)">{{ fmt(scores.skill_quality_score) }}</div>
    </div>
    <div class="score-item">
      <div class="label">Model Trace</div>
      <div class="value" :class="valueClass(scores.model_trace_score)">{{ fmt(scores.model_trace_score) }}</div>
    </div>
    <div class="score-item">
      <div class="label">Tokens</div>
      <div class="value muted">{{ scores.total_tokens }}</div>
    </div>
    <div class="score-item">
      <div class="label">Duration (ms)</div>
      <div class="value muted">{{ scores.total_duration_ms }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  scores: {
    type: Object,
    required: true,
  },
})

function fmt(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return Number(v).toFixed(2)
  return v
}
function valueClass(v) {
  if (v === null || v === undefined) return ''
  return v >= 80 ? 'good' : v >= 60 ? 'warn' : 'bad'
}
function gainClass(v) {
  if (v === null || v === undefined) return ''
  return v > 0 ? 'good' : v < 0 ? 'bad' : ''
}
</script>

<style scoped>
.score-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.score-item {
  text-align: center;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
}
.label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.value {
  font-size: 26px;
  font-weight: 700;
}
.value.muted {
  color: #909399;
}
.value.good {
  color: #67c23a;
}
.value.warn {
  color: #e6a23c;
}
.value.bad {
  color: #f56c6c;
}
</style>
