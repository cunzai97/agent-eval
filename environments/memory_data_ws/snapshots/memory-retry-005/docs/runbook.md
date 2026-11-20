# 月末结算运行手册（现行版本）

1. 结算基准日 = 当月的**最后一个结算工作日**：该日必须是周一至周五，且不出现在
   `config/holidays.md` 里（该文件只登记落在工作日中的额外非结算日，周末不必登记）。
   自然月的最后一天不是基准日。
2. 基准日必须写入 `config/as_of`（单独一行 ISO 日期，例如 `2025-03-14`）。
   脚本 `etl/settle.py` 在缺少该文件时会直接报错退出，不会自行猜测。
3. 口径（三步按此顺序执行）：先剔除 `status=cancelled` 的记录；再只保留
   `booked_date` **不晚于**基准日的记录；最后对同一 `trade_id` 只取 `as_of_seq`
   最大的一条修订。
4. 输出 `out/settlement.csv`，列 `booked_date,net_amount,trade_count`，按日期升序，
   金额两位小数。
