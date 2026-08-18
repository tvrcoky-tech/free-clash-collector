"""
真实测试节点是否可用: 把候选节点写进一份临时 mihomo 配置, 启动 mihomo 内核,
然后通过它的 REST API 让 mihomo *真的* 用这个节点去请求一个外部 URL (generate_204),
拿到实际往返延迟。这比单纯 TCP connect 靠谱得多 —— 能过滤掉端口开着但协议/密钥
不对、或被墙检测阻断的假活节点。
"""
import asyncio
import re
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
        results = await asyncio.gather(*tasks)
    return dict(results)


def run_real_test(proxies, mihomo_bin="./mihomo"):
    """
    proxies: list of clash proxy dicts (需要唯一 name)
    返回: {name: delay_ms}  只包含测试成功(真的能连通)的节点

    如果某个节点配置损坏导致 mihomo 直接拒绝加载整份配置(mihomo 的行为是
    一颗老鼠屎坏一锅粥式的), 会自动从报错信息里定位这个坏节点的下标并剔除,
    然后重试, 而不是让全部节点都陪葬。
    """
    seen = {}
    for p in proxies:
        base = p["name"]
        n = base
        i = 2
        while n in seen:
            n = f"{base}-{i}"
            i += 1
        seen[n] = True
        p["name"] = n

    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        if not proxies:
            return {}
        config_path = build_mihomo_config(proxies)
        log_file = open("mihomo_run.log", "w")
        proc = subprocess.Popen(
            [mihomo_bin, "-f", config_path, "-d", "."],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            ready = asyncio.run(_wait_controller_ready())
            if ready:
                names = [p["name"] for p in proxies]
                print(f"开始对 {len(names)} 个候选节点做真实连通性测试 (并发 {CONCURRENCY}) ...")
                results = asyncio.run(_test_all(names))
                alive = {k: v for k, v in results.items() if v is not None}
                print(f"真实测试通过: {len(alive)} / {len(names)}")
                return alive

            log_file.close()
            log_text = ""
            try:
                with open("mihomo_run.log") as f:
                    log_text = f.read()
            except Exception:
                pass

            m = re.search(r"proxy (\d+):", log_text)
            if m and attempt < max_attempts:
                bad_index = int(m.group(1))
                if 0 <= bad_index < len(proxies):
                    bad_name = proxies[bad_index].get("name", "?")
                    print(f"第 {attempt} 次尝试: 配置里第 {bad_index} 个节点({bad_name})解析失败, 剔除后重试")
                    del proxies[bad_index]
                    continue
            print(f"mihomo 内核未能启动 (第 {attempt} 次尝试后放弃), 日志:")
            print(log_text[-2000:])
            return {}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            if not log_file.closed:
                log_file.close()
    return {}
