# CS340 · Paper 2: ULE vs. CFS

论文：Justinien Bouron et al., *The Battle of the Schedulers: FreeBSD ULE vs. Linux CFS*, USENIX ATC 2018, pp. 85–96.

**本周阶段（2026-11-01）：实验刚刚开始进行。** 本次提交整理构建与实验代码、环境配置和待验证结论，暂不提交测量结果、图表、结论或最终报告。代码包含本地准备和预实验过程中形成的兼容处理；本周阶段描述不表示这些代码从未运行。后续结果需经小组复核后单独提交。

## 研究范围

聚焦原论文第 5.1 节、Figure 1 和 Table 2：在同一个 CPU 核心上运行 Fibo 与 Sysbench/MySQL 数据库负载，比较 CFS 与作者移植到 Linux 的 ULE。比较对象为两套 Linux 4.9 内核，不是直接比较 Linux 与 FreeBSD 整个操作系统。

- 待验证结论 1：CFS 与 ULE 对计算任务和数据库负载的运行机会分配存在明显差异。
- 待验证结论 2：在该负载下，ULE 的数据库吞吐量与延迟可能优于 CFS，同时影响计算任务进度。

以上均为研究问题，不代表本组已在本次提交中确认。指标、判定办法及边界见 [待验证结论与实验方案](docs/claims.md)。不覆盖第 5.2 节、全部应用或多核负载均衡。

## 目录

| 路径 | 用途 |
|---|---|
| `build/` | 两套内核构建、ULE 兼容修改、客户机 initramfs 生成 |
| `experiment/` | Fibo、实验驱动、调度事件采样、统计工具及离线测试 |
| `scripts/Start-VM.ps1` | 使用显式路径和根分区参数启动 Windows/QEMU 实验虚拟机 |
| `docs/environment.md` | 原论文与本组环境、版本、偏差和准备步骤 |
| `docs/claims.md` | 待验证结论、变量、指标和重复实验设计 |
| `docs/code-provenance.md` | 代码来源、起始提交、修改文件和 AI 使用说明 |
| `source-lock.json` | 上游与作者固定提交、作者修改文件的 Git blob 标识 |
| `licenses/` | 上游 Linux 许可证及归属说明 |

## 准备环境

先按 [环境说明](docs/environment.md) 准备独立 Ubuntu 18.04.6 x86_64 虚拟机、GCC 7、MySQL 5.7 和 Sysbench 1.0。将本仓库复制到该虚拟机中无空格的路径。构建和实验脚本在 Linux 客户机运行；不要在 Windows PowerShell 中运行 Linux 命令。

本周没有提供虚拟机磁盘、内核二进制或数据库数据文件。使用普通容器不能替换宿主内核，因此不作为两内核对照的启动方式。新机器完整复现仍需按此流程验证；仅通过离线测试不等于性能实验成功。

## 构建与启动

以下命令均从仓库根目录执行，除非明确说明。

```bash
# 在 Linux 构建虚拟机内；脚本自动下载固定提交源码
bash build/build_kernels.sh configure
JOBS=2 bash build/build_kernels.sh all

# 仅在实验虚拟机内生成启动所需的 initramfs
sudo bash build/make_initramfs_in_guest.sh cfs build/work/artifacts/cfs
sudo bash build/make_initramfs_in_guest.sh ule build/work/artifacts/ule
```

输出位于 `build/work/`：工具版本、源码标识、配置差异、补丁及构建日志在 `logs/`；内核、模块包和配置在 `artifacts/cfs/` 与 `artifacts/ule/`。构建脚本不安装宿主内核；initramfs 脚本会写入实验客户机的 `/boot` 和 `/lib/modules`，但不修改引导器。

Windows/QEMU 启动：先将两套产物复制到 Windows 可访问目录，使用已初始化、可以登录的客户机磁盘。在客户机内用 `lsblk -f` / `blkid` 确认根分区 UUID；不能照搬其他机器的分区标识。

```powershell
# 示例路径需替换为实际值；QEMU 和客户机磁盘需提前准备
./scripts/Start-VM.ps1 -Variant cfs `
  -QemuPath 'D:/qemu/qemu-system-x86_64.exe' `
  -DiskPath 'D:/paper02/guest.qcow2' `
  -ArtifactDirectory 'D:/paper02/artifacts/cfs' `
  -RootDevice 'UUID=<实际根分区UUID>'
```

ULE 使用相同虚拟硬件设置与用户空间，选择 `-Variant ule` 和对应产物目录。一次只运行一个实验客户机，关闭后再切换；不要让两个进程同时写同一磁盘。启动后核验 `uname -r`、`cat /proc/cmdline`、`lscpu`；ULE 的 `/proc/sys/kernel/sched_ktz_enabled` 应为 `1`，CFS 不应有该文件。不要将作者分支的 ULE 开关改成 `0` 来代替独立 CFS 内核。

## 实验入口

先在相应内核下执行小规模贯通测试（smoke test）。绝对输出路径可避免包装脚本切换目录引起歧义。

```bash
sudo python3 experiment/run_experiment.py --scheduler cfs --smoke \
  --output "$PWD/results/cfs-smoke"
# 切换到 ULE 内核后，将 scheduler 与输出目录同时改为 ule
```

正式实验计划每个条件至少运行 3 轮；输出目录必须是新的，脚本拒绝覆盖。

```bash
# CFS 内核
sudo python3 experiment/run_experiment.py --scheduler cfs \
  --output "$PWD/results/cfs-main" --runs 3
# ULE 内核
sudo python3 experiment/run_experiment.py --scheduler ule \
  --output "$PWD/results/ule-main" --runs 3
```

预期产物为每轮的 `run.json`、`fibo.json`、`sysbench_run.log`、`cpu_samples.csv` 和跟踪元数据，以及每组的环境、参数、编译信息与计时探针。均由实际执行产生，本周仓库不附这些数据。

待实际输出完整后，可使用以下工具汇总，不能把离线测试夹具当作测量值：

```bash
python3 experiment/summarize.py results/cfs-main results/ule-main
python3 experiment/analyze_results.py results/cfs-main results/ule-main \
  --output results/analysis-main.json
```

时间预算：首次两内核构建建议预留数小时（规划值，随硬件变化）；贯通测试通常应先预留数分钟，但数据库初始化也有成本。正式单轮执行超时默认 1800 秒，初始化各阶段默认 600 秒，这些是保护上限而非预计性能；实际耗时由后续日志记录。

## 本地检查

```bash
python3 -m unittest discover -s experiment -p 'test_*.py' -v
python3 experiment/analyze_results.py --self-test
bash -n build/build_kernels.sh
bash -n build/make_initramfs_in_guest.sh
bash -n experiment/run_experiment.sh
```

这些检查验证计数器解析、异常拒绝和统计计算，不生成论文结果，也不验证新机器能启动实验内核。

## 提交约定

本次提交者：**邹鑫棠（1240028311）**。其他成员身份与具体分工待确认。

每条 commit message 包含实际提交者学号、全名及变更说明。成员使用本人账号提交本人审核、完成的实质修改；不得虚构分工。本阶段不创建最终 `v1.0` 标签。

`.gitignore` 排除本地结果、编译产物、磁盘镜像、下载缓存及密钥；后续准备结果提交时应按需调整规则并审核具体文件。不要上传整个本地 submission ZIP。

原文：[USENIX PDF](https://www.usenix.org/system/files/conference/atc18/atc18-bouron.pdf)。作者代码：[JBouron/linux — loadbalancing](https://github.com/JBouron/linux/tree/loadbalancing)。代码来源、改动与 AI 使用见 [来源说明](docs/code-provenance.md)。
