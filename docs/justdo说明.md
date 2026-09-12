当前 JustDo 调用已经测试通过。评测系统使用前端保存的：
D:\software\JustDo\JustDo-agent.exe
实际指定模型为 glm-4.5-air，不是 JustDo 里原先配置的默认模型。
已完成的实测
普通 Prompt 测试：
状态：completed
返回：JUSTDO_PROMPT_OK
JustDo 进程退出码：0
实际模型：glm-4.5-air
模型调用次数：1
模型匹配：true
最小 Skill 评测：
状态：completed
with_skill：PASS
without_skill：FAIL（这是基线预期结果）
任务得分：100
总分：87.4
实际模型调用：22 次
实际模型：glm-4.5-air
模型不匹配：0 次
评测结果位于：
[evaluation-report.json](D:/AI_FOR_WORLD/14_AI_workspace/common_tools/agent_eval_multca_skillup/backend/evaluation_results/local/example-marker/20260909-121532__c1593fa34e1b4956b261ced604321feb/evaluation-report.json)
其中 Skill 质量只有 40 分，是因为 example-marker 是极简测试 Skill，缺少工作流、约束、异常处理等章节，不是 JustDo 调用失败。
一、准备命令
打开 PowerShell：
$EvalRoot = "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\agent_eval_multca_skillup"
$EvalCli = "$EvalRoot\backend\.runtime\windows\python\Scripts\agent-eval.exe"
$JustDoExe = "D:\software\JustDo\JustDo.exe"
$JustDoAgent = "D:\software\JustDo\JustDo-agent.exe"

Set-Location $EvalRoot
启动 JustDo：
Start-Process $JustDoExe
启动评测前后端：
.\start-all.ps1
前端：
http://127.0.0.1:5173
后端：
http://127.0.0.1:8000
检查当前保存的 Agent 路径：
Get-Content .\backend\config\agent-paths.json
应该包含：
{
  "justdo": "D:\\software\\JustDo\\JustDo-agent.exe"
}
这个路径一旦从前端保存，前端、后端和未显式传入 --agent-executable 的命令行都会使用它。
二、查看 Agent 和模型
查看所有 Agent：
& $EvalCli agents --all
查看所有模型：
& $EvalCli models --list
筛选模型：
& $EvalCli models --list --prefix glm-4.5-air
重新探测模型可用性会产生真实模型请求：
& $EvalCli models `
  --refresh `
  --prefix glm-4.5-air `
  --workers 1 `
  --timeout 120
三、JustDo 基础诊断
查看启动器版本：
& $JustDoAgent --version
查看 JustDo Agent：
& $JustDoAgent agents list --json
注意：这里显示的版本可能是 JustDo 内置执行引擎的版本，不一定等于安装包版本 2026.8.27。
四、最推荐的连通性测试
& $EvalCli check-agent `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "Reply with exactly JUSTDO_CONNECTIVITY_OK." `
  --timeout 180 `
  --database-verify
成功时重点检查：
status = connected
agent = justdo
executable = D:\software\JustDo\JustDo-agent.exe
response = JUSTDO_CONNECTIVITY_OK
model_verification.verified = true
model_verification.model_matched = true
这是以后每次重新打包、安装后的首选测试，速度和费用都比完整评测低。
五、直接执行 Prompt
& $EvalCli prompt `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "只回复 JUSTDO_PROMPT_OK" `
  --workers 1 `
  --timeout 180 `
  --database-verify
修改 --prompt 即可执行任意任务：
& $EvalCli prompt `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "分析当前目录的项目结构，并给出简要说明。" `
  --workers 1 `
  --timeout 600 `
  --database-verify
六、指定 Skill 进行评测
以下命令已经实测通过：
Set-Location "$EvalRoot\backend"

& $EvalCli run `
  --skill .\skills\example-marker `
  --agent justdo `
  --model glm-4.5-air `
  --case .\skills\example-marker\evals\cases\marker.yaml `
  --parallelism 1 `
  --iterations 1 `
  --timeout 300 `
  --max-turns 4 `
  --benchmark `
  --database-trace `
  --require-model-verification `
  --no-llm-judge
如果临时构造 Prompt 测试某个 Skill：
& $EvalCli run `
  --skill .\skills\example-marker `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "Return the evaluation marker using the installed Skill." `
  --must-contain "MULTICA_SKILL_UP_OK" `
  --parallelism 1 `
  --iterations 1 `
  --timeout 300 `
  --max-turns 4 `
  --benchmark `
  --database-trace `
  --require-model-verification `
  --no-llm-judge
七、完整原理图流水线评测
Set-Location $EvalRoot

& $EvalCli pipeline-eval `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "生成一个包含电源输入、MCU、传感器和通信接口的多页原理图，并输出最终网页 URL。" `
  --workers 1 `
  --parallelism 1 `
  --iterations 1 `
  --timeout 1800 `
  --max-turns 12 `
  --benchmark `
  --database-trace `
  --require-model-verification `
  --llm-judge
