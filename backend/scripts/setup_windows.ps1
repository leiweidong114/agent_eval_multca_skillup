# ============================================================================
# setup_windows.ps1 —— Windows 环境一键搭建脚本
# 作用：下载/校验 Go、Multica 源码与评测运行器、Skill-Up，创建 Python 虚拟环境，
#       安装项目 Python 依赖，运行测试并做连通性自检，输出 WINDOWS_SETUP_OK。
# 用法：.\backend\scripts\setup_windows.ps1 [-SkipTests]
#       -SkipTests：跳过 Go 运行器单测与 Python pytest（仅构建，不跑测试）。
# ============================================================================

# 声明脚本参数：-SkipTests 为"开关型"参数，调用时写 -SkipTests 即视为 $true。
param([switch]$SkipTests)

# 让脚本在任何命令出错时立即终止，避免带着错误继续执行造成更难排查的问题。
$ErrorActionPreference = "Stop"

# $PSScriptRoot = 本脚本所在目录(backend\scripts)；先取它的父级再解析成绝对路径，
# 得到 backend 目录；$projectRoot 指向 backend。
$projectRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path

# 把后续所有相对操作的工作目录切到 backend（含 runtime、pyproject 的根）。
Set-Location $projectRoot

# 运行时根目录：backend\.runtime\windows（所有下载的二进制/虚拟环境都放这里，
# 不写入系统目录，也方便整目录迁移）。
$runtime = Join-Path $projectRoot ".runtime\windows"

# binDir：放置编译产物(如 multica-eval-runtime.exe)的目录。
$binDir = Join-Path $runtime "bin"

# goHome：独立 Go 工具链的安装根目录。
$goHome = Join-Path $runtime "go"

# go：独立安装的 go.exe 完整路径。
$go = Join-Path $goHome "bin\go.exe"

# pythonEnv：项目专属 Python 虚拟环境根目录(backend\.runtime\windows\python)。
$pythonEnv = Join-Path $runtime "python"

# python：该虚拟环境内的 python.exe。
$python = Join-Path $pythonEnv "Scripts\python.exe"

# multicaSource：Multica 源码检出目录(backend\.runtime\windows\src\multica)。
$multicaSource = Join-Path $runtime "src\multica"

# multicaCommit：项目钉死的 Multica 源码 commit，防止上游变动影响可复现性。
$multicaCommit = "c1a61e1e863eb62ddd7b5fd5ab5ff85391f212fd"

# 确保 runtime 与 binDir 目录存在(-Force：已存在也不报错)。
New-Item -ItemType Directory -Force -Path $runtime, $binDir | Out-Null

# 若独立 Go 工具链尚未安装，则下载并校验。
if (-not (Test-Path -LiteralPath $go)) {
    # 钉死的 Go 版本号。
    $goVersion = "1.26.7"

    # 根据 CPU 架构选择 amd64 或 arm64 的发行包。
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }

    # 各架构 Go 压缩包的官方 SHA256，用于下载后完整性校验。
    $checksums = @{
        amd64 = "f4f534a486e4bc3387fa18f08208f2f854b7aaea8a08f2a2d829a914a05abb11"
        arm64 = "6f1b08de9e2dd94f69c52e524ab6834737275253291e8fd7f1c12ed4eceeda89"
    }

    # Go 发行压缩包文件名，例如 go1.26.7.windows-amd64.zip。
    $archiveName = "go$goVersion.windows-$architecture.zip"

    # 压缩包下载落盘路径(runtime 下)。
    $archive = Join-Path $runtime $archiveName

    # 若压缩包尚未下载，则从 go.dev 官方源下载。
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -Uri "https://go.dev/dl/$archiveName" -OutFile $archive
    }

    # 计算已下载文件的实际 SHA256(小写)，用于与官方校验值对比。
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()

    # 校验值不匹配直接抛错，防止被篡改或下载不完整的包。
    if ($actual -ne $checksums[$architecture]) { throw "Go checksum mismatch: $actual" }

    # 校验通过后解压到 runtime 目录(go\ 结构随之生成)。
    Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
}

