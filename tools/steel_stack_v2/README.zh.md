# 十层 Steel Module v2：本地工程验收与待运行清单

本工具将四种 SiPM 布局和安装间隙作为独立配置。当前候选间隙为
0.5、1.0 mm；尚未选择正式间隙。工程验收数据不能并入科学扫描统计。

固定 Geant4 11.4.2、Serial，保留旧 steel 宏的材料、光学开关与
opticalphoton 的 Scintillation。Cerenkov、Rayleigh、MieHG、OpWLS 仍遵循
旧宏的关闭设置；不额外禁用任何过程。所有 SiPM 面进入均按旧模型计数。

**2026-09-30 已验收：`painted-corner-v2`，固定 16τ。** 完整射线晋级和
130 事件工程矩阵均通过；本地 Geant4 11.4.2／ARM64／Serial 可使用此数值
基线准备后续扫描。身份记录为 `accepted-numerical-baseline.json`，完整报告为
`docs/decisions/steel-module-stack-v2-full-validation.md`。

保留 `legacy` 默认值用于历史复查，已验收的旧默认二进制没有被替换。
新扫描须显式选 `--optical-numerics painted-corner-v2 --optical-corner-scale 16`，
并使用本批冻结的可执行文件：
`outputs/steel_stack_v2/20260930-full-corner-acceptance-v2-s16-r2/candidate/build/OpNovice2`。
该文件是 Linux ARM64 程序，放在已验收 Docker 中运行；不能直接作为 Mac
程序或上传 OSC 执行。`run.py` 的旧默认准备流程仍生成 legacy 配置，不能把
默认清单误当成新基线。新基线的 8/48 配置清单已保存在本批
`pending-scans/`，均明确携带 v2/16τ 身份，没有分配科学事件或预算。

0.5／1.0 mm 间隙仍待敏感性评估；工程通过不提供间隙选择或布局排序。
原 `painted-corner-v1` 历史失败记录继续保留，说明如下。

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
painted-corner-v2 的 16τ 已通过完整本地工程验收。stack-v1 拒绝这些 v2 参数。配置哈希、宏、运行状态、
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

该批次预期返回未通过。原始失败及三个候选尺度的结果均保留；该历史 v1 批次不被覆盖；后续 v2 已用独立批次覆盖“未正常返回 tile”的路径，
见上方已验收状态。

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

OSC 的清单生成可显式指定 `--optical-numerics painted-corner-v2
--optical-corner-scale 16`；资源测试使用 `--purpose benchmark`，与科学样本的
任务阶段和种子登记分开。worker 分别记录模拟启动器耗时、子进程 CPU 时间和
峰值 RSS，运行结束仍须审计，不能把 exit 0 自动当成科学验收。

`remote_build.py` 在独立 OSC 目录冻结干净 checkout、验证全部数据文件，
复制并固定现有 SIF，从对应 x86_64 容器独立编译。它不运行事件或提交任务。
普通 array 仍须显式给事件数、种子块、事件预算、账户、时限、内存和并发。

每批记录实际源码字节、dirty 状态、构建、二进制、镜像、数据集、依赖、
配置、种子、宏、日志、ROOT、实际物理状态、逐项审计及分析结果。
运行使用冻结副本，`UPDATE_LATEST=0`，保留历史结果和默认二进制。

`osc.py` 只做校验、脚本生成与替身测试，不连接远端或自动调用 sbatch。
OSC 须使用对应架构独立构建并冻结的程序，不能直接使用本地 ARM64 程序。
2026-09-30 已完成远端 x86_64 数值资格验证及 400 事件资源 benchmark，
见 `docs/decisions/steel-module-stack-v2-osc-benchmark.md`；科学扫描尚未开始。

## OSC 工程 benchmark 的结果审计

远端程序先通过 `remote_qualification.py` 的 x86_64 数值验收，再运行
`osc.py render --purpose benchmark` 生成的小规模任务。每项使用独立种子，
工程事件不并入科学比较。提交回执须保存实际作业号、任务清单与脚本的校验值。

`benchmark_audit.py --bundle <清单目录> --output-root <结果目录>
--manifest-sha256 <提交回执中的校验值> --report <新报告路径>`
使用分析虚拟环境运行。它逐项检查输出完整性、运行身份、ROOT 光子账本和
数值修复记录，再汇总每事件耗时与子进程峰值内存；缺失或重复 attempt
均会拒绝。还须核对调度器的最终退出状态和整项任务内存。

报告中的一小时任务容量使用最慢观测速度、两倍耗时余量和 5 分钟启动余量。
短样本可能漏掉耗时很长的中子簇射，该容量只是资源建议，不能用来确定科学
统计量、选择间隙或宣称两配置等价。失败报告独立保存，不改写模拟结果。

Pitzer 的单核内存限额随节点类型不同：40 核节点约 4556 MiB／核，48 核
节点约 3797 MiB／核。请求 `--memory-gib 4` 时应明确使用
`--node-constraint 40core`，否则调度器可能增加 CPU 配额，触发本工具的
单核检查。该选项会写入清单和调度脚本；不会修改模拟线程、种子或物理过程。
参见 [OSC 的 Pitzer 资源说明](https://www.osc.edu/resources/technical_support/supercomputers/pitzer/guidance_on_requesting_resources_on_pitzer)。

## 统计精度校准：8,000 事件与正式样本量建议

`precision.py prepare` 复用已通过远端数值资格验证及 benchmark 的冻结程序，
准备 8 配置 × 10 个独立任务 × 100 事件。它要求源清单、构建回执、benchmark
清单与审计、数值资格报告、分析解释器及账户；不提交任何任务。
校准主种子 2026093002，分析种子 2026093002，正式建议主种子 2026093003。
完整固定规格保存在 `statistics-spec.json`，控制工具另存冻结副本。

校准入口使用 `osc.py --purpose calibration`，须经 `precision.py prepare`
绑定逐任务审计与停止标记。每项 1 CPU／4 GiB／40core／1 小时，最大并发 4。
账本或数值检查失败会创建 `STOP.json`，后续任务拒绝启动；还核对本 array
此前的调度失败，涵盖无法写停止标记的节点失败和 OOM。失败记录不能删除来继续运行。

全部 80 项完成后，以冻结解释器执行 `precision.py estimate --batch-dir <批次>
--manifest-sha256 <准备回执校验值> --output-dir <新输出目录>`。
准备阶段生成的 `estimate.sbatch` 可由操作者附加校准作业依赖提交；它只分析已完成数据。
估计器重新核对每项审计、文件校验和 Slurm 完成状态。输出各配置事件分布、尾部贡献、
四组间隙对比、bootstrap 记录及正式建议清单。

主指标为全模块收光／入射中子，包含零响应；每个任务块内重采完整事件 10,000 次。
四组比较都以 0.5 mm 为参照，分别给逐项 95% 与校正后的 98.75% 区间，
后者对应名义整体 95%。目标区间半宽 5 个百分点；样本量取方差系数点估计与
bootstrap 90% 分位中的较大值，加 20% 余量，按每任务 100 事件向上取整。
校准批及 benchmark 不进入未来正式主估计，不能拿校准区间冒充正式精度验收。

`formal-plan/` 只有冻结的配置、事件数、种子与资源建议，没有提交行为。
若分母或方差退化，生成诊断与 `rejected.json`，不生成正式清单。
若实测耗时无法保留既定一小时余量，建议清单标记 `executable=false`，不会自动换资源。
正式任务完成后仍须检查实际区间宽度；本规划不承诺精度达标、不判定等价、不选择间隙。
