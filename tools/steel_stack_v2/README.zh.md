# 十层 Steel Module v2：本地工程验收与待运行清单

本工具将四种 SiPM 布局和安装间隙作为独立配置。当前候选间隙为
0.5、1.0 mm；尚未选择正式间隙。工程验收数据不能并入科学扫描统计。

固定 Geant4 11.4.2、Serial，保留旧 steel 宏的材料、光学开关与
opticalphoton 的 Scintillation。Cerenkov、Rayleigh、MieHG、OpWLS 仍遵循
旧宏的关闭设置；不额外禁用任何过程。所有 SiPM 面进入均按旧模型计数。

**2026-09-30 新进展：`painted-corner-v2` 已通过完整射线与尺度晋级测试，选择
16τ 进入完整工程验收；尚未声明完整验收通过。** 见
`docs/decisions/steel-module-stack-v2-immediate-validation.md`。原 legacy 与 v1
入口继续保留，v1 的历史失败说明如下。

**此前 `painted-corner-v1` 状态：棱边候选未通过晋级。**
完整 10,560 个单光子事件和 8 个中子重放已完成。三个尺度都修复了原失败
中子路径，但各有相同的 608 个靶向射线仍触发 `NoRINDEX`；其中包括反射后
没有先返回 tile、直接沿 World 传播的路径。已停止后续 130 事件工程验收矩阵。
详见 `outputs/steel_stack_v2/20260930T042800Z-corner-recovery-r2/ACCEPTANCE.zh.md`。

## 模型与数据

- `back-four`：每层底面四个象限中心；`back-two`：两个对角象限中心。
- `edge-two`：同一 +X 面的两个原位置；`back-center`：底面中央。
- 十层之间仅有九个安装间隙；SiPM 正面贴着 tile。末层背部 SiPM 伸入
  World，后面没有第十块下游钢板或第十个间隙。
- 四布局分别有 40、20、20、10 个有效传感器。v2 编号为
  `4*layer+local_sensor`，无效槽不作为零响应传感器参与分析。
- 旧 `scan` 和 summary 字段含义保留。新 ROOT 表为 `stack_event_v2`、
  `stack_layer_v2`、`stack_sensor_v2`、`stack_photon_flow_v2`。
- `births_nonoptical_parent` 与 `births_optical_parent` 分开；旧 G 没有包含
  所有再闪烁后代，不能拿旧 G 与所有光子轨迹强行闭合。
- 根祖先来源与实际出生来源分开。新的出生口径 collection 使用 tile 内
  出生的光子轨迹及这些轨迹自身的收集数，旧 collection 仅作兼容指标。
- 零产光中子事件合法；零分母显示无效，不写成零效率。

## 直接扫描入口

在已加载 Geant4 11.4.2 的 Serial 环境中，v2 的三个参数必须明确指定：

```bash
./run_sipm_cavity_scan.sh full custom \
  --study-preset steel-module-stack-v2 \
  --sipm-layout back-two --tile-thickness-mm 4 --readout-gap-mm 0.5 \
  --x-min 0 --x-max 0 --y-min 0 --y-max 0 --step 1 --grid-unit mm \
  --events 1 --seed1 17001 --seed2 17002 --no-root-plots --dry-run
```

以上仅生成宏。科学任务应通过冻结批次运行，不能依赖可变工作目录或
环境中的旧二进制。`--stack-photon-accounting on|off` 默认 on；off 只用于
同程序、同种子的无扰动验收，不关闭物理过程。

数值接口 `--optical-numerics legacy|painted-corner-v1|painted-corner-v2` 默认 `legacy`。
候选还要求显式 `--optical-corner-scale 16|32|64`，这些尺度仅用于工程诊断，
painted-corner-v2 的 16τ 已晋级，完整验收仍待通过。stack-v1 拒绝这些 v2 参数。配置哈希、宏、运行状态、
本地及未来 OSC 参数均携带数值身份；分析拒绝混合不同数值身份。

Geant4 初始化前命令为 `/opnovice2/numerics/mode` 和
`/opnovice2/numerics/cornerScale`。`/opnovice2/numerics/probeFile` 仅用于专用
光学诊断输入，生产中子审计明确拒绝该入口。每次运行的
`*.optical-numerics.jsonl` 单独保存修正、原始状态与终态；中子运行不保存
每条光子的全轨迹，单光子诊断保存有效边界序列与逐事件 RNG 状态。

