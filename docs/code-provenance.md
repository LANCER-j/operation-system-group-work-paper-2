# 代码来源与修改说明

## 原始研究与代码

- Bouron et al., *The Battle of the Schedulers: FreeBSD ULE vs. Linux CFS*, USENIX ATC 2018, pp. 85–96：[论文](https://www.usenix.org/system/files/conference/atc18/atc18-bouron.pdf)。
- [作者仓库](https://github.com/JBouron/linux/tree/loadbalancing)，固定提交 `8e76f4826daadebce6e7f5fe9cbd48823fd403a0`。
- 上游 Linux v4.9 基准提交 `69973b830859bc6529a7a0468ba0d80ee5117826`；[原始发行档案](https://www.kernel.org/pub/linux/kernel/v4.x/linux-4.9.tar.xz)。

Linux 与作者 ULE 代码不属于本组原创。原始版权声明应保留；许可证见 `licenses/Linux-COPYING`。`source-lock.json` 列出作者相对上游的修改文件；它不是本组修改清单。

## 本组代码与适配范围

| 文件 | 作用 / 修改 |
|---|---|
| `build/build_kernels.sh` | 下载固定源码、共同配置、构建、记录日志与校验值；对 CFS 的 `include/linux/log2.h` 同步作者兼容修正 |
| `build/apply_ule_compat.py` | 修改作者 `kernel/sched/sched_ktz.c`，保护缺失 LLC 调度域并回退，输出补丁与元数据 |
| `build/make_initramfs_in_guest.sh` | 在独立客户机中安装对应模块、配置并生成 initramfs |
| `experiment/fibo.c` | 重建的固定工作量 Fibo，不是声称取得了论文原始 Fibo 实现 |
| `experiment/run_experiment.py`、`run_experiment.sh` | 环境核验、分组、CPU 亲和性、数据库初始化、计时探针、启动与清理 |
| `experiment/sample_cpu.py`、`trace_cpu.py` | 原始进程计数与调度事件采集；统一比较口径 |
| `experiment/summarize.py`、`analyze_results.py` | 从实际输出汇总与计算统计；本周不附输入结果 |
| `experiment/test_*.py` | 离线计数器和拒绝路径检查；测试夹具不作为实验数据 |
| `scripts/Start-VM.ps1` | 参数化 Windows/QEMU 启动入口，显式传入机器相关路径和根分区 |

已有代码由本地准备材料整理，本周副本补充了缺失工具检查和阶段性文档。构建脚本会生成具体源码补丁；这些输出应在后续结果提交中与日志一起保留。

## 贡献与 AI 使用

AI（Codex）协助了源码理解、实验与构建脚本编写、兼容问题排查，以及本周代码整理和文档起草。小组成员须审核脚本、理解变量与测量边界，并对提交内容负责。此次不提交实验结论。

本次提交者为邹鑫棠（1240028311），身份由本人提供。其他成员信息与个人分工尚待确认，不能推断或虚构。每位成员应使用自己的账号提交自己实际审核、完成的实质修改；每条提交说明均需包含学号、全名和变更内容。
