<template>
  <div>
    <el-card shadow="never">
      <template #header><b>Skill 上传与版本管理</b></template>
      <el-alert title="ZIP 中必须包含一个根 SKILL.md；每个内容版本使用 SHA-256 独立保存，源文件不会被改动。" type="info" show-icon />
      <el-form inline style="margin-top:18px">
        <el-form-item label="Skill 名称"><el-input v-model="name" placeholder="lowercase-name" /></el-form-item>
        <el-form-item><el-upload :auto-upload="false" :limit="1" accept=".zip" :on-change="pick"><el-button>选择 ZIP</el-button></el-upload></el-form-item>
        <el-form-item><el-button type="primary" :disabled="!name || !file" :loading="uploading" @click="submit">上传</el-button></el-form-item>
      </el-form>
    </el-card>
    <el-card shadow="never" style="margin-top:16px">
      <template #header><div class="row"><b>已上传版本</b><el-button size="small" @click="load">刷新</el-button></div></template>
      <el-table :data="versions" stripe>
        <el-table-column prop="name" label="Skill" /><el-table-column prop="version" label="版本" />
        <el-table-column prop="uploaded_at" label="上传时间" min-width="180" /><el-table-column prop="file_count" label="文件数" width="90" />
        <el-table-column label="操作" width="100"><template #default="{row}"><el-button type="danger" link @click="remove(row)">删除</el-button></template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { uploadSkill, fetchSkillVersions, deleteSkillVersion } from '../api'
const name=ref(''); const file=ref(null); const versions=ref([]); const uploading=ref(false)
const pick=(item)=>{file.value=item.raw}
async function load(){versions.value=await fetchSkillVersions()}
async function submit(){uploading.value=true;try{await uploadSkill(name.value,file.value);ElMessage.success('上传成功');await load()}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{uploading.value=false}}
async function remove(row){await ElMessageBox.confirm(`删除 ${row.skill_id}？`,'确认');await deleteSkillVersion(row.name,row.version);await load()}
onMounted(load)
</script>
<style scoped>.row{display:flex;justify-content:space-between;align-items:center}</style>
