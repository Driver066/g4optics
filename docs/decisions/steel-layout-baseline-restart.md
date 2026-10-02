# 从 7 月基线重新实现四种十层 SiPM 布局

## 本轮决定

- 唯一源码起点：`a2d05dfe1a6c76df2acd072d7fab8cff366a02f6`。
- 新分支：`codex/steel-layout-baseline-restart`。没有导入后续扩展或修复代码。
- 闪烁体保持一整块；四等份仅用于确定背面 SiPM 的中心位置。
- 四种布局的九个内部 tile→下一块 steel 间隙全部固定为 **0.5 mm**。
- steel→自身 tile 接触无间隙；最后一块 tile 后是 World，没有第十个间隙。
- 首阶段先完成 **4 mm** 的四布局验证；通过后才扩展 8、12、16、20、24 mm。
- 历史原始 ROOT 和运行环境原件保留在 OSC，本轮未访问 OSC。

## 模型与接口

保留基线十块 500×500×40 mm SAE-304 钢板、十块 100×100 mm EJ-200、
2.4×2.4×0.5 mm SiPM 代理，以及原有材料、光学过程、表面和探测定义。
入射为中心点 1 GeV **动能**中子，沿 −Z。没有修改物理列表或光子追踪逻辑。

| 布局 | 每层 SiPM | 局部位置（mm） |
|---|---:|---|
| back-four | 4 | −Z 面：(-25,-25)、(-25,+25)、(+25,-25)、(+25,+25) |
| back-two | 2 | −Z 面：(-25,-25)、(+25,+25) |
| back-center | 1 | −Z 面：(0,0) |
| edge-two | 2 | 同一个 +X 面：(y,z)=(-25,0)、(+25,0) |

新入口为初始化前设置 `/opnovice2/stack/layoutStudy true`。
默认关闭时保留原 stack-v1 的零间隙 side-two。新模式中
`L=10*(40+t)+9*0.5`，4 mm 时 `L=444.5 mm`、源 `z=223.75 mm`。
全局 SiPM 编号是 `4*layer+local_sensor`，未使用编号不代表物理探测器。
取消跨空气间隙的 tile→下游 steel border surface；保留 tile→World、
tile→上游 steel 和 tile→SiPM 的基线处理。

## 输出与审计

原 `scan` 总 G/D 语义不变。G 是基线的闪烁与切伦科夫产生光子口径，
不是完整光子出生/终态账本；D 是进入 SiPM 代理体积的光子数，不含 PDE。
原 `scan` 的四个 sensor 列仍只对应全局编号 0…3，不能相加作为全栈响应。

新模式使用三个独立 ROOT 表：

- `layout_layers`：每事件十层的产生光子、收集光子及钢板/闪烁体能量。
- `layout_sensors`：每个有效 SiPM 的总来源与同层来源计数，包含零计数行。
- `layout_transfers`：非零的光子来源层→SiPM 转移计数。

新模式下旧 `stack_layers/stack_transfers` 为空，防止旧两传感器分析器误用。
审计要求进程正常退出、实际几何和输入一致、无重叠/未知来源/NoRINDEX，
且逐层、逐传感器、转移、事件和汇总文件的计数一致。
零分母事件明确输出 invalid/NaN；不把零响应事件删除。
只接受原始基线同样出现的确切 Serial ntuple-merging 提示。

## 本地运行方式

输入生成器从上述 Git 提交提取原始 runner、宏模板和光学表，使用其 dry-run
生成基线宏；不读取以前的扩展或研究输出。生成后的每个布局必须独立进程运行，
工作目录为相应布局目录，输入 `run.mac`。几何抽样检查后重置指定随机种子。

```sh
python3 tools/layout_study/prepare.py --output-dir outputs/new-t04-check \
  --events 10 --seed1 20261002 --seed2 10401
```

使用已有 Geant4 11.4.2 Docker 镜像、只读数据集和独立 Release 构建。
设置 `G4RUN_MANAGER_TYPE=Serial`；一次进程只执行一次 `beamOn`。
`--events 0` 仅初始化与检查几何，不调用 `beamOn 0`。

```sh
python3 tools/layout_study/audit.py result.root --layout back-four \
  --events 10 --log simulation.log --exit-code 0
```

`--exit-code` 必须来自实际程序退出状态，不能为通过审计而手填零。
几何日志与配置使用 `audit_geometry.py` 核对。分析依赖为 NumPy 和 uproot。

## 本地验证记录（2026-10-02）

记录位于 `outputs/layout-study-t04/`，不与历史科学样本合并。
原始基线和新程序分别构建；镜像、数据集收据、源码、二进制、输入、输出
与进程回执均保存。当前本地运行环境不是历史 OSC 环境的完整复现。

初次背面四 SiPM 初始化中，新增检查把 Geant4 返回的恒等旋转矩阵指针误判为
旋转，进程在零事件阶段退出 134。已依据实际 Geant4 API 改为矩阵值的
`isIdentity()` 检查，保留盒体、母体、包含与重叠验证；失败日志和原二进制保留。
这项改动只修正检查逻辑，没有改变传感器放置或光学物理。

验证按以下顺序完成：

| 阶段 | 配置 | 每配置事件 | 结果 |
|---|---:|---:|---|
| 首阶段：4 mm | 4 | 10 | 几何、ROOT/汇总计数、正常退出全部通过 |
| 后续：8/12/16/20/24 mm | 20 | 5 | 相同检查全部通过 |
| 原 v1 回归 | 原程序与新程序各 1 | 5 | 原 ROOT 物理字段/直方图完全一致，summary 和日志逐字节相同 |

四种布局共验证 **140 个工程事件**；包括初版和最终版本回归在内，实际本地
中子执行总数为 155。首阶段完成后 **C++ 源码与可执行文件完全未变**，后续
只参数化输入生成和审计工具；24 个配置使用同一候选程序。
42 项输入/审计检查通过。所有样本无未知来源检测、无 NoRINDEX，进程均正常退出。
这些有限检查不等于长期稳定性、完整光学账本或物理模型有效性证明。

首阶段原始冻结记录保留在 `outputs/layout-study-t04/`；后续完整矩阵的汇总、
更新后的工具源码快照和文件校验和在 `outputs/layout-study-thickness-smoke/`。
后者的 `verification.json` 同时绑定全部 24 配置的几何与事件审计。
后续厚度输入示例：

```sh
python3 tools/layout_study/prepare.py --output-dir outputs/new-t24-check \
  --tile-thickness-mm 24 --events 5 --seed1 20261026 --seed2 20425
```

事件审计应传入对应的 `--tile-thickness-mm 24`，并与同一日志哈希的几何审计
配对使用。默认参数仍为 4 mm。
小样本运行检查仅证明本次输入下可以构建、运行、正常退出并一致记账，
不构成布局优劣、统计精度或完整物理模型有效性的结论。
