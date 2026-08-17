"""
真实测试节点是否可用: 把候选节点写进一份临时 mihomo 配置, 启动 mihomo 内核,
然后通过它的 REST API 让 mihomo *真的* 用这个节点去请求一个外部 URL (generate_204),
拿到实际往返延迟。这比单纯 TCP connect 靠谱得多 —— 能过滤掉端口开着但协议/密钥
不对、或被墙检测阻断的假活节点。
"""
import asyncio
import json
import subprocess
import time
import yaml
import aiohttp

CONTROLLER = "127.0.0.1:9090"
TEST_URL = "https://www.gstatic.com/generate_204"
TEST_TIMEOUT_MS = 5000
CONCURRENCY = 60
DEAD_DELAY = 0  # mihomo 返回 0 或报错代表不通


def build_mihomo_config(proxies, out_path="mihomo_test_config.yaml"):
    cfg = {
        "mixed-port": 17890,
        "external-controller": CONTROLLER,
        "log-level": "silent",
        "mode": "rule",
        "ipv6": False,
        "proxies": [{k: v for k, v in p.items() if not k.startswith("_")} for p in proxies],
        "proxy-groups": [
            {"name": "collector", "type": "select", "proxies": [p["name"] for p in proxies]}
        ],
        "rules": ["MATCH,collector"],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return out_path


async def _wait_controller_ready(timeout=20):
    async with aiohttp.ClientSession() as session:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                async with session.get(f"http://{CONTROLLER}/version", timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                await asyncio.sleep(0.5)
        return False


async def _test_one(session, sem, name):
    async with sem:
        url = (
            f"http://{CONTROLLER}/proxies/{aiohttp.helpers.quote(name, safe='')}/delay"
            f"?timeout={TEST_TIMEOUT_MS}&url={TEST_URL}"
        )
        try:
            async with session.get(url, timeout=(TEST_TIMEOUT_MS / 1000) + 2) as r:
                if r.status != 200:
                    return name, None
                data = await r.json()
                delay = data.get("delay")
                return name, delay if delay and delay > DEAD_DELAY else None
        except Exception:
            return name, None


async def _test_all(names):
    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks = [_test_one(session, sem, n) for n in names]
        results = await
