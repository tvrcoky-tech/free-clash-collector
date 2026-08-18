import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from fetch_sources import fetch_all
from mihomo_test import run_real_test
from generate_outputs import annotate_and_sort, build_clash_yaml, build_v2ray_base64, write_stats

MAX_CANDIDATES = 1500
MAX_DELAY_MS = 4000


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
    print(f"最终可用节点: {len(alive)}")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "output")
    build_clash_yaml(alive, os.path.join(out_dir, "clash.yaml"))
    build_v2ray_base64(alive, os.path.join(out_dir, "v2ray-base64.txt"))
    stats = write_stats(alive, source_stats, os.path.join(out_dir, "stats.json"))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
