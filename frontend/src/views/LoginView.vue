<template>
  <main class="login-page">
    <section class="login-panel">
      <span class="eyebrow">AGENT EVAL</span>
      <h1>登录评测平台</h1>
      <p>请输入工号。当前为内网追踪登录，密码仅用于完成登录流程，不会校验或保存。</p>
      <el-form :model="form" label-position="top" @submit.prevent="submit">
        <el-form-item label="工号">
          <el-input v-model.trim="form.employee_no" autocomplete="username" autofocus />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="loading">登录</el-button>
      </el-form>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { login } from '../api'

const emit = defineEmits(['logged-in'])
const form = reactive({ employee_no: '', password: '' })
const loading = ref(false)

async function submit() {
  if (!form.employee_no || !form.password) return ElMessage.warning('请输入工号和密码')
  loading.value = true
  try {
    const identity = await login(form)
    emit('logged-in', identity)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page{min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#eef1f5,#fafafa);padding:24px}.login-panel{width:min(420px,100%);background:white;border:1px solid var(--line);border-radius:16px;padding:34px;box-shadow:0 18px 55px rgba(20,24,31,.1)}h1{margin:8px 0 10px;font-size:28px}.login-panel>p{color:var(--muted);font-size:13px;line-height:1.7;margin-bottom:24px}.el-button{width:100%;margin-top:4px}
</style>
