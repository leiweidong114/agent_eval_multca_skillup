<template>
  <div class="page-stack">
    <section class="hero compact"><div><span class="eyebrow">SKILL REGISTRY</span><h1>Skill 管理</h1><p>查看可参与评测的 Skill、用例数量与完整指令内容。</p></div><el-button type="primary" @click="uploadOpen=true"><el-icon><Upload /></el-icon> 上传 Skill</el-button></section>
    <el-card shadow="never" class="panel">
      <div class="toolbar"><el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索 Skill"/><span>{{ filtered.length }} 个可评测 Skill</span></div>
      <div class="skill-grid" v-loading="loading">
        <article v-for="skill in filtered" :key="skill.name" class="skill-card" @click="openSkill(skill)">
          <div class="skill-mark"><el-icon><MagicStick /></el-icon></div><div class="skill-main"><h3>{{ skill.name }}</h3><p>{{ skill.version ? `上传版本 ${skill.version}` : '本地内置 Skill' }}</p></div><el-tag :type="skill.version ? 'warning' : 'success'" effect="plain">{{ skill.version ? '已上传' : '内置' }}</el-tag><el-icon class="arrow"><ArrowRight /></el-icon>
        </article>
        <el-empty v-if="!loading&&!filtered.length" description="没有匹配的 Skill" />
      </div>
    </el-card>
    <el-drawer v-model="drawer" size="68%" :title="detail?.name || 'Skill 详情'">
      <template v-if="detail"><div class="detail-meta"><el-tag type="success">可评测</el-tag><span>{{ detail.case_count }} 个内置用例</span><span>{{ detail.files?.length }} 个文件</span></div><h3>SKILL.md</h3><pre class="skill-content">{{ detail.content }}</pre><h3>文件清单</h3><div class="file-list"><code v-for="file in detail.files" :key="file">{{ file }}</code></div></template>
    </el-drawer>
    <el-dialog v-model="uploadOpen" title="上传 Skill ZIP" width="520px"><el-form label-position="top"><el-form-item label="Skill 名称"><el-input v-model="uploadName" placeholder="例如 my-evaluator" /></el-form-item><el-form-item label="ZIP 文件"><input type="file" accept=".zip" @change="onFile" /></el-form-item></el-form><template #footer><el-button @click="uploadOpen=false">取消</el-button><el-button type="primary" :loading="uploading" @click="doUpload">上传</el-button></template></el-dialog>
  </div>
</template>
<script setup>
import {computed,onMounted,ref} from 'vue';import{ElMessage}from'element-plus';import{Search}from'@element-plus/icons-vue';import{fetchSkill,fetchSkills,uploadSkill}from'../api'
const skills=ref([]),keyword=ref(''),loading=ref(false),drawer=ref(false),detail=ref(null),uploadOpen=ref(false),uploadName=ref(''),uploadFile=ref(null),uploading=ref(false)
const filtered=computed(()=>skills.value.filter(x=>x.name.toLowerCase().includes(keyword.value.toLowerCase())))
async function load(){loading.value=true;try{skills.value=(await fetchSkills()).skills||[]}finally{loading.value=false}}
async function openSkill(skill){drawer.value=true;detail.value=null;try{detail.value=await fetchSkill(skill.name)}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}}
function onFile(e){uploadFile.value=e.target.files?.[0]||null}
async function doUpload(){if(!uploadName.value||!uploadFile.value)return ElMessage.warning('请填写名称并选择 ZIP 文件');uploading.value=true;try{await uploadSkill(uploadName.value,uploadFile.value);ElMessage.success('Skill 上传成功');uploadOpen.value=false;await load()}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{uploading.value=false}}
onMounted(load)
</script>
<style scoped>
.toolbar,.detail-meta{display:flex;align-items:center;justify-content:space-between;gap:12px}.toolbar .el-input{max-width:360px}.toolbar span,.detail-meta span{color:var(--muted);font-size:13px}.skill-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:18px}.skill-card{display:grid;grid-template-columns:48px 1fr auto 18px;align-items:center;gap:12px;padding:16px;border:1px solid var(--line);border-radius:12px;cursor:pointer;transition:.2s}.skill-card:hover{border-color:var(--brand);transform:translateY(-1px)}.skill-mark{width:44px;height:44px;display:grid;place-items:center;border-radius:11px;background:var(--brand-soft);color:var(--brand);font-size:20px}.skill-main h3{margin:0 0 5px;font-size:15px}.skill-main p{margin:0;color:var(--muted);font-size:12px}.arrow{color:var(--muted)}.skill-content{padding:18px;background:#111827;color:#d8e1f0;border-radius:12px;white-space:pre-wrap;line-height:1.65;max-height:520px;overflow:auto}.file-list{display:flex;flex-wrap:wrap;gap:8px}.file-list code{background:var(--surface-2);padding:6px 9px;border-radius:6px}@media(max-width:800px){.skill-grid{grid-template-columns:1fr}}
</style>
