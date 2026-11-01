# 论文环境与本组实验环境

本周处于实验启动阶段。本文件整理已有环境配置与后续复现目标，不给出性能结果。版本依据本地准备记录；在每次新环境执行时仍须核验并保存实际版本，不能将计划值写成新机器的检测结果。

## 环境对照

| 项目 | 原论文设置 | 本组配置 / 复现目标 | 差异与理由 |
|---|---|---|---|
| 硬件 | 主实验：32 核 AMD Opteron 6172，32 GB RAM | Windows 宿主；Intel Core Ultra 5 236V，8 个逻辑处理器，约 16 GB RAM | 利用现有机器；不比较绝对硬件性能 |
| 虚拟化 | 主实验为原文物理机器配置 | QEMU 11.1.2 / WHPX；Nehalem CPU 模型；1 插槽、4 个虚拟核心、4096 MiB RAM | 隔离实验内核；外层调度与虚拟 I/O 可能影响测量 |
| 系统 | Linux，发行版未完整说明 | Ubuntu 18.04.6 LTS x86_64 | 为旧内核使用一致用户空间 |
| 内核 | Linux 4.9 | `4.9.0-paper02-cfs` 与 `4.9.0-paper02-ule` | 两套独立内核，同一配置，仅版本后缀不同；有明确兼容修改 |
| ULE 来源 | FreeBSD 11.1 的 ULE 移植到 Linux | 作者固定提交，见下文 | 不使用原生 FreeBSD 作为对照 |
| 编译器 | 未完整公布 | GCC 7.5.0（Ubuntu 包 `7.5.0-3ubuntu1~18.04`） | 保持双方工具链一致 |
| 构建工具 | 未完整公布 | GNU binutils 2.30；GNU Make 4.1 | 构建时保存版本 |
| 驱动语言 | 未完整公布 | Python 3.6 或更新；脚本使用标准库 | 新机器的精确 Python 版本需在运行前记录 |
| 数据库 | Sysbench + MySQL 读写负载，完整版本/参数未给出 | MySQL 5.7.42（服务端包 `5.7.42-0ubuntu0.18.04.1`）；Sysbench 1.0.11 | 作为重建参数披露，不能声称与原文完全一致 |
| CPU 放置 | 第 5.1 节单核竞争 | 工作负载 CPU 0；控制和采样程序另核 | 减少控制程序直接竞争 |
| 分组 | 描述应用级公平性，未完整交代 cgroup 层级 | 两个等权 CPU cgroup；Sysbench 与 MySQL 同组 | 显式重建设置；ULE 不因此具备 CFS 组公平语义 |
| 时间测量 | 累计 runtime 曲线 | 两侧用 `sched_switch` 重建调度驻留时间 | ULE 原始进程计数有兼容问题；此口径可能包含中断和虚拟化停顿 |

## 固定源码与修改

- 上游 CFS：Linux v4.9，`69973b830859bc6529a7a0468ba0d80ee5117826`。
- 作者 ULE：`JBouron/linux` 的 `loadbalancing` 固定提交，`8e76f4826daadebce6e7f5fe9cbd48823fd403a0`。
- 作者修改文件及 Git blob 标识见根目录 `source-lock.json`。构建脚本按固定提交下载压缩包，记录 SHA256；这不是对整棵源码树独立真实性核验的承诺。
- CFS 的 `include/linux/log2.h` 同步作者的 GCC 7 兼容修正。
- ULE 的 `kernel/sched/sched_ktz.c` 增加缺失末级缓存调度域的空指针保护与回退；正常非空路径保留。修改前检查作者原文件 blob，补丁输出到构建日志目录。
- 双方共同开启 `CONFIG_RT_GROUP_SCHED=y` 与跟踪配置。有效配置会归一化比较，仅允许 `CONFIG_LOCALVERSION` 不同。

以上改动使研究对象成为“明确适配后的 ULE 移植”，不是完全未修改的作者版本。更多归属说明见 [code-provenance.md](code-provenance.md)。

## Linux 客户机准备

使用独立可丢弃的 Ubuntu 18.04.6 x86_64 虚拟机，预留足够空间容纳两套源码、目标文件和模块；建议起步预留 30 GB 可用磁盘，再根据实际使用增长。配置 4 个 vCPU、4 GB RAM。先准备能够正常登录的客户机，再构建实验内核。

在客户机内安装依赖：

```bash
sudo apt-get update
sudo apt-get install build-essential gcc-7 binutils make libncurses5-dev \
  libssl-dev libelf-dev bc bison flex perl curl ca-certificates xz-utils \
  gzip tar kmod initramfs-tools python3 sysbench mysql-server-5.7
```

上述安装命令不保证软件源仍提供目标补丁版本，不能当作完整版本锁定。新环境需检查 `gcc-7 --version`、`ld --version`、`make --version`、`python3 --version`、`mysqld --version`、`sysbench --version`。若与目标不一致，先解决版本差异或在偏差表说明，并对两内核使用相同版本。不要隐瞒软件源或版本变化。

源码获取已包含在 `build/build_kernels.sh`。默认构建目录为 `build/work/`，也可以通过 `PAPER02_BUILD_ROOT` 指向无空格路径。

实验脚本需要 cgroup v1 CPU 控制器。先检查 `/proc/mounts`；仅当尚未挂载时，在实验客户机内设置：

```bash
sudo mkdir -p /sys/fs/cgroup/cpu
sudo mount -t cgroup -o cpu cpu /sys/fs/cgroup/cpu
```

独立 MySQL 实例使用临时目录、Unix socket，并禁止 TCP 监听；脚本不停止其他数据库服务。尽量保持客户机无其他负载，检查 AppArmor 是否允许实验临时数据目录；如需要权限适配，应在该隔离客户机内做最小修改并记录。

## Windows 启动准备

准备支持 WHPX 的 QEMU 与可以登录的客户机 qcow2 磁盘；版本以 `qemu-system-x86_64 --version` 实际输出为准。虚拟机需已完成账号初始化，启动脚本不会创建账号、下载磁盘或安装 QEMU。

将构建产物中的 `bzImage`、`initrd.img`、`kernelrelease.txt` 和配置复制到 Windows；启动示例见根 README。`scripts/Start-VM.ps1` 显式接收 QEMU、磁盘、产物路径及根分区标识，不包含个人路径或固定磁盘 UUID。日志只写入本地 `local/`，默认 SSH 转发为 `127.0.0.1:22222`；实验客户机需要自行启用 SSH 服务。

记录 CPU 拓扑、启动命令、根分区、内核配置与版本。两侧保持虚拟硬件、用户空间、电源设置及背景负载尽量一致。每轮实验创建新的数据库数据目录。

## 尚需完成的验证

本周只提交启动代码与方案。后续应在清洁环境复核构建/启动流程、执行两内核贯通测试、检查采样完整性和工作负载共同存活区间，再按协议整理正式结果。离线单元测试不能替代这些运行检查。
