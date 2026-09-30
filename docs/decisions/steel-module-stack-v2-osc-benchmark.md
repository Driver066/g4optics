# Steel Module v2：OSC 环境与资源 benchmark

本批仅用于远端数值资格验证和资源测量，不能作为间隙、布局优劣或等价性的科学证据。
Geant4 11.4.2、x86_64、Serial，数值基线 `painted-corner-v2 / 16τ`；
再闪烁及原 steel 宏的过程开关保留。原 OSC checkout、旧数据目录和本地默认程序均保留。

## 远端环境与归档

批次位于 Pitzer：
`/users/PAS2524/anolddriver66/g4optics-rn/steel-stack-v2/20260930-benchmark-v1`。
以下记作 `C`。通过用户指定的 OSC VS Code 窗口及专用 terminal 6 执行。
本文据远端终端显示的回执与报告整理；原始机器可读证据留在 OSC，本文不是原始报告的逐字副本。

- 冻结源码提交：`737f8010f7697a4d0c1187ecc08e9da04fd73c2a`。
- 源码清单 SHA-256：`d89d21a04962be3f5819ecec8607e644411d7fbffa39519a1c6e893ddaf47ed6`。
- 远端程序 SHA-256：`fe9663534c143e7ccb1d08cde5abc95801903ed463d8e7da19af615ff727b10e`。
- SIF SHA-256：`6a777120a8ad8ae4959580e5c1e9eaa4dc2a837a95cbfca533f2f07d71d54962`。
- `C/source/`、`source-manifest.json`、`remote-build.json` 保存冻结构建输入与结果；程序在 x86_64 容器独立构建。
- 新数据目录 `~/geant4-data/11.4.2-validated-v1` 的全部 12 项版本与文件校验通过。
  旧 `11.4.2` 目录含混合旧数据包，因此没有复用其目录身份。
- `C/analysis-venv` 使用 OSC Python 3.12 和仓库分析依赖锁；锁定包与安装日志归档。
- 新审计工具从单独的已提交版本导出到 `C`，未覆盖冻结模拟源码或程序。

## 远端数值资格验证

作业 `55219639` 在计算节点执行 10,560 个单光子事件和 8 个中子对照事件，
耗时 3 分 58 秒，退出状态 `COMPLETED / 0:0`。
原始棱边射线路径复现；候选尺度、相邻尺度稳定性、负控制的物理结果及 RNG 检查通过。
远端 x86_64 不要求与本地 ARM64 的中子逐事件结果逐位相同。

原始报告：`C/qualification/qualification-result.json`，SHA-256：
`c4c63c31380dec49f738040fb4389b85cb05d4ce23432fadde3658c3d0566d16`。
资格验证控制器来自提交 `74e79c2b79e811d1bda3953569219f416909fa24`。

## 资源 benchmark 与启动问题

8 配置：4/24 mm 厚度 × back-center/edge-two × 0.5/1.0 mm 间隙。
每配置两组独立种子，各 25 个中子；合计 16 项、400 事件。
请求每项 1 CPU、4 GiB、1 小时；首批最多同时运行 4 项。

原 array 为 `55219829`。其中 11–13 在启动前分配到 `p0748` 的 2 CPU 后被
严格单核检查拒绝，没有创建模拟输出或产生事件。其余项按原登记任务运行。
这些失败的调度日志、分配记录与提交回执均保留。

