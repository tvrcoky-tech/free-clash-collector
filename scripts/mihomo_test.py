"""
真实测试节点是否可用: 把候选节点写进一份临时 mihomo 配置, 启动 mihomo 内核,
然后通过它的 REST API 让 mihomo *真的* 用这个节点去请求一个外部 URL (generate_204),
拿到实际往返延迟。这比单纯 TCP connect 靠谱得多 —— 能过滤掉端口开着但协议/密钥
不对、或被墙检测阻断的假活节点。

第二阶段还会额外测一次: 这个节点能不能正常打开 Gemini。很多免费节点的出口 IP
已经被 Google 标记为"机房/代理 IP", 能上网但打开 Gemini 会被拦截返回错误页,
一般的连通性测试测不出这个, 必须专门针对 Gemini 的地址单独测一次才知道。
"""
import asyncio
import re
import subprocess
import time
import yaml
import aiohttp

CONTROLLER = "127.0.0.1:9090"
TEST_URL = "https://www.gstatic.com/generate_204"
GEMINI_TEST_URL = "https://gemini.google.com/"
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


async def _test_one(session, sem, name, target_url):
    async with sem:
        url = (
            f"http://{CONTROLLER}/proxies/{aiohttp.helpers.quote(name, safe='')}/delay"
            f"?timeout={TEST_TIMEOUT_MS}&url={target_url}"
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


async def _test_all(names, target_url):
    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks = [_test_one(session, sem, n, target_url) for n in names]
        results = await asyncio.gather(*tasks)
    return dict(results)


def run_real_test(proxies, mihomo_bin="./mihomo"):
    """
    proxies: list of clash proxy dicts (需要唯一 name)
    返回: (delays, gemini_ok_names)
      delays: {name: delay_ms}  只包含基础连通性测试通过(真的能连通)的节点
      gemini_ok_names: set, 这些节点里面能正常打开 Gemini 的名字集合

    如果某个节点配置损坏导致 mihomo 直接拒绝加载整份配置(mihomo 的行为是
    一颗老鼠屎坏一锅粥式的), 会自动从报错信息里定位这个坏节点的下标并剔除,
    然后重试, 而不是让全部节点都陪葬。
    """
    # 保证 name 唯一, 否则 mihomo 会去重导致漏测
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
            return {}, set()
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
                results = asyncio.run(_test_all(names, TEST_URL))
                alive = {k: v for k, v in results.items() if v is not None}
                print(f"真实测试通过: {len(alive)} / {len(names)}")

                gemini_ok = set()
                if alive:
                    print(f"对 {len(alive)} 个存活节点额外测试 Gemini 可用性 ...")
                    gemini_results = asyncio.run(_test_all(list(alive.keys()), GEMINI_TEST_URL))
                    gemini_ok = {k for k, v in gemini_results.items() if v is not None}
                    print(f"其中可正常访问 Gemini: {len(gemini_ok)} / {len(alive)}")

                return alive, gemini_ok

            # 没启动起来: 看是不是某个节点配置有问题导致 mihomo 直接拒绝加载
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
            return {}, set()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            if not log_file.closed:
                log_file.close()
    return {}, set()