# 若 Multica 源码尚未检出，则浅克隆钉死分支 v0.4.36。
if (-not (Test-Path -LiteralPath $multicaSource)) {
    # 先创建源码父目录 src\。
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $multicaSource) | Out-Null

    # 浅克隆(--depth 1)指定标签 v0.4.36 到 src\multica。
    git clone --depth 1 --branch v0.4.36 https://github.com/multica-ai/multica.git $multicaSource

    # git 退出码非 0 表示克隆失败，直接报错。
    if ($LASTEXITCODE -ne 0) { throw "Failed to download Multica source" }
}

# 读取实际检出的 commit 值(去首尾空白)。
$actualCommit = (git -C $multicaSource rev-parse HEAD).Trim()

# 实际 commit 必须与钉死的 multicaCommit 完全一致，否则中止(保证可复现构建)。
if ($actualCommit -ne $multicaCommit) {
    throw "Unexpected Multica source commit: $actualCommit"
}

# 定位 Multica 内评测运行器的主程序目录。
$commandDir = Join-Path $multicaSource "server\cmd\multica-eval-runtime"

# 确保上述 Go 命令目录存在(拷贝源文件前)。
New-Item -ItemType Directory -Force -Path $commandDir | Out-Null

# 把项目自带的 Windows 本地运行器 main.go 与单测拷贝进 Multica 命令目录，
# 替换其默认实现(运行时路径引用等 Windows 适配)。
Copy-Item runtime\multica-local-runner\main.go,runtime\multica-local-runner\main_test.go -Destination $commandDir -Force

# 进入 Multica server 目录构建(记住当前目录，便于最后恢复)。
Push-Location (Join-Path $multicaSource "server")
try {
    # 用独立 Go 编译 multica-eval-runtime.exe 到 binDir(-trimpath 去掉本地路径信息)。
    & $go build -trimpath -o (Join-Path $binDir "multica-eval-runtime.exe") .\cmd\multica-eval-runtime

    # 编译失败则抛出。
    if ($LASTEXITCODE -ne 0) { throw "Multica local runtime build failed" }

    # 未显式跳过测试时，运行该运行器的 Go 单元测试。
    if (-not $SkipTests) {
        & $go test .\cmd\multica-eval-runtime

        # 测试失败则抛出。
        if ($LASTEXITCODE -ne 0) { throw "Multica local runtime tests failed" }
    }
} finally {
    # 无论成功/异常，都恢复到之前的目录。
    Pop-Location
}

# 始终验证钉死的 Skill-Up 源码并重新应用 Windows custom-engine 补丁
#(install_skillup_windows.ps1 负责下载 Skill-Up 并打补丁)。
& .\scripts\install_skillup_windows.ps1

# 定位系统里可用的引导 Python(用于创建虚拟环境；找不到则报错)。
$bootstrapPython = Get-Command python -ErrorAction Stop

# 若项目虚拟环境尚未创建，则用系统 Python 创建(--copies：以复制方式而非符号链接，跨目录更稳)。
if (-not (Test-Path -LiteralPath $python)) {
    & $bootstrapPython.Source -m venv --copies $pythonEnv
}

# 在虚拟环境中以可编辑模式安装项目自身依赖(dev/web/database 三组 extras)。
& $python -m pip install -e ".[dev,web,database]"

# 未显式跳过测试时，运行整个 Python 测试套件(pytest)。
if (-not $SkipTests) {
    & $python -m pytest

    # pytest 失败则抛出。
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed" }
}

# 运行 agent_eval CLI 的自检命令(校验安装/环境配置正确)。
& $python -m agent_eval.cli doctor

# 打印成功标记，供脚本/CI 判断搭建完成。
Write-Host "WINDOWS_SETUP_OK"