[OSC 的节点资源表](https://www.osc.edu/resources/technical_support/supercomputers/pitzer/guidance_on_requesting_resources_on_pitzer)
列出 40 核节点每核约 4556 MiB、48 核节点约 3797 MiB 可用内存。
因此本次 4 GiB 请求在 48 核节点会获得额外 CPU。
补齐作业 `55220184` 只运行缺少的 11–13，显式指定 `40core`，
保持原程序、任务、种子、1 CPU、4 GiB 和 1 小时上限。它等待原 array 结束后再启动。
补齐不增加登记的总事件数，失败记录不覆盖。

冻结任务清单 SHA-256：
`b040331d489254d1d04f7f73742f2bda5de6cf3fe24da38761532ea440811d4f`。
原脚本 SHA-256：
`860bd596f07b6a0ec2b5b435a82b1c5fb13b1cbe6e3c6ba78070c823ffe4d75e`。
节点约束修复已加入 `osc.py --node-constraint`；提交 `611ddd57649320fb6bf8e586f007940e775fd28b`，71 项测试通过。

## 结果审计

审计作业：`55220237`。控制器来自提交 `eccb5df78ba5e7c6811345d3800840c3238001ab`，
文件 SHA-256：`38834442d6bcd83d74a72e70373ecbcc5ee30da5b09a8cb13efa2e7108fc0695`。
它在计算节点读取每项结果，验证任务和环境身份、完整文件校验、ROOT 账本、
SiPM 合计、能量沉积、实际过程状态及数值修正记录，再汇总资源；不运行新事件。

16 项、400 事件全部通过完整结果审计，最终有效任务均为 `COMPLETED / 0:0`，
实际分配 1 CPU。原来的 3 个启动失败仍保留，不计作模拟事件。
`NoRINDEX`、零步越界、未结算光子为零；光子账本、传感器合计及能量沉积一致。
这 400 个中子事件没有触发数值修正；修复有效性证据来自上面的靶向数值验收。

原始报告 `C/benchmark-audit.json` SHA-256：
`7e4acfd2cce08de37216ad913e4cef64b416adbcb8d1d24bbe216c3fd8040675`。
调度完成汇总 `C/benchmark-completion.json` SHA-256：
`820aa791f43e4ec24f60c3642a9b9b1f4f60f84b8ec48e1572c075fb38bd3ebf`。
`C/benchmark-scheduler-accounting.txt` 保存包括原失败项在内的最终 Slurm 记录。

## 实测资源与下一批建议

下表每格为一组独立种子的 **25 事件**启动器耗时，包含初始化及模拟输出。

| 布局 | tile 厚度 mm | 间隙 mm | 块 0 秒 | 块 1 秒 |
|---|---:|---:|---:|---:|
| back-center | 4 | 0.5 | 66.971 | 77.319 |
| edge-two | 4 | 0.5 | 104.222 | 105.371 |
| back-center | 24 | 0.5 | 322.746 | 220.261 |
| edge-two | 24 | 0.5 | 298.210 | 254.683 |
| back-center | 4 | 1.0 | 72.028 | 94.255 |
| edge-two | 4 | 1.0 | 109.403 | 89.398 |
| back-center | 24 | 1.0 | 284.894 | 231.895 |
| edge-two | 24 | 1.0 | 283.622 | 359.478 |

- 最慢观测速度 14.379 秒／事件；全部模拟启动器累计 0.8263 小时。
- 16 项有效 Slurm allocation 累计 0.84194 核时，含 worker 的校验开销。
- 子进程峰值 RSS 325.61 MiB；Slurm 的 `.batch` 峰值 MaxRSS 为 478,704 KiB，
  即 467.48 MiB。两种测量分别保留，短样本的内存峰值不构成后续任务的上界。

对这 8 个间隙初筛配置，建议先使用已实际验证的资源 **1 CPU、4 GiB、40core 节点**，
任务规模建议为 **100 事件／任务、
1 小时时限**。按本次最慢速度线性外推，100 事件约 24 分钟；乘二再加
5 分钟余量约 53 分钟。按同一公式计算的整数容量为 114，选择 100 便于分块。
短 benchmark 可能漏掉特别耗时的中子簇射，因此这些估算不保证所有任务都在限时内完成。
本次资源测试没有覆盖 back-four/back-two 或中间厚度，不把这些速度自动推广到完整 48 配置。

此建议尚未生成或提交科学扫描。每配置总事件数、独立块数、最小有意义的差异及
最终间隙仍需后续确定；不能从本次资源耗时差异推断光学性能差异。

顶层归档索引为 `C/campaign-artifact-index.json`，SHA-256：
`d2cc6fd90cdb6d1f1ba6556dbd820b899fd73644d0a27afc8afaccabc8cec288`。
`post-benchmark-preservation.json` 确认旧 OSC checkout 仍在原提交且干净，
构建 checkout 仍固定于 `737f8010`；后续推送未改变本批的冻结运行输入。
