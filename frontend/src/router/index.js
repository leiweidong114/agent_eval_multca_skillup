import { createRouter, createWebHistory } from 'vue-router'
import EvalRun from '../views/EvalRun.vue'
import EvalResult from '../views/EvalResult.vue'
import Discovery from '../views/Discovery.vue'
import SkillManager from '../views/SkillManager.vue'
import Schematic from '../views/Schematic.vue'
import ModelEval from '../views/ModelEval.vue'

const routes = [
  { path: '/', redirect: '/run' },
  { path: '/run', name: 'run', component: EvalRun, meta: { title: '评测运行' } },
  { path: '/result', name: 'result', component: EvalResult, meta: { title: '评测结果' } },
  { path: '/discovery', name: 'discovery', component: Discovery, meta: { title: 'Skill / Agent 管理' } },
  { path: '/skills', name: 'skills', component: SkillManager, meta: { title: 'Skill 版本管理' } },
  { path: '/schematic', name: 'schematic', component: Schematic, meta: { title: '原理图生成评测' } },
  { path: '/model-eval', name: 'model-eval', component: ModelEval, meta: { title: '模型与题库评测', fullscreen: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} - Agent Eval` : 'Agent Eval'
})

export default router
