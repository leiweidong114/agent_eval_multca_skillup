import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 100000,
})

export const fetchAgents = () => http.get('/agents').then((r) => r.data)
export const fetchModelConfig = () => http.get('/model-config').then((r) => r.data)
export const fetchSkills = () => http.get('/skills').then((r) => r.data)
export const fetchSkillCases = (name) => http.get(`/skills/${name}/cases`).then((r) => r.data)

export const triggerRun = (payload) => http.post('/run', payload).then((r) => r.data)
export const triggerValidate = (payload) => http.post('/validate', payload).then((r) => r.data)

export const fetchRuns = () => http.get('/runs').then((r) => r.data)
export const fetchRun = (runId) => http.get(`/runs/${runId}`).then((r) => r.data)

export default http
