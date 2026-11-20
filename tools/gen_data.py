"""生成 data_ws 快照（data-pipeline 4 个新任务）的数据集。"""
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "environments/data_ws/snapshots")


def gen(name, files):
    d = os.path.join(ROOT, name)
    for rel, c in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(c)
    print("gen", name)


# data-002: 缺失值 + 脏数据清洗，按 salary 降序
gen("data-002", {
    "employees.csv":
        "id,name,dept,salary\n"
        "1,Alice,eng,100\n"
        "2,Bob,eng,\n"
        "3,Cara,sales,50\n"
        "4,David,eng,-5\n"
        "5,Eve,sales,80\n"
        "6,Frank,eng,200\n"
        "7,Gina,sales,\n"
        "8,Hank,eng,150\n",
})

# data-003: 多表 join（order 关联 user 的 city，无匹配填 unknown）
gen("data-003", {
    "users.csv":
        "user_id,city\n"
        "u1,NYC\n"
        "u2,LA\n"
        "u3,Chicago\n",
    "orders.csv":
        "order_id,user_id,amount\n"
        "o1,u1,10\n"
        "o2,u2,20\n"
        "o3,u9,5\n"
        "o4,u3,30\n",
})

# data-004: 时序环比
gen("data-004", {
    "monthly.csv":
        "month,value\n"
        "2025-01,100\n"
        "2025-02,120\n"
        "2025-03,90\n"
        "2025-04,110\n",
})

# data-005: 分组 TopN（每个 region 取 amount 最大的 product）
gen("data-005", {
    "sales.csv":
        "region,product,amount\n"
        "east,A,100\n"
        "east,B,200\n"
        "east,C,50\n"
        "west,X,300\n"
        "west,Y,150\n",
})
