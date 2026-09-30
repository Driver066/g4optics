# 本地 Geant4 环境

日常环境由 `g4env.sh` 管理：Geant4 在 Docker 中运行；ROOT 和 Python 分析使用 Mac 上已有的原生 ARM64 环境。配置及工具放在仓库内，主机启动脚本仅转发调用。

## 日常使用

从仓库根目录运行：

```sh
# 启动已验收的默认容器；可重复执行，不删除容器。
tools/local/g4env.sh start

# 进入默认环境。
tools/local/g4env.sh shell

# 打开 W08 dimple 几何，默认用已验证的 TSGQtZB。
tools/local/g4env.sh gui

# 转发原有扫描参数；模型/正式扫描范围由各次任务决定。
tools/local/g4env.sh scan -- --help

# 查看选择的环境和安装身份。
tools/local/g4env.sh status

# 进入保留的历史容器。
tools/local/g4env.sh shell --version 11.3.2
```

原来的 `~/geant4-docker/bin/g4dev.sh`、`g4cli.sh`、`g4gui.sh` 以及 `~/geant4-docker/g4docker.sh` 分别转发到启动、shell、GUI、shell。`g4gui.sh` 默认直接显示几何；需要自定义程序时用 `--` 分隔管理器参数和程序参数。

新容器名为 `g4dev-1142`。原始 `g4dev` 名称仍属于 11.3.2；直接执行 `docker exec g4dev ...` 不会进入新版。旧 `build` 和 `build-gui` 不会用于新版构建；旧版本若需要重新开发编译，使用独立的 `build-linux-arm64-g4-11.3.2-work`。

## 图形与分析

`TSGQtZB` 使用 CPU 绘图，保留 Qt 几何交互。此次 XQuartz 环境下 `OGLSQt` 创建 OpenGL 上下文失败，因此它不是默认查看器；这不会影响光学模拟。批处理无需启动 XQuartz。

ROOT 入口为 `/opt/homebrew/bin/root`，已验证版本 6.38.04。Python 使用 `test/OpNovice2/.venv-analysis/bin/python`；完整版本清单在 `requirements-analysis.lock.txt`，包括 uproot。不要把 Python 的软链接先解析为系统解释器，否则可能绕过虚拟环境。

```sh
test/OpNovice2/.venv-analysis/bin/python your_analysis.py
```

## 安装、复验与回退

`setup` 安装固定基底的项目派生镜像（补齐扫描宏需要的 Python）、官方12项数据集并重新编译项目。它不自动启动正式模拟或切换默认环境。安装批次路径必须位于本仓库 `outputs/environment/` 下。

```sh
tools/local/g4env.sh setup --batch-dir outputs/environment/MY_INSTALL_BATCH
tools/local/g4env.sh smoke --batch-dir outputs/environment/MY_INSTALL_BATCH
tools/local/g4env.sh check --batch-dir outputs/environment/MY_INSTALL_BATCH --skip-gui
```

`smoke` 运行六个W08检查点共600事件，加一个100事件固定种子重复任务。已有任务不会被覆盖；中断重试保留旧批次，使用新的验收批次。完整验收还需要绑定同一镜像/可执行文件的GUI确认和重启检查记录；`check --activate` 只有全部通过后才安装主机转发入口并切换默认版本。本次安装已由代理完成这些验收。

```sh
tools/local/g4env.sh rollback
```

回退只改变默认选择，保留新旧两套环境和输出。重新启用新版时，对原验收批次执行 `check --activate`；源码、二进制或数据身份改变时必须重新验收。

所有安装/失败尝试、数据校验、实际源码快照、可执行文件身份、宏、种子、ROOT、日志与验收报告都位于 `outputs/environment/<批次>/`。`active.json` 标识当前默认及其验收批次；`image-lock.json` 标识实际派生镜像，`compose.env` 使仓库Compose配置使用同一身份。日常操作使用管理器，不让Compose与管理器并发创建同名容器。

普通扫描仍可更新 `latest`。环境验收和需隔离的检查设置 `UPDATE_LATEST=0`；Docker封装通过 `SCAN_RESULT_POINTER` 获取本次结果，不从历史 `scan_latest` 推测输出。原有环境变量覆盖仍可用于非托管运行，但版本、数据和受保护构建目录的冲突会明确拒绝。
