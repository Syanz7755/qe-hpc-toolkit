# QE HPC Toolkit

独立的 Quantum ESPRESSO `pw.x` / Slurm 提交工具。给定已经审查过的 QE 输入文件和对应赝势，它负责校验、预览作业脚本、上传、提交、查询、取回输出及提取基本完成证据。

本工具不生成材料结构或物理参数。`preview` 只做本地检查；`submit` 才连接集群。Slurm 的 `COMPLETED` 与 QE 的 `JOB DONE.`、SCF 收敛分别记录，不互相代替。

## 安装与前提

需要 Python 3.11+、OpenSSH 的 `ssh`/`scp`、可用的 SSH host alias，以及集群上的 Slurm 和 `pw.x`。本地安装：

```bash
python -m pip install -e .
```

准备一个 JSON 请求文件。相对文件路径以 JSON 所在目录为基准。示例见 [examples/request.example.json](examples/request.example.json)，字段规范见 [接口契约](docs/INTERFACE.md)。示例仅展示格式；执行前需提供真实赝势并审查科学输入。输入文件必须将 `pseudo_dir` 设为 `./pseudo`，将 `outdir` 设为 `./outdir`，且 `ATOMIC_SPECIES` 中引用的每个 `.UPF` 文件都列在请求中。

## 使用

以下命令共用同一 `--state-dir`。默认是当前目录下的 `.qe-hpc`。

```bash
qe-hpc schema
qe-hpc preview --config request.json
qe-hpc submit --config request.json
qe-hpc status --config request.json
qe-hpc fetch --config request.json --destination artifacts
```

`preview` 返回输入和赝势的 SHA-256、远端目录、完整 Slurm 脚本与请求指纹。审核脚本和科学输入后再执行 `submit`。成功提交会返回 Slurm job ID；同一 job ID 无法再次提交。`status` 查询 `squeue`，离开队列后查询 `sacct`。终态后 `fetch` 取回 `output.out` 和可用日志，并生成 `result.json`。

若提交时 SSH 超时或 `sbatch` 响应无法确认，记录将为 `submission_unknown`。先在集群核实作业 ID，再执行：

```bash
qe-hpc adopt --config request.json --slurm-id 12345
qe-hpc status --config request.json
```

所有正常输出都是 JSON；错误写到标准错误，格式和处理规则见 [错误契约](docs/ERRORS.md)。退出码 `0` 表示命令完成，`2` 表示请求或执行失败。

## 范围与验证

此版本支持已准备好的 `pw.x` SCF/relax 输入、Slurm 批处理、单个 job ID 的完整生命周期。它会取回文本输出和日志；`outdir` 中的大型中间文件保留在集群。输出解析只提取 `JOB DONE.`、SCF 收敛标记与最后一个总能量，不宣称计算具备科学有效性。

本地验证：

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```

开发规则见 [AGENTS.md](AGENTS.md)。
