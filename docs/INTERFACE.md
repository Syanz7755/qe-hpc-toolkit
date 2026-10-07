# 接口契约（schema v1）

## 请求

`SubmissionRequest` 包含 `schema_version: 1`、`cluster` 与 `job`。未知字段拒绝。CLI 的 `qe-hpc schema` 输出完整 JSON Schema。

| 字段 | 约束 | 含义 |
| --- | --- | --- |
| `cluster.host` | SSH alias，安全字符 | OpenSSH 连接目标 |
| `cluster.remote_root` | 绝对 POSIX 路径，安全字符，无 `..` 段 | 作业目录父路径 |
| `cluster.qe_executable` | 默认 `pw.x`，安全字符 | 集群上的程序名或绝对路径 |
| `cluster.mpi_launcher` | `srun` 或 `mpirun` | MPI 启动器 |
| `cluster.modules` | 模块名数组，安全字符 | 作业脚本中的 `module load` |
| `cluster.connect_timeout_seconds` / `operation_timeout_seconds` | 1–120 秒 / 30–86400 秒 | SSH 建连与单次操作时限；调整不改变请求指纹 |
| `job.job_id` | 字母开头，最多 64 字符 | 本地记录及远端目录名；不可复用 |
| `job.calculation` | `scf`、`relax`、`vc-relax`、`nscf`、`bands` | 必须与输入文件中的 `calculation` 一致 |
| `job.input_file` | 可读 UTF-8 文件 | 原样上传为 `input.in` |
| `job.pseudopotentials` | 非空、文件名互异的 `.UPF` 路径数组 | 上传至 `pseudo/` |
| `job.resources` | 分区、可选账号、MPI ranks、每 rank 线程、内存、时限 | 映射到 `#SBATCH` |

资源 `memory` 使用例如 `16G`；`walltime` 使用 `HH:MM:SS` 或 `D-HH:MM:SS`。请求中的文件路径在 CLI 中相对于 JSON 文件解析；Python 接口使用调用方传入的 `Path`。

`pw.x` 输入至少包含 `&CONTROL`、`&SYSTEM`、`ATOMIC_SPECIES`、`ATOMIC_POSITIONS` 和 `K_POINTS`。`pseudo_dir` 必须为 `./pseudo`，`outdir` 必须为 `./outdir`。每个 `ATOMIC_SPECIES` 中的赝势文件名必须在 `job.pseudopotentials` 中。这里检查的是可执行前提和文件一致性，不校验物理模型、QE 版本语法或赝势适用性。

## 公共操作

| 操作 | 输入 | 返回 | 副作用 |
| --- | --- | --- | --- |
| `preview(request)` | `SubmissionRequest` | `SubmissionPlan` | 无 |
| `submit(request)` | `SubmissionRequest` | `JobRecord` | 上传并调用一次 `sbatch` |
| `adopt(request, slurm_job_id)` | 原请求、已核实的数字 ID | `JobRecord` | 更新本地记录 |
| `status(request)` | 原请求 | `JobRecord` | 查询集群并更新本地记录 |
| `fetch(request, destination)` | 原请求、目标目录 | `QEParseResult` | 下载可用文本输出并写 `result.json` |

`SubmissionPlan` 包含 SHA-256、远端目录和完整脚本。`JobRecord` 包含请求指纹、Slurm ID、状态、时间和错误信息。`QEParseResult` 单独记录 QE 完成标记、SCF 收敛标记、最后一个总能量及输出文件 SHA-256。

## 生命周期

```text
staging → submitting → submitted → queued/running → completed/failed/cancelled/timeout
    ↘ stage_failed       ↘ submission_unknown → adopt → submitted
```

提交前检查远端目录不存在。本地记录在远端动作之前创建；同一 job ID 的再次 `submit` 返回 `JOB_EXISTS`。未知提交结果需要人工核实并用 `adopt` 登记真实 ID；`submitting` 状态在本地写入中断后也可经核实使用 `adopt`。`status` 在 `sacct` 尚无记录时保持已有状态。终态后才允许 `fetch`。请求文件或计算配置改变后，`status`/`fetch` 拒绝继续使用该记录。

集群目录结构：`<remote_root>/<job_id>/{input.in,job.sbatch,pseudo/,output.out,stdout.log,stderr.log,outdir/}`。`fetch` 只下载 `output.out` 和存在的日志，远端 `outdir/` 不下载。
