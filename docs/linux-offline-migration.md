# Linux x64 离线环境迁移

Linux 离线包与 Git 源码分离：Git 保存评测系统业务代码，Release 包保存 Go、Node、
portable Python、Python wheelhouse、npm cache、带 vendor 的 Skill-Up/Multica 源码以及
JustDo AppImage。包内不包含 `.env`、密钥、数据库密码或历史评测结果。

在联网制作机生成包：

```bash
sh ./prepare_linux_release.sh \
  /tmp/agent-eval-runtime-linux-x64-portable \
  /tmp/agent-eval-runtime-linux-x64-portable.tar.gz
```

新电脑先取得 `dev` 分支源码，解压 Release 后安装：

```bash
git clone --branch dev https://github.com/leiweidong114/agent_eval_multca_skillup.git
cd agent_eval_multca_skillup
tar -xzf /path/agent-eval-runtime-linux-x64-portable.tar.gz -C /tmp
sh ./install_linux.sh --release-root /tmp/agent-eval-runtime-linux-x64-portable
```

安装过程不访问网络。安装后编辑 `.env`，再执行：

```bash
sh ./start-all.sh
./backend/.runtime/linux/python/bin/agent-eval agents
```

JustDo 启动器位于 `backend/.tools/linux/justdo/JustDo-agent-linux-x64`。需要显式指定时：

```bash
export JUSTDO_AGENT_EXECUTABLE="$PWD/backend/.tools/linux/justdo/JustDo-agent-linux-x64"
```
