<template>
  <div>
    <el-card shadow="never">
      <template #header><div class="row"><b>原理图全流程评测</b><div><el-button @click="addComponent">添加器件</el-button><el-button @click="addConnection">添加连线</el-button><el-button @click="loadExample">加载 147 示例</el-button><el-button type="primary" :loading="generating" @click="generate">生成并专项评分</el-button></div></div></template>
      <el-alert title="左侧框图 JSON 可编辑；生成流程会输出信号接口、CBB 分类、并行器件产物、整版原理图 JSON 和可打开的工程 URL。" type="info" show-icon />
      <el-row :gutter="16" style="margin-top:16px">
        <el-col :span="10"><el-input v-model="source" type="textarea" :rows="25" /></el-col>
        <el-col :span="14">
          <div class="canvas">
            <svg width="100%" height="540" viewBox="0 0 900 540">
              <line v-for="(w,i) in rendered.wires||[]" :key="w.id||i" :x1="point(w.source).x" :y1="point(w.source).y" :x2="point(w.target).x" :y2="point(w.target).y" stroke="#409eff" stroke-width="2" />
              <g v-for="c in rendered.components||[]" :key="c.id">
                <rect :x="pos(c).x" :y="pos(c).y" width="150" height="90" rx="8" fill="#fff" stroke="#303133" />
                <text :x="pos(c).x+75" :y="pos(c).y+34" text-anchor="middle" font-size="16">{{c.id}} · {{c.name}}</text>
                <text :x="pos(c).x+75" :y="pos(c).y+62" text-anchor="middle" font-size="12" fill="#909399">{{c.library_type}}</text>
              </g>
            </svg>
          </div>
        </el-col>
      </el-row>
    </el-card>
    <el-card v-if="result" shadow="never" style="margin-top:16px">
      <template #header><b>生成结果</b></template>
      <el-result :icon="result.judge.passed?'success':'warning'" :title="`专项 Judge：${result.judge.score} 分`" :sub-title="result.judge.errors.join('；')||'JSON 拓扑与过程产物完全符合要求'" />
      <el-link type="primary" :href="result.project_url">打开实际原理图工程：{{result.project_url}}</el-link>
      <el-descriptions border :column="5" style="margin-top:12px"><el-descriptions-item v-for="(v,k) in result.judge.dimensions" :key="k" :label="k">{{v}}</el-descriptions-item></el-descriptions>
    </el-card>
  </div>
</template>
<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchSchematicExample, generateSchematic, fetchSchematicProject } from '../api'
const route=useRoute(); const source=ref('{}'); const result=ref(null); const generating=ref(false)
const parsed=computed(()=>{try{return JSON.parse(source.value)}catch{return {components:[],connections:[]}}})
const rendered=computed(()=>result.value?.schematic||{components:parsed.value.components||[],wires:parsed.value.connections||[]})
function pos(c){if(c.position)return c.position;const i=(rendered.value.components||[]).findIndex(x=>x.id===c.id);return{x:50+(i%3)*280,y:50+Math.floor(i/3)*220}}
function point(ep){const c=(rendered.value.components||[]).find(x=>x.id===ep?.component);const p=c?pos(c):{x:0,y:0};return{x:p.x+75,y:p.y+45}}
async function loadExample(){const data=await fetchSchematicExample();source.value=JSON.stringify(data,null,2);result.value=null}
function updateSource(mutator){const data=JSON.parse(source.value);mutator(data);source.value=JSON.stringify(data,null,2);result.value=null}
async function addComponent(){
  try{const {value}=await ElMessageBox.prompt('格式：ID,名称,类型,public|private，例如 U4,ADC,adc,private','添加器件')
    const [id,name,type,library_type]=value.split(',').map(x=>x.trim());if(!id||!name||!type||!['public','private'].includes(library_type))throw new Error('格式不正确')
    updateSource(d=>d.components.push({id,name,type,library_type,pins:[{id:'P1',name:'P1',direction:'bidirectional'}]}))
  }catch(e){if(e!=='cancel'&&e!=='close')ElMessage.warning(e.message||'已取消')}
}
async function addConnection(){
  try{const {value}=await ElMessageBox.prompt('格式：U1.SDA,U2.SDA,I2C_SDA','添加连线')
    const [a,b,net]=value.split(',').map(x=>x.trim());const [ac,ap]=a.split('.');const [bc,bp]=b.split('.');if(!ac||!ap||!bc||!bp||!net)throw new Error('格式不正确')
    updateSource(d=>d.connections.push({id:`W${d.connections.length+1}`,source:{component:ac,pin:ap},target:{component:bc,pin:bp},net}))
  }catch(e){if(e!=='cancel'&&e!=='close')ElMessage.warning(e.message||'已取消')}
}
async function generate(){try{generating.value=true;result.value=await generateSchematic(JSON.parse(source.value));history.replaceState({},'',result.value.project_url);ElMessage.success('原理图生成完成')}catch(e){ElMessage.error(e.response?.data?.detail||e.message)}finally{generating.value=false}}
onMounted(async()=>{if(route.query.project){result.value=await fetchSchematicProject(route.query.project);source.value=JSON.stringify(result.value.input,null,2)}else await loadExample()})
watch(()=>route.query.project,async id=>{if(id){result.value=await fetchSchematicProject(id)}})
</script>
<style scoped>.row{display:flex;justify-content:space-between;align-items:center}.canvas{background:#eef1f6;border:1px solid #dcdfe6;border-radius:6px;overflow:auto}</style>
