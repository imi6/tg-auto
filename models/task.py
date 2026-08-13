"""
批量任务相关的状态常量
供任务调度层与各服务层共用，避免两边字符串不一致
"""

# 单条操作的执行结果
STATUS_SUCCESS = 'success'
STATUS_ALREADY = 'already'
STATUS_PENDING = 'pending'
STATUS_FAILED = 'failed'
STATUS_FLOOD = 'flood'

# 计入任务汇总的结果状态（flood 会被重试或转成 failed，不单独统计）
SUMMARY_STATUSES = (STATUS_SUCCESS, STATUS_ALREADY, STATUS_PENDING, STATUS_FAILED)

# 任务与账号维度的运行状态
TASK_RUNNING = 'running'
TASK_COMPLETED = 'completed'
TASK_CANCELLED = 'cancelled'
TASK_INTERRUPTED = 'interrupted'

ACCOUNT_PENDING = 'pending'
ACCOUNT_RUNNING = 'running'
ACCOUNT_COMPLETED = 'completed'
ACCOUNT_CANCELLED = 'cancelled'
ACCOUNT_ABORTED = 'aborted'
ACCOUNT_FAILED = 'failed'
