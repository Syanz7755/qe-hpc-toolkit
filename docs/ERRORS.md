# 错误契约

CLI 错误输出为 `{"error":{"code":"...","step":"...","message":"...","retryable":false}}`。Python 接口抛出 `ToolkitError`，提供相同字段。`retryable` 描述单次操作是否可能在环境恢复后重试；提交动作仍遵守单 job ID 规则。

| 代码 | 含义 | 处理 |
| --- | --- | --- |
| `SCHEMA_INVALID` | JSON 结构或字段值错误 | 修正请求 |
| `CONFIG_UNAVAILABLE` | 请求文件不可读 | 检查文件路径和权限 |
| `INPUT_UNAVAILABLE` / `INPUT_INVALID` | 输入或赝势不可读、为空或格式不满足前提 | 修正文件后先 `preview` |
| `PSEUDO_MISSING` | `ATOMIC_SPECIES` 引用了未提供的赝势 | 补充对应 `.UPF` |
| `JOB_BUSY` / `JOB_EXISTS` | 同 ID 正在提交或已有记录 | 检查记录，新增计算使用新 ID |
| `REMOTE_JOB_EXISTS` | 集群上该 ID 的目录已存在 | 核对远端目录及历史作业 |
| `TRANSPORT_UNAVAILABLE` / `TRANSPORT_TIMEOUT` / `REMOTE_COMMAND_FAILED` | SSH、SCP 或远端命令失败 | 检查连接、权限及集群状态；若发生在提交阶段，先核实 Slurm |
| `SUBMISSION_UNCONFIRMED` | 缺少可信的 Slurm ID | 在集群独立核实后使用 `adopt` |
| `JOB_NOT_FOUND` / `RECORD_INVALID` | 本地记录不存在或损坏 | 检查 `--state-dir` 及备份 |
| `RECORD_IO_FAILED` / `STAGE_IO_FAILED` / `OUTPUT_IO_FAILED` | 本地记录、暂存或取回文件写入失败 | 检查空间与权限；提交阶段先核实 Slurm 状态 |
| `REQUEST_CHANGED` | 请求指纹与提交时不同 | 恢复原文件和配置，或用新 ID 建立新作业 |
| `STATE_INVALID` / `JOB_NOT_TERMINAL` | 当前状态不允许该操作 | 查询状态后执行匹配操作 |
| `OUTPUT_MISSING` | 已完成作业缺少 `output.out` | 检查远端目录和 Slurm 日志 |

`stage_failed` 表示 `sbatch` 尚未调用。`submission_unknown` 表示命令可能已在远端生效，禁止仅凭本地超时再次提交。若 SSH 在文件存在性检查时失败，也返回传输错误，而非把文件视为不存在。
