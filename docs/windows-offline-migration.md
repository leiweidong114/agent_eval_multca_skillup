# Windows 离线环境迁移

本项目把经常变化的业务源码保存在 Git 仓库，把低频变化的 Windows 工具链和离线依赖放在单独的 Release 压缩包中。安装脚本不访问网络，不校验 manifest 或 SHA256，也不进行 Release 版本匹配。

## Release 解压目录

Release 完整 ZIP 解压后应至少包含：

```text
release-root/
├─ toolchains/
│  ├─ go1.26.7.windows-amd64.zip
│  ├─ node-v26.1.0-win-x64.zip
│  ├─ cpython-3.12.14-*-install_only_stripped.tar.gz
│  └─ VC_redist.x64.exe
├─ python/wheelhouse/*.whl
├─ frontend/npm-cache/
├─ sources/
│  ├─ skill-up/                 # 必须包含 vendor/
│  └─ multica/                  # server/ 必须包含 vendor/
├─ prebuilt/
│  ├─ skill-up.exe
│  └─ multica-eval-runtime.exe
├─ justdo/JustDo Setup 2026.8.27.exe
└─ question-bank/maeval-public.db
```

Python wheelhouse 必须包含项目及构建所需的全部直接、间接依赖。npm cache 必须在 Windows x64 上根据仓库中的 `frontend/package-lock.json` 准备。公开 Release 只允许包含清理后的公开题库，不能放入当前运行数据库、账号、密码、密钥或历史评测记录。

## 新电脑安装

```powershell
git clone <repository-url> D:\workspace\agent_eval_multca_skillup
Set-Location D:\workspace\agent_eval_multca_skillup

.\install_windows.ps1 -ReleaseRoot D:\Downloads\agent-eval-runtime-windows-x64
```

Python 默认直接从 portable `install_only` 包解压到项目，不调用系统安装器。VC Runtime 安装包会随 Release 提供，但默认不运行；只有确实缺少系统运行库时才加 `-InstallVCRuntime`。JustDo 默认使用 Electron/NSIS 的静默参数安装；如果目标安装包需要用户交互，可加 `-InteractiveJustDo`。不需要安装 JustDo 时使用 `-SkipJustDo`。

安装脚本把后续运行和编译需要的文件复制到项目的：

- `backend/.runtime/windows`
- `backend/.tools/windows`
- `backend/.offline-cache/windows`

安装后不再依赖原 Release 解压目录。已有 `.env`、题库和第三方源码默认保留；`-Force` 只用于重建工具链和 Python 环境。

安装完成后编辑根目录 `.env`，再启动：

```powershell
.\start.ps1
.\stop.ps1
.\restart.ps1
```

## 修改代码后重建

```powershell
.\rebuild.ps1 -Target Backend
.\rebuild.ps1 -Target Frontend
.\rebuild.ps1 -Target SkillUp
.\rebuild.ps1 -Target Multica
.\rebuild.ps1 -Target All
.\rebuild.ps1 -Target Frontend -Restart
```

普通 Python 源码以 editable 模式安装，通常只需重启。修改 `pyproject.toml` 后，离线 wheelhouse 必须已经包含新增依赖。修改 `package.json` 或 `package-lock.json` 后，npm 离线缓存也必须包含新依赖，否则应在有网络的制作电脑上更新离线包。

Skill-Up 源码位于 `backend/.runtime/windows/src/skill-up`，Multica 源码位于 `backend/.runtime/windows/src/multica`。两者均用项目内 Go 和 `vendor` 离线编译。重新执行安装时不会覆盖已有源码，避免本机修改丢失。

Multica 评测入口的项目定制代码保存在 `backend/runtime/multica-local-runner`；构建 Multica 时会将这里的 `main.go` 和 `main_test.go` 覆盖到 Multica 源码的对应命令目录。因此需要长期保存的运行器修改应修改项目中的这两个文件并提交 Git。

## 环境检查

```powershell
.\doctor.ps1
.\doctor.ps1 -RequireConfiguration
```

第一个命令检查本地工具链和运行器，第二个还检查 `.env` 中的 LiteLLM 和数据库必要配置。

## 在制作电脑组装 Release

仓库提供 `prepare_windows_release.ps1`，它只整理本地已经存在的文件，不负责下载工具链或 Python/npm 包，也不生成版本清单和校验文件。示例：

```powershell
.\prepare_windows_release.ps1 `
  -OutputRoot D:\release-work\agent-eval-runtime-windows-x64 `
  -GoArchive D:\packages\go1.26.7.windows-amd64.zip `
  -NodeArchive D:\packages\node-v26.1.0-win-x64.zip `
  -PythonPackage D:\packages\cpython-3.12.14-install_only_stripped.tar.gz `
  -VCRedist D:\packages\VC_redist.x64.exe `
  -Wheelhouse D:\packages\wheelhouse `
  -NpmCache D:\packages\npm-cache `
  -QuestionBank D:\packages\maeval-public.db `
  -ZipPath D:\release-work\agent-eval-runtime-windows-x64.zip
```

脚本默认从当前项目运行时复制 Skill-Up/Multica 源码和预编译文件，并从指定的 JustDo `release` 目录复制安装包。默认在复制后的 Go 源码中执行 `go mod vendor`；如果输入源码已经带有完整 `vendor`，可使用 `-SkipVendor`。
