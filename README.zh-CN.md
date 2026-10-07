# QE HPC Toolkit

[English](README.md) | **简体中文**

把**已有的 Quantum ESPRESSO `pw.x` 输入文件**提交到 Slurm 集群，并查询状态、取回输出。工具会检查输入文件与赝势文件是否对应，保存每次提交的记录，避免同一作业 ID 被重复提交。

## 快速上手

下面以 Windows PowerShell 为例。第一次使用需要 Python 3.11+、本机可用的 `ssh`/`scp`，以及能通过 SSH 登录的 Slurm 集群。集群计算节点需能运行 `pw.x`。

### 1. 安装工具

```powershell
git clone https://github.com/Syanz7755/qe-hpc-toolkit.git
Set-Location .\qe-hpc-toolkit
python -m pip install -e .
```

### 2. 准备一次计算

复制请求模板：

```powershell
Copy-Item .\examples\request.example.json .\request.json
New-Item -ItemType Directory -Force .\pseudo | Out-Null
```

在项目目录放入**已检查过的** QE 输入文件 `input.in`，在 `pseudo` 目录放入它引用的真实 `.UPF` 赝势。准备完后是这个结构：

```text
qe_hpc_toolkit/
├── request.json
├── input.in
└── pseudo/
    ├── Mg.upf
    └── O.upf
```

打开 `request.json`，至少修改：

- `cluster.host`：本机 SSH 配置中的集群别名，例如 `my-cluster`。先用 `ssh my-cluster` 确认能登录。
- `cluster.remote_root`：集群上你有写入权限的**绝对路径**，例如 `/scratch/your-name/qe-jobs`。
- `job.job_id`：这次计算的唯一名称；每次新计算都换一个 ID。
- `job.calculation`、`job.resources`：分别与 `input.in` 中的计算类型、集群队列和资源要求对应。
- `job.pseudopotentials`：列出 `input.in` 的 `ATOMIC_SPECIES` 中引用的每个赝势文件。模板只有 Mg 和 O；你的体系不同就改成自己的文件清单。

如果集群需要加载模块，设置 `cluster.modules`；若无需加载，设为 `[]`。按集群要求选择 `mpi_launcher`（`srun` 或 `mpirun`）和 `qe_executable`。`input.in` 中的 `pseudo_dir` 必须为 `./pseudo`，`outdir` 必须为 `./outdir`。模板中的 [input.in](examples/input.in) 只展示文件格式，不能代替经过检查的科学输入。

### 3. 先预览，再提交

```powershell
qe-hpc preview --config .\request.json
```

`preview` 只在本地运行。它会给出文件 SHA-256、远端目录和完整 Slurm 脚本。确认输入、赝势、分区、核数、内存、时限以及脚本中的 `pw.x` 命令后提交：

```powershell
qe-hpc submit --config .\request.json
```

成功时输出中的 `slurm_job_id` 是集群作业号。提交后请保留原 `request.json`、`input.in` 和赝势文件；状态查询会核对它们是否仍与提交时一致。

### 4. 查看状态并取回结果

```powershell
qe-hpc status --config .\request.json
```

按需重复查询。状态变为 `completed`、`failed`、`cancelled` 或 `timeout` 后运行：

```powershell
qe-hpc fetch --config .\request.json --destination .\artifacts
```

结果在 `artifacts/<job_id>/`：`output.out` 是 QE 输出，`stdout.log`、`stderr.log` 是可用的作业日志，`result.json` 给出 `JOB DONE.`、SCF 收敛标记和最后一个总能量。**Slurm 的 `completed` 只表示作业进程结束**；请结合这些 QE 证据检查计算结果。`outdir/` 中的大型中间文件保留在集群。

## 遇到提交结果不明

如果 SSH 在提交时超时，记录可能显示 `submission_unknown`。先到集群核实是否已有作业，得到真实 Slurm ID 后再登记：

```powershell
qe-hpc adopt --config .\request.json --slurm-id 12345
qe-hpc status --config .\request.json
```

同一个 `job_id` 的 `submit` 不会自动重试。默认记录保存在当前目录的 `.qe-hpc/`；请在同一目录运行后续命令，或每次显式指定相同的 `--state-dir`。其他错误和处理办法见[错误说明](docs/ERRORS.md)。

## 接口与开发

请求字段及状态说明见[接口契约](docs/INTERFACE.md)；`qe-hpc schema` 可输出完整 JSON Schema。此工具只负责已准备好的 `pw.x` 输入到集群作业的执行链路，不生成结构或科学参数。

开发时运行：

```powershell
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```

项目约定见 [AGENTS.md](AGENTS.md)。
