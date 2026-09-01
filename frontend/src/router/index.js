import { createRouter, createWebHistory } from 'vue-router'
import EvalRun from '../views/EvalRun.vue'
import EvalResult from '../views/EvalResult.vue'
import Discovery from '../views/Discovery.vue'

const routes = [
  { path: '/', redirect: '/run' },
  { path: '/run', name: 'run', component: EvalRun, meta: { title: '评测运行' } },
  { path: '/result', name: 'result', component: EvalResult, meta: { title: '评测结果' } },
  { path: '/discovery', name: 'discovery', component: Discovery, meta: { title: 'Skill / Agent 管理' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} - Agent Eval` : 'Agent Eval'
})

export default router
