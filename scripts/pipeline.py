import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from fetch_sources import fetch_all
from mihomo_test import run_real_test
from generate_outputs import (
    annotate_and_sort,
    build_clash_yaml,
    build_v2ray_base64,
    write_stats,
    top_up_with_previous,
)

# 每轮候选节点上限, 避免免费 Actions runner 跑太久 (可按需调整)
MAX_CANDIDATES = 1500
# 只保留延迟在这个范围内的节点 (毫秒)
MAX_DELAY_MS = 4000
# 保底数量: 这一轮真实测出来的存活节点如果低于这个数, 就从上一次成功发布的
# 订阅里借几个补上, 避免某一轮抽风(源集体挂掉/网络异常)导致订阅几乎是空的。
# 够了就完全不触发, 不影响正常情况。
MIN_ALIVE_GUARANTEE = 5


def main():
    print("== 第 1 步: 抓取所有订阅源 ==")
    proxies, source_stats = fetch_all(os.path.join(os.path.dirname(__file__), "..", "sources.yaml"))

    if len(proxies) > MAX_CANDIDATES:
        print(f"候选节点 {len(proxies)} 超过上限 {MAX_CANDIDATES}, 随机抽样")
        import random
        random.shuffle(proxies)
        proxies = proxies[:MAX_CANDIDATES]

    if not proxies:
        print("没有抓到任何节点, 退出")
        return

    print("== 第 2 步: 用 mihomo 内核做真实连通性测试 ==")
    mihomo_bin = os.path.join(os.path.dirname(__file__), "..", "mihomo")
    delays = run_real_test(proxies, mihomo_bin=mihomo_bin)
    delays = {k: v for k, v in delays.items() if v <= MAX_DELAY_MS}

    print("== 第 3 步: 标注地理位置, 排序, 生成输出 ==")
    alive = annotate_and_sort(proxies, delays)
    print(f"本轮真实测试存活: {len(alive)}")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "output")
    clash_path = os.path.join(out_dir, "clash.yaml")

    if len(alive) < MIN_ALIVE_GUARANTEE:
        alive, borrowed_count = top_up_with_previous(alive, MIN_ALIVE_GUARANTEE, clash_path)
        if borrowed_count:
            print(
                f"本轮存活不足 {MIN_ALIVE_GUARANTEE} 个, "
                f"已从上一次发布的订阅里借了 {borrowed_count} 个补上(标记为 🕰️沿用)"
            )
        else:
            print(f"本轮存活不足 {MIN_ALIVE_GUARANTEE} 个, 但没有可借用的历史订阅, 保持原样")

    print(f"最终可用节点: {len(alive)}")

    build_clash_yaml(alive, clash_path)
    build_v2ray_base64(alive, os.path.join(out_dir, "v2ray-base64.txt"))
    stats = write_stats(alive, source_stats, os.path.join(out_dir, "stats.json"))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
