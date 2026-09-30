# Steel Module v2 局部内移候选与停止结论

本次实现 `painted-corner-v1`，保留 `legacy` 默认值。它是未晋级的数值候选，
不构成可用于后续科学扫描的新基线。固定 Geant4 11.4.2、Serial、既有尺寸、
表面、材料、SiPM 计数和旧宏物理开关，保留 opticalphoton Scintillation。

## 规则与接口

`PaintedCornerBoundary` 继承 `G4OpBoundaryProcess`，名称仍为 `OpBoundary`。
`StackOpticalPhysics` 在候选模式使用 11.4.2 同顺序的光学过程注册，其他模式
调用安装版实现。逐次运行记录并比较实际 post-step 过程顺序与激活状态。
项目没有改动 Geant4 安装或全局容差。

只有原 tile 的实际 `polishedfrontpainted` 界面发生存活 `SpikeReflection`，
反射点在至少两个外表面一个运行时 surface tolerance 范围内，且下一步
`StepTooSmall` 已正常返回同一 tile，才在该步把近面坐标内移到 `sτ`。
核对 post-step、next-touchable、navigator 和材料；候选点必须严格在原
无子体积的 box tile 内。体积状态不一致立即报错。若正常返回原 tile 的
先决条件不成立，保留原输运结果，由晋级测试判定是否失败。

先同步同体积 navigator 的位置，再仅对 ParticleChange 调用
`ProposePosition`。不改变触摸体积、不伪造 pre-step、不直接改 track、
不改变动量、偏振、能量、时间、权重、次级、终止状态或随机抽样。
运行时再次验证上述 ParticleChange 属性及物理步长、沉积保持原值。
每次反射最多修正一次；数值位移上界为 `sqrt(3)*(s+1)*τ`。

`--optical-numerics legacy|painted-corner-v1` 及诊断尺度
`--optical-corner-scale 16|32|64` 进入配置哈希和运行状态；v1 仍为原过程。
没有晋级尺度，不能把任一候选默认为正式基线。

## 预登记与证据

矩阵包含 165 个射线 × 2 个明确线偏振 × 8 组固定种子 × 4 个设置，
共 10,560 个光学事件。完整十层几何保持不变；靶向探针使用 24 mm tile、
0.5 mm 间隙、back-four，其他布局只用于其 SiPM 负控制。
能量 2.6988098789 eV，靶向起点在预期命中点入射线路后方 1 mm。
起点、方向、偏振、种子、四布局控制、12 条棱与 8 个顶点全部预登记。
独立光学源绕过 GPS 的随机偏振路径；它不能通过生产中子源审计。

另有 4 个数值设置各 2 个原失败中子事件。legacy 与冻结失败数据精确一致，
包括旧字段、直方图、summary、RNG 及四个 v2 账本表。

完整结果位于
`outputs/steel_stack_v2/20260930T042800Z-corner-recovery-r2/`。
前一次初始化参数名错误在事件开始前即被拒绝，独立保留于
`outputs/steel_stack_v2/20260930T040353Z-corner-recovery/`，没有消耗模拟事件。

## 未晋级的原因

三个候选尺度均消除了原中子案例的一次零步穿出及 NoRINDEX，也消除了
诊断记录器识别的 182 次同类零步路径。但每个尺度仍有 608 个靶向光学
事件出现 NoRINDEX：216 个棱边事件、392 个顶点事件。
16/32 与 32/64 的有效边界序列、终态及收集 sensor 均一致；结果稳定
不能替代“所有靶向用例通过”的要求。256 个非目标控制的物理结果与 RNG
保持一致，裸钢控制也保留原有 NoRINDEX 行为。

代表性未覆盖路径是在 x/y 棱边反射后直接进入 World，下一步已经是有
长度的 World→钢传播，没有候选所要求的 StepTooSmall 返回 tile 阶段。
因此仅扩大内移尺度无法覆盖该类路径。本轮没有扩大修复条件或寻找其他尺度。

已按预定停止条件停止完整 130 事件验收矩阵；其通常所需的 122 个新中子
事件没有启动。没有开展科学扫描、提交 OSC、选择间隙、修改历史 latest
或替换已验收默认程序。这些光学输入有意集中在容差尺度棱边，不能将失败
占比解释为物理中子扫描中的发生率或 collection efficiency 偏差。

继续工作需要另行审查可覆盖“没有正常返回原 tile”的处理方案，并重新
验证导航、touchable 与粒子状态一致性；不应放宽 NoRINDEX 验收条件。

实现依据：[Geant4 11.4.2 光学过程构造源码](https://github.com/Geant4/geant4/blob/v11.4.2/source/physics_lists/constructors/electromagnetic/src/G4OpticalPhysics.cc)、
[Navigator 接口](https://github.com/Geant4/geant4/blob/v11.4.2/source/geometry/navigation/include/G4Navigator.hh)。
