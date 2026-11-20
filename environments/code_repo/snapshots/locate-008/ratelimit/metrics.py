"""准入结果的可观测性输出。

#4455（2025-11-20）只改了输出标签，不在准入路径上。
"""

from ratelimit.policy import admit


def render(requests):
    return [{"scope": req["tenant"], "admitted": ok, "reason": reason}
            for req, ok, reason in admit(requests)]


def summarize(requests):
    rows = render(requests)
    return {
        "total": len(rows),
        "admitted": sum(1 for row in rows if row["admitted"]),
        "denied_by_quota": sum(1 for row in rows if row["reason"] == "quota"),
        "denied_by_bucket": sum(1 for row in rows if row["reason"] == "bucket"),
    }
