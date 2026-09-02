<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">BENCHMARK LIBRARY</span><h1>题库管理</h1><p>集中查看当前支持的标准题库、自定义题库及其题目内容。</p></div><div class="hero-actions"><el-button @click="load" :loading="loading"><el-icon><Refresh /></el-icon> 刷新</el-button><el-button type="primary" @click="importVisible=true"><el-icon><Upload /></el-icon> 导入 JSON</el-button></div></section>
    <div class="summary-strip"><div><b>{{ benchmarks.length }}</b><span>全部题库</span></div><div><b>{{ installed.length }}</b><span>已安装</span></div><div><b>{{ totalItems.toLocaleString() }}</b><span>可评测题目</span></div><div><b>{{ taskTypes }}</b><span>任务类型</span></div></div>
    <el-card shadow="never" class="panel">
      <div class="toolbar"><el-input v-model="keyword" clearable placeholder="搜索名称、描述或类型" :prefix-icon="Search" /><el-segmented v-model="status" :options="statusOptions" /></div>
      <el-table :data="filtered" v-loading="loading" @row-click="openDetail" row-class-name="clickable-row">
        <el-table-column label="题库" min-width="240"><template #default="{row}"><div class="title-cell"><b>{{ row.name }}</b><span>{{ row.description }}</span></div></template></el-table-column>
        <el-table-column prop="task_type" label="任务类型" min-width="150"><template #default="{row}"><el-tag effect="plain">{{ typeName(row.task_type) }}</el-tag></template></el-table-column>
        <el-table-column prop="language" label="语言" width="90" />
        <el-table-column prop="item_count" label="题目数" width="100" sortable />
        <el-table-column label="状态" width="105"><template #default="{row}"><el-tag :type="row.status === 'installed' ? 'success' : 'info'">{{ row.status === 'installed' ? '已安装' : '可用' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="{row}"><el-button link type="primary" @click.stop="openDetail(row)">查看内容</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drawer" size="68%" :title="detail?.name || '题库详情'">
      <template v-if="detail">
        <div class="detail-meta"><el-tag>{{ typeName(detail.task_type) }}</el-tag><span>{{ detail.item_count }} 题</span><span>版本 {{ detail.version }}</span><span>{{ detail.license }}</span></div>
        <p class="detail-description">{{ detail.description }}</p>
        <h3>分类分布</h3><div class="category-list"><el-tag v-for="cat in detail.categories" :key="cat.category" effect="plain">{{ cat.category }} · {{ cat.item_count }}</el-tag></div>
        <h3>题目预览</h3>
        <el-table :data="items" v-loading="detailLoading" max-height="560">
          <el-table-column prop="item_key" label="编号" width="130" />
          <el-table-column prop="category" label="分类" width="130" />
          <el-table-column label="Prompt" min-width="360"><template #default="{row}"><div class="prompt-cell">{{ row.prompt }}</div></template></el-table-column>
          <el-table-column prop="scorer_type" label="评分器" width="120" />
        </el-table>
        <p v-if="detail.item_count > items.length" class="field-help">当前预览前 {{ items.length }} 题，共 {{ detail.item_count }} 题。</p>
      </template>
    </el-drawer>

    <el-dialog v-model="importVisible" title="导入自定义题库" width="680px">
      <el-alert type="info" :closable="false" title="粘贴符合题库清单格式的 JSON，至少包含 id、name、description 和 items。" />
      <el-input v-model="importText" type="textarea" :rows="16" class="json-input" placeholder='{"id":"my-bank","name":"...","description":"...","items":[...]}' />
      <template #footer><el-button @click="importVisible=false">取消</el-button><el-button type="primary" :loading="importing" @click="submitImport">导入题库</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { fetchBenchmark, fetchBenchmarkItems, fetchBenchmarks, importBenchmark } from '../api'
const benchmarks=ref([]),loading=ref(false),keyword=ref(''),status=ref('all'),drawer=ref(false),detail=ref(null),items=ref([]),detailLoading=ref(false),importVisible=ref(false),importText=ref(''),importing=ref(false)
const statusOptions=[{label:'全部',value:'all'},{label:'已安装',value:'installed'},{label:'未安装',value:'available'}]
const installed=computed(()=>benchmarks.value.filter(x=>x.status==='installed'&&x.item_count>0))
const totalItems=computed(()=>installed.value.reduce((sum,x)=>sum+Number(x.item_count||0),0))
const taskTypes=computed(()=>new Set(installed.value.map(x=>x.task_type)).size)
const filtered=computed(()=>benchmarks.value.filter(x=>(status.value==='all'||x.status===status.value)&&`${x.name} ${x.description} ${x.task_type}`.toLowerCase().includes(keyword.value.toLowerCase())))
const typeName=t=>({multiple_choice:'选择题',math:'数学推理',code_generation:'代码生成',repository_agent:'仓库 Agent',custom_qa:'自定义问答'}[t]||t)
async function load(){loading.value=true;try{benchmarks.value=await fetchBenchmarks()}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{loading.value=false}}
async function openDetail(row){drawer.value=true;detailLoading.value=true;items.value=[];try{[detail.value,items.value]=await Promise.all([fetchBenchmark(row.id),fetchBenchmarkItems(row.id,100)])}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{detailLoading.value=false}}
async function submitImport(){try{importing.value=true;const payload=JSON.parse(importText.value);await importBenchmark(payload);ElMessage.success('题库导入成功');importVisible.value=false;importText.value='';await load()}catch(e){ElMessage.error(e.response?.data?.detail||e.message||'JSON 格式错误')}finally{importing.value=false}}
onMounted(load)
</script>
<style scoped>
.hero-actions,.toolbar,.detail-meta,.category-list{display:flex;gap:10px;align-items:center}.summary-strip{display:grid;grid-template-columns:repeat(4,1fr);background:var(--surface);border:1px solid var(--line);border-radius:14px}.summary-strip div{display:flex;flex-direction:column;padding:18px 24px;border-right:1px solid var(--line)}.summary-strip div:last-child{border:0}.summary-strip b{font-size:24px}.summary-strip span,.title-cell span,.detail-meta span,.field-help{color:var(--muted);font-size:12px}.toolbar{justify-content:space-between;margin-bottom:18px}.toolbar .el-input{max-width:360px}.title-cell{display:flex;flex-direction:column;gap:5px}.title-cell span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:440px}.detail-description{color:var(--muted);line-height:1.7}.category-list{flex-wrap:wrap;margin-bottom:24px}.prompt-cell{white-space:pre-wrap;line-height:1.55;max-height:130px;overflow:auto}.json-input{margin-top:14px}@media(max-width:800px){.summary-strip{grid-template-columns:repeat(2,1fr)}.toolbar{align-items:stretch;flex-direction:column}.hero-actions{display:none}}
</style>
