# 跨来源聚合与交付

先固定每条记录的业务粒度、来源与唯一标识。关联前检查键是否唯一；多对多关联可能把金额倍增，不能默认 merge 后求和。避免把“广告名称”等显示字段自动当成素材唯一 ID；用户要求按名称归并时可遵循，但保留来源 ID 和重名合并说明。

币种、日期时区、归因窗口和事件类型不一致时分别统计，除非有明确转换规则。不同事件别名可能重叠，不能把多种 Purchase 事件一股脑相加；缺失、零、失败和未成熟数据分别处理。Reach/独立用户等不可加指标不能简单求和后声称去重结果。

完整数据先去重、按用户确认维度汇总，再计算比率并排序。CPA=总消耗/总 Purchase；分母为零输出空值或明确无定义。不要平均各行 CPA，不在全局聚合前把各账户截取 Top N。

下面用合成数据演示跨来源汇总。真实使用前先把各来源字段映射为共同粒度，并确认金额币种与事件口径可比。只读本地文件时 api_scopes 应为空。

```python
from decimal import Decimal

def aggregate(rows):
    totals, seen = {}, set()
    for r in rows:
        identity = (r["source"], r["record_id"])
        if identity in seen:
            raise ValueError("重复记录，先核对分页/关联后去重")
        seen.add(identity)
        key = (r["group"], r["currency"])
        item = totals.setdefault(key, {"spend": Decimal("0"), "purchases": 0})
        item["spend"] += Decimal(str(r["spend"]))
        item["purchases"] += int(r["purchases"])
    return [{"group": group, "currency": currency, "spend": str(v["spend"]),
             "purchases": v["purchases"],
             "cpa": str(v["spend"] / v["purchases"]) if v["purchases"] else None}
            for (group, currency), v in totals.items()]
```

例子只演示整数事件计数，若真实归因数据为小数则保留精度，不可直接套 int 截断。真实唯一键通常还包括账户、日期、拆分维度，必须按实际粒度确定。

交付前核对：预期目标清单发现完整；每个目标已完成全部相关分页或明确列为失败/未完成；无重复查询导致的重复记录；所有转换和计算有依据。代码状态 succeeded 本身不足以证明这些条件。

只向模型返回必要摘要、覆盖数、异常项和文件名，不反复输出全部明细。code_file 发送本轮已经生成的文件。若缺任何范围，文件名和摘要明确标记 partial/部分，不给未完整数据冠以“完整 Top 100”；旧文件只能作为标明日期和范围的旧结果引用。
