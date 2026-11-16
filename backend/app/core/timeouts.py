"""全链路超时常量单点声明（2026-10-08 五项加固）。

序关系是硬约束（破坏即连锁故障）：
    GATE_TTL_S > ARQ_JOB_TIMEOUT_S > GATE_QUEUE_TIMEOUT_S > {H3_LOCAL_TOTAL_S, H3W_DEADLINE_S}
    CLEANUP_STALE_MIN*60 > max(H3_LOCAL_TOTAL_S, H3W_DEADLINE_S)

改任何一档必须回来核对本链条（2026-10-08 事故教训：1200/1800/3500/3700
散落 4 文件靠注释互指，改一处忘其他即破序）。
"""

# arq：从入队计时（排队也吃预算）——60 分钟
ARQ_JOB_TIMEOUT_S = 3600

# 视频渠道闸门：排队上限（< arq，闸门先于 arq 放弃并给出明确文案）
GATE_QUEUE_TIMEOUT_S = 3500.0
# 闸门计数泄漏兜底 TTL（> arq job：任务必然先结束，TTL 只兜崩溃）
GATE_TTL_S = 3700

# provider 层总超时（正常任务必在此内终态）
H3_LOCAL_TOTAL_S = 1200.0  # asus 本地（i2v ~5min，720p 上限 ~15min）
H3W_DEADLINE_S = 900  # 算力仓轮询（1024x576-5s 实测 2-4min）
H3W_DOWNLOAD_TIMEOUT_S = 300.0  # 算力仓产物下载（几十 MB 视频，独立于轮询）

# cleanup 收尸阈值（> 所有 provider 总超时：超此仍 generating 的必是孤儿）
CLEANUP_STALE_MIN = 30

# queued 收尸阈值（入队即计时，含排队 + 生成全链路）：必须 > ARQ_JOB_TIMEOUT_S，
# 否则会把仍在排队预算内的正常任务误杀。独立成常量：queued 生命周期比
# generating 长（arq 从入队计时，排队也吃预算），阈值应显著大于上面那档。
# 默认 6 小时（用户决策 2026-11-16）：容忍长队列积压，仅回收真正被遗弃的任务；
# 环境变量 QUEUED_TASK_TIMEOUT_MIN 可覆盖（见 core/config.py）。
CLEANUP_STALE_QUEUED_MIN = 360