`corner_recovery.py prepare|run|audit` 针对本次冻结失败案例管理有限诊断
矩阵。`run` 使用冻结源码内的工具及程序，只读复用校验一致的完成任务；
失败记录不覆盖，失败后不会自动重跑或补量。`audit` 只读结果，只有
全部晋级条件通过才报告可进入完整工程验收；不会自动提交或运行科学任务。

已归档批次可以直接重新审计，无须运行事件：

```bash
batch=outputs/steel_stack_v2/20260930T042800Z-corner-recovery-r2
STEEL_STACK_V2_REPOSITORY_ROOT="$PWD" PYTHONDONTWRITEBYTECODE=1 \
  test/OpNovice2/.venv-analysis/bin/python \
  "$batch/candidate/source/tools/steel_stack_v2/corner_recovery.py" audit \
  --batch-dir "$batch"
```

该批次预期返回未通过。原始失败及三个候选尺度的结果均保留；继续修复需要
先为“未正常返回 tile”的路径另定处理规则，本轮未扩大触发条件。

## 工程验收流程

从仓库根目录调用固定的分析虚拟环境。以下 `$batch` 是一个新批次目录；
**参考快照必须在修改待验收源码前建立**。本次实现已有保留的升级前快照，
不需要重新运行已完成且校验通过的参考任务。

```bash
batch=outputs/steel_stack_v2/<新批次>
py=test/OpNovice2/.venv-analysis/bin/python

"$py" tools/steel_stack_v2/run.py snapshot --batch-dir "$batch" --role reference
"$py" tools/steel_stack_v2/run.py build --batch-dir "$batch" --role reference
"$py" tools/steel_stack_v2/run.py prepare --batch-dir "$batch" --matrix acceptance

# 实现及静态测试完成后冻结候选；不覆盖参考或已验收默认程序。
"$py" tools/steel_stack_v2/run.py snapshot --batch-dir "$batch" --role candidate
"$py" tools/steel_stack_v2/run.py build --batch-dir "$batch" --role candidate
"$py" tools/steel_stack_v2/run.py run-local --batch-dir "$batch"
"$py" tools/steel_stack_v2/run.py check --batch-dir "$batch"
```

验收清单包含 89 次运行、130 个事件：旧行为回归 16、48 配置冒烟 48、
观察器 off/on 64、重复运行 2。任何硬检查失败即停。每次运行在容器中
执行 15 分钟超时保护，超时不会自行扩大事件数或时间。

再次运行会验证并跳过已接受结果。失败任务必须先检查原因，再显式使用
`--retry-failed`；每次尝试保留独立目录。若只有审计器问题且原模拟已完整
结束，可使用 `audit-existing --task-id ...` 重审原数据，不重跑事件，
旧 receipt 保留。`--reaudit-completed` 用于显式更新已完成任务的审计契约。

参考与候选必须比较旧 ROOT 逐事件字段、直方图、summary 和 RNG 末态。
ROOT 文件时间戳等序列化元数据不作字节比较。Serial 模式唯一允许的既有
提示是准确匹配的 `Analysis_W001`／`SetNtupleMergingMode` 合并设置被忽略；
其他异常不因此被放行。

## 科学清单与分析

`model.make_matrix('sensitivity', [0.5, 1.0])` 返回 8 个初筛配置：
4/24 mm × back-center/edge-two × 两种间隙。
`model.make_matrix('full', [0.5, 1.0])` 返回 48 个完整候选。
这些配置清单没有事件数、种子或提交行为。

生成可运行科学任务时，`run.py prepare --matrix sensitivity|full` 必须同时
提供 `--events-per-task`、`--blocks`、`--campaign-seed`、
`--total-event-budget`。默认只准备；`run-local` 不会执行科学清单，除非另行
显式传入 `--allow-science`。本阶段没有执行该路径。

分析必须使用冻结候选目录内的 `analyze.py`，并指定本批次。它核对已通过的
审计和数据校验值，按完整中子事件进行块内 bootstrap。工程数据需要显式
`--engineering`，所有图表都标记为工程验收，不给间隙排序或等价结论。
最终间隙、最小有意义差异和正式统计预算仍需后续确定。

## 归档与 OSC 边界

每批记录实际源码字节、dirty 状态、构建、二进制、镜像、数据集、依赖、
配置、种子、宏、日志、ROOT、实际物理状态、逐项审计及分析结果。
运行使用冻结副本，`UPDATE_LATEST=0`，保留历史结果和默认二进制。

OSC 适配仅做校验、脚本生成与替身测试；工具不连接远端或自动调用 sbatch。
未来必须在 OSC 对应架构构建并冻结程序，不能上传本地 ARM64 程序作为
远端可执行文件。当前工程验收不代表 OSC 执行已验证。
