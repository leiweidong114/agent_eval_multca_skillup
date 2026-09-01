import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView, meta: { title: '首页', description: '统一查看评测资产、运行状态与关键结果' } },
  { path: '/evaluations/new', name: 'new-evaluation', component: () => import('../views/NewEvaluation.vue'), meta: { title: '新建评测', description: '创建原理图、题库或单/多 Skill 联合评测' } },
  { path: '/benchmarks', name: 'benchmarks', component: () => import('../views/BenchmarkManager.vue'), meta: { title: '题库管理', description: '查看标准题库、私有题库及题目内容' } },
  { path: '/skills', name: 'skills', component: () => import('../views/SkillCatalog.vue'), meta: { title: 'Skill 管理', description: '查看、上传和管理当前支持评测的 Skill' } },
  { path: '/results', name: 'results', component: () => import('../views/ResultsView.vue'), meta: { title: '评测结果', description: '按评测类型查看历史任务与结果' } },
  { path: '/results/:type/:id', name: 'result-detail', component: () => import('../views/ResultDetail.vue'), meta: { title: '结果详情', description: '查看评分、证据和运行配置' } },
  { path: '/runtimes', name: 'runtimes', component: () => import('../views/RuntimeCatalog.vue'), meta: { title: '模型与 Agent', description: '查看本地 Agent 和 LiteLLM 可用模型' } },
  { path: '/model-eval', redirect: { path: '/evaluations/new', query: { type: 'question' } } },
  { path: '/eval', redirect: { path: '/evaluations/new', query: { type: 'skill' } } },
  { path: '/schematic', redirect: { path: '/evaluations/new', query: { type: 'schematic' } } },
  { path: '/result', redirect: '/results' },
  { path: '/discovery', redirect: '/runtimes' },
]

const router = createRouter({ history: createWebHistory(), routes })
router.afterEach((to) => { document.title = `${to.meta.title || '评测平台'} - Agent Eval` })
export default router
