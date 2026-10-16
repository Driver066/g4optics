# Issue #7：两块闪烁体拼接的实现、验证与首次差异比较

日期：2026-10-10（America/New_York）。

这是首次 Side 2 / v2 运行的历史记录。下文的未提交、未发布状态描述该阶段结束时的情况；后续四布局结果、已发布评论和当前接续状态见[阶段归档记录](combined-layouts-dual-readout.md#阶段归档与后续接续2026-10-16)。

本轮实现及 Docker 验证完成。最终结果仅使用 **model-v2 / boundary-entry** 的
1,000 个 beta 源事件。没有设等效阈值，没有扩大样本，也没有提交、推送或关闭
[上游 Issue #7](https://github.com/jdbrice/g4optics/issues/7)。

分支：`codex/combined-tiles-study`，基于
`404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5`。
工作树：`/Users/edison/.codex/worktrees/combined-tiles-study/g4optics`。
截至本阶段运行结束时，代码与本记录尚未提交；旧的 dirty 工作树和历史研究输出保持原样。

## 已确认的研究条件

- 100×100 mm EJ-200；整块 16/24 mm，与 8+8、8+16、16+8 mm 比较。
- 拼接层为 0.1 mm EJ-550，名义折射率 1.46。塑料厚度不缩减，组合外包厚度
  分别为 16.1/24.1 mm。
- 原 `sr90_allowed_beta_v1` Sr-90/Y-90 连续谱逐字复用；中央点源初始方向 −Z，
  始终位于实际 +Z 入射面外 1.5 mm。没有单能覆盖。
- 裸抛光外表面；接缝两侧是显式、双向 polished dielectric 界面，没有涂层。
- Side 2：同一 +X 面，两个 2.4×2.4×0.5 mm SiPM，中心
  `(50.25,−25,0)`、`(50.25,+25,0)` mm。保持整个组合体厚度中点 z=0。
- 8+8 时每颗 SiPM 跨缝，接触面积为前片 2.76、胶 0.24、后片 2.76 mm²。
  两个非对称朝向中，每颗 SiPM 的 5.76 mm² 接收面全部接触 16 mm 片。
- 关闭 steel、stack、dimple 和旧的底面 SiPM grease 几何。新的片间胶层是独立实体。

方向标签均沿源的入射方向书写：“8 → 胶 → 16”表示 8 mm 片朝向源。
这是用户选定的简化模型，未进行新的实验束流/表面校准。

## 实现和本次发现的继承缺陷

新增 opt-in combined 几何、两片登记、接缝材料及边界、参数约束、实际几何日志，
并保存逐片产生量、能量沉积、光学谱系、光学出生和全程几何边界状态。
具体接口和复现命令见 [工具 README](../../tools/combined_tiles/README.md)。

光学探针揭示旧模式只凭 `prePV == SiPM` 就计数并终止光子。在发生 Fresnel
反射后的 StepTooSmall 回迁步骤中，pre-volume 也可能暂时显示为 SiPM，造成
没有成功入射却被计数的情况。第一版两个胶缝→传感器探针里实际找到了三个这样的事件。

修正模式 `/opnovice2/sipm/readoutMode boundary-entry` 只在实际几何边界、
pre/post 是不同体积、post 是 SiPM，且状态为 Transmission、FresnelRefraction
或 SameMaterial 时计数并终止。反射和 StepTooSmall 不会触发检测。
所有五个最终比较配置统一使用该模式。默认 `legacy` 保留用于历史复现，
单独验证原结果和最终随机状态完全不变。

新版胶缝探针中，两组分别是 **8 次成功入射＝8 次检测**。四个只有反射/回迁而
没有后续入射的事件均 D=0，随后在胶层留下光学能量沉积，支持光子继续传播。
这修复了已发现的计数问题，不代表完成了所有光子的完整出生/终态闭合。

## 最终工程验收

| 检查 | 结果 |
|---|---|
| 新工具与回归测试 | 34 项通过，包括真实反射误计模式和退出码为零的 UI 错误 |
| legacy 几何与 50-beta 初检 | 通过；两个单体的全部原 scan 列、汇总字节、直方图数值与最终 RNG 匹配旧基线 |
| boundary-entry 五组几何与 50-beta 初检 | 通过；两个塑料块、接缝、传感器位置和接触面积一致，无抽样重叠报告 |
| 光学/计数探针 | 370 个 optical primary＋10 个单能 electron；四方向透射、75° TIR、折射率匹配、跨缝进入两传感器、前后块独立产光记账均通过 |
| 非法配置 | 9 组、零事件，按预期异常码和具体原因拒绝 |
| 最终探索 | 10 个独立进程、每个 100 个 beta，全部正常退出；全部源事件保留 |
| 独立复核 | 原始 ROOT、逐事件 CSV、计数与谱系、输入/源码/二进制哈希、四个 bootstrap 对照全部一致 |

新 `readout_events` 对每个源事件核对真实入射与检测；拼接体再通过独立
`combined_boundaries` 状态表逐事件交叉核对。记录的所有几何边界均没有
NoRINDEX。边界统计包含重复碰撞和 StepTooSmall，不能直接当作透射概率。
几何 overlap 是有限抽样检查，不是对所有边缘路径的数学证明。

## 固定 1,000-beta 探索结果

主指标 D 是每个**发射的源 beta 事件**平均进入理想 SiPM 体积的光子数，
包含未进入塑料和零响应的源事件；不是 photoelectron 数或电子学信号。
1,000 个源事件中 985 个记录到 primary 入块，15 个未入块事件仍保留在分母中。

每配置两组独立种子、每组 100 事件；不进行跨配置逐事件配对。

| 配置（沿源入射顺序） | 源事件数 | 平均 D/源事件 | 零响应事件 | 两个 seed block 的平均 D |
|---|---:|---:|---:|---:|
| 整块 16 mm | 200 | 102.680 | 4 | 99.67 / 105.69 |
| 整块 24 mm | 200 | 75.415 | 3 | 76.14 / 74.69 |
| 8 → 胶 → 8 | 200 | 39.100 | 3 | 39.32 / 38.88 |
| 8 → 胶 → 16 | 200 | 11.490 | 17 | 11.24 / 11.74 |
| 16 → 胶 → 8 | 200 | 36.290 | 10 | 36.05 / 36.53 |

| 对照：target − reference | D/源事件差值 [95% CI] | 相对差值 [95% CI] |
|---|---:|---:|
| 8 → 胶 → 8 − 整块 16 mm | -63.580 [-78.625, -49.409] | -61.92% [-68.63%, -54.19%] |
| 8 → 胶 → 16 − 整块 24 mm | -63.925 [-73.680, -54.115] | -84.76% [-87.40%, -81.61%] |
| 16 → 胶 → 8 − 整块 24 mm | -39.125 [-50.110, -28.615] | -51.88% [-60.39%, -42.12%] |
| 16 → 胶 → 8 − 8 → 胶 → 16 | +24.800 [+19.455, +30.405] | +215.84% [+160.40%, +284.35%] |

每个对照使用 10,000 次独立事件 bootstrap，在各 seed block 内重采样；区间为
pointwise percentile 95%，未对四个比较作多重性校正，未包含材料/表面/源模型
系统误差。独立实现重新计算全部四项区间，与输出在 1e-10 内一致。

在这些固定条件下，三个拼接方案的收集量均低于对应整块，且方向相关差异明显。
这不建立实验等效性结论。方向对比同时改变源侧塑料厚度、接缝位置和传感器
接触的前/后片，不能把差异单独归因为某一个机制。

## 物理与统计口径限制

- EJ-550 吸收沿用仓库数字化厂家 0.1 mm 样片透射数据，按
  `L = −0.1 mm / ln(T)` 转换为有效衰减长度。实际 EJ-200 发射谱在数据覆盖范围内。
  该近似未将样片测量中的界面反射/散射与纯体吸收分开，可能重复计入部分损失；
  不确定区间不包含这种模型不确定性。
- 继承的 World 吸收长度为 100 mm，EJ-200 为 3800 mm。读出仍是理想 SiPM 体积
  任意面的成功入射，不含 PDE、fill factor 或电子学。
- Legacy G 不含全部 optical-parent births，不能作为全光子出生数。
  新 birth-region 与既有 lineage-origin 分别记录，不能混为同一量。
- Legacy tile edep 包含光学沉积；新诊断分开记录 optical/nonoptical。
  没有输出 primary 电子的完整背面出射计数，不能据后片沉积为零推断穿透概率。
- 控制台旧的 nominal primary energy 文本可能显示 1 MeV；实际 beta 能量以
  逐事件 ROOT 和冻结的 GPS 任意谱为准。

## 模型版本、失败尝试与样本隔离

`20261010-implementation-v1/REJECTED_MODEL.json` 明确排除了继承误计模式的
全部 1,000 个探索事件；它们只作为错误诊断保留。没有选择性丢弃单个事件，也没有
与 v2 合并。v1 的结构审计通过不构成物理读出验收。

v1 的首个探针计划未执行；下一计划因错误 GPS 命令在发射前中断（11 次初始化，
实际 0 事件）。修正输入后，探针进一步暴露了上述真实计数问题。旧日志、宏与
凭据全部保留。运行器现会在明确 UI 错误或缺失输出时立即停止，即使进程退出码为零。

v1 两轮初检共 100 beta、一次诊断探针共 370 optical＋10 electron，均为开发证据。
v2 的 50 legacy beta、50 corrected beta、370 optical＋10 electron 探针也不进入
最终 1,000-beta 统计。更早的 20-beta preflight 保持独立。

## 可复现证据

最终证据根目录：`outputs/combined-tiles/20261010-implementation-v2/`。
这些原始运行目录被 Git 忽略，保留在本地；本次未发布到上游。

- `qualification.json`：从真实几何、初检、探针、legacy 回归、非法配置证据重新验证生成。
- `explore/manifest.json`：五配置、十任务、源谱、材料、种子、源码/工具哈希与固定预算。
- `explore/*/block-*`：每任务输入、完整日志、ROOT、汇总 CSV、RNG 状态及真实退出回执。
- `analysis/`：完整事件 CSV、readout/combined 诊断、数值 JSON、对照 CSV 和简明报告。
- `build-inputs.json`、`runtime/`、`reproduction/`、`implementation.patch`：构建输入和最终源码备份。
- `image.json`、`container.json`、`data-receipt.json`、`runtime-environment.json`：环境身份。
- 34 个测试在宿主 Python 环境执行通过。一次容器测试尝试因工作树 `.git` 指向
  容器外宿主路径，Git 元数据读取失败；该日志保留。准备步骤需要可读 Git checkout，
  Geant4 执行与分析均在容器中完成。

最终运行：Geant4 11.4.2、linux/arm64、Serial；Python 3.10.12、NumPy 2.0.2、
uproot 5.6.9。离线、非 root 容器；源码/数据只读，build/output 独立；2 CPU、4 GiB
限制，任务串行。十个探索进程耗时合计 141.47 秒（不含构建与审计）。
已有数据集安装收据保留，本轮未重新逐文件哈希整个解压数据集。

- 镜像：`sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee`
- 可执行文件 SHA256：`2841a45fafb3963fbfd730ef76f07ed32ad47e990834aeb2166d90a6461719d4`
- 最终探索 manifest SHA256：`f48c450b8fe730e71493d724d3d2464183f35030b639667a45738f7210713d37`
- analysis.json SHA256：`70c8ad50e55ef3832b2f30dc055948fd0040108b530a1d5be7ec18898e2260de`

材料来源：[Eljen EJ-550](https://eljentechnology.com/products/accessories/ej-550-ej-552)。
边界状态核对：[Geant4 11.4.2 官方头文件](https://raw.githubusercontent.com/Geant4/geant4/v11.4.2/source/processes/optical/include/G4OpBoundaryProcess.hh)。
历史起点：[只读扫描后的 preflight](combined-tiles-preflight.md)。
