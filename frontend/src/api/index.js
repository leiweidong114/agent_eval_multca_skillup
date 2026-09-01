import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 100000,
})

export const fetchAgents = () => http.get('/agents').then((r) => r.data)
export const fetchModelConfig = () => http.get('/model-config').then((r) => r.data)
export const fetchDatabaseHealth = () => http.get('/database/health').then((r) => r.data)
export const fetchSkills = () => http.get('/skills').then((r) => r.data)
export const fetchSkillCases = (name) => http.get(`/skills/${name}/cases`).then((r) => r.data)

export const triggerRun = (payload) => http.post('/run', payload).then((r) => r.data)
export const triggerValidate = (payload) => http.post('/validate', payload).then((r) => r.data)
export const fetchJobs = () => http.get('/jobs').then((r) => r.data)
export const fetchJob = (id) => http.get(`/jobs/${id}`).then((r) => r.data)
export const cancelJob = (id) => http.post(`/jobs/${id}/cancel`).then((r) => r.data)
export const uploadSkill = (name, archive) => {
  const data = new FormData()
  data.append('name', name)
  data.append('archive', archive)
  return http.post('/skills/upload', data).then((r) => r.data)
}
export const fetchSkillVersions = () => http.get('/skills/versions').then((r) => r.data)
export const deleteSkillVersion = (name, version) => http.delete(`/skills/${name}/versions/${version}`).then((r) => r.data)
export const fetchRetention = () => http.get('/privacy/retention').then((r) => r.data)
export const cleanupRetention = () => http.post('/privacy/retention/cleanup', { confirm: true }).then((r) => r.data)
export const fetchSchematicExample = () => http.get('/schematic/example').then((r) => r.data)
export const generateSchematic = (payload) => http.post('/schematic/generate', payload).then((r) => r.data)
export const fetchSchematicProject = (id) => http.get(`/schematic/projects/${id}`).then((r) => r.data)

export const fetchRuns = () => http.get('/runs').then((r) => r.data)
export const fetchRun = (runId) => http.get(`/runs/${runId}`).then((r) => r.data)

export default http