快速排查时可以关闭 LLM Judge：
& $EvalCli pipeline-eval `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "生成一个最小电源输入原理图，并输出最终网页 URL。" `
  --workers 1 `
  --parallelism 1 `
  --iterations 1 `
  --timeout 1200 `
  --max-turns 8 `
  --benchmark `
  --database-trace `
  --require-model-verification `
  --no-llm-judge
当前实际调用关系
agent-eval --agent justdo --model glm-4.5-air
    ↓
读取前端保存的 JustDo-agent.exe 路径
    ↓
JustDo-agent.exe agent --local --json ...
    ↓
连接正在运行的 JustDo
    ↓
创建可见的 JustDo 会话
    ↓
临时注入 glm-4.5-air Provider 和会话模型
    ↓
JustDo 的 main Agent 执行任务
    ↓
对话写入 JustDo 会话并返回评测系统
结果里的：
"agent": "justdo",
"agent_backend": "openclaw",
"model": "main",
"provider_model": "glm-4.5-air"
含义是：
- agent=justdo：外部实际调用的是 JustDo。
- agent_backend=openclaw：JustDo 内部使用 OpenClaw 执行引擎。
- model=main：内部 Agent 配置名称，不是大模型名称。
- provider_model=glm-4.5-air：本次实际调用的大模型。
- 数据库验证已经证明真实模型是 glm-4.5-air。
因此不需要把命令改回 --agent openclaw。
是否能在 JustDo 聊天窗口看到
可以。调用会创建 JustDo 可见会话，Prompt 和回答会进入 JustDo 会话记录。
为保证可以看到：
1. 先启动 D:\software\JustDo\JustDo.exe。
2. 再运行 agent-eval。
3. 在 JustDo 会话列表中查看新创建的评测会话。
4. 必要时刷新会话列表。
命令行仍然会等本次调用结束后输出完整 JSON。
修改 JustDo 代码后的流程
开发阶段：可以不重新打包
先切换到符合要求的 Node。JustDo 要求：
Node >= 24.15.0 且 < 25
当前系统默认 Node 26 不能用。当前机器可以临时使用 Codex 附带的 Node 24.19：
$Node24 = "C:\Users\leiwe\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$env:Path = "$Node24;$env:Path"

node --version
npm --version
应看到：
v24.19.0
进入源码：
Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo"
只有第一次、package-lock.json 变化或依赖损坏时才需要：
npm ci
检查代码：
npm run lint
npm run build
npm test
生成开发版评测启动器：
npm run multica:dev-agent
输出路径：
C:\Users\leiwe\AppData\Roaming\JustDo\multica\development\JustDo-agent.exe
启动开发版 JustDo：
npm run electron:dev:openclaw
然后在评测前端将 justdo 路径改成：
C:\Users\leiwe\AppData\Roaming\JustDo\multica\development\JustDo-agent.exe
再运行 check-agent。这种方式适合开发调试，不需要每次打安装包。
正式使用：需要重新打包并安装
如果评测系统继续调用：
D:\software\JustDo\JustDo-agent.exe
那么修改源码后必须重新打包并更新 D:\software\JustDo 中的安装版本。
完整流程：
$Node24 = "C:\Users\leiwe\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$env:Path = "$Node24;$env:Path"

Set-Location "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo"

node --version
npm ci
npm run lint
npm test
npm run dist:win
安装包生成在：
D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo\release\JustDo Setup 2026.8.27.exe
然后：
1. 从 JustDo 托盘菜单彻底退出旧版本。
2. 运行新安装包。
3. 安装或覆盖到 D:\software\JustDo。
4. 启动新的 D:\software\JustDo\JustDo.exe。
5. 前端继续配置 D:\software\JustDo\JustDo-agent.exe。
6. 运行 check-agent 验证。
安装后可以比较文件：
Get-FileHash `
  "D:\AI_FOR_WORLD\14_AI_workspace\common_tools\JustDo\release\win-unpacked\JustDo-agent.exe"

Get-FileHash "D:\software\JustDo\JustDo-agent.exe"
不要只复制 JustDo-agent.exe。它是调用入口，真正修改的 Electron 主程序、网页资源和内置运行时位于 JustDo.exe、resources\app.asar 等文件中。正式更新应运行新安装包，或者整体使用 release\win-unpacked。
简单判断：
- 只修改文档：不需要打包。
- 只修改评测系统：重启评测前后端，不需要打包 JustDo。
- 修改 JustDo 前端、Electron 主进程或评测桥接：开发测试可不打包；正式安装必须重新执行 npm run dist:win 并安装。
- 修改模型配置：通常不需要重新打包。



$env:EvalCli = [Environment]::GetEnvironmentVariable('EvalCli', 'User')

& $env:EvalCli prompt `
  --agent justdo `
  --model glm-4.5-air `
  --prompt "只回复 JUSTDO_PROMPT_OK" `
  --workers 1 `
  --timeout 180 `
  --database-verify