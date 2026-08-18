"""
真实测试节点是否可用: 把候选节点写进一份临时 mihomo 配置, 启动 mihomo 内核,
然后通过它的 REST API 让 mihomo *真的* 用这个节点去请求一个外部 URL (generate_204),
拿到实际往返延迟。这比单纯 TCP connect 靠谱得多 —— 能过滤掉端口开着但协议/密钥
不对、或被墙检测阻断的假活节点。

针对 Gemini 可用性做了两层测试:
  第一层(快, 可并发): 用 mihomo 内部测速接口打 gemini.google.com, 只看 HTTP 状态码。
  第二层(慢, 只能排队一个个测): 真正把网页内容抓下来, 检查里面有没有"该地区不可用"
  这类字样 —— 因为 Gemini 被地区限制时经常还是返回 200 状态码, 只是页面内容提示
  不可用, 光看状态码测不出来, 必须读内容才知道。第二层只对第一层筛出来的候选做,
  并且限定总耗时, 避免拖垮整个 workflow。
"""
import asyncio
import re
import subprocess
import time
import yaml
import aiohttp

CONTROLLER = "127.0.0.1:9090"
MIXED_PORT = 17890
TEST_URL = "https://www.gstatic.com/generate_204"
GEMINI_TEST_URL = "https://gemini.google.com/"
TEST_TIMEOUT_MS = 5000
CONCURRENCY = 60
DEAD_DELAY = 0  # mihomo 返回 0 或报错代表不通

# 深度检测 Gemini 页面内容时, 出现这些字样说明是"地区不可用"的软拒绝
BLOCK_PHRASES = [
    "not available in your country",
    "isn't available in your country",
    "not available in your region",
    "not supported in your country",
    "您所在的地区暂不支持",
    "在您所在的国家/地区不可用",
    "该地区不可用",
]
DEEP_CHECK_MAX_SECONDS = 360  # 深度检测最多花这么久, 超时就停(避免拖垮整个 workflow)
DEEP_CHECK_MAX_COUNT = 200    # 深度检测最多测这么多个(按延迟从低到高优先测)


def build_mihomo_config(proxies, out_path="mihomo_test_config.yaml"):
    cfg = {
        "mixed-port": MIXED_PORT,
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


async def _deep_check_gemini(session, name, debug_counter):
    """把这个节点设为当前出口, 真的抓一次 Gemini 首页内容, 看有没有地区限制的字样"""
    try:
        async with session.put(
            f"http://{CONTROLLER}/proxies/collector",
            json={"name": name},
            timeout=5,
        ) as r:
            if r.status not in (200, 204):
                if debug_counter[0] < 5:
                    debug_counter[0] += 1
                    print(f"  [调试] 切换节点失败: status={r.status}")
                return False
    except Exception as e:
        if debug_counter[0] < 5:
            debug_counter[0] += 1
            print(f"  [调试] 切换节点异常: {type(e).__name__}: {e}")
        return False

    try:
        async with session.get(
            GEMINI_TEST_URL,
            proxy=f"http://127.0.0.1:{MIXED_PORT}",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124"},
        ) as r:
            if r.status >= 400:
                if debug_counter[0] < 5:
                    debug_counter[0] += 1
                    print(f"  [调试] 抓取页面状态码异常: {r.status}")
                return False
            text = (await r.text(errors="ignore"))[:20000].lower()
            for phrase in BLOCK_PHRASES:
                if phrase.lower() in text:
                    return False
            return True
    except Exception as e:
        if debug_counter[0] < 5:
            debug_counter[0] += 1
            print(f"  [调试] 抓取页面异常: {type(e).__name__}: {e}")
        return False


async def _deep_check_all(names_sorted_by_delay):
    verified = set()
    deadline = time.time() + DEEP_CHECK_MAX_SECONDS
    candidates = names_sorted_by_delay[:DEEP_CHECK_MAX_COUNT]
    debug_counter = [0]
    async with aiohttp.ClientSession() as session:
        for i, name in enumerate(candidates):
            if time.time() > deadline:
                print(f"深度检测超时预算, 已测 {i}/{len(candidates)} 个, 停止剩余检测")
                break
            ok = await _deep_check_gemini(session, name, debug_counter)
            if ok:
                verified.add(name)
    return verified


def run_real_test(proxies, mihomo_bin="./mihomo"):
    """
    proxies: list of clash proxy dicts (需要唯一 name)
    返回: (delays, gemini_ok_names)
      delays: {name: delay_ms}  只包含基础连通性测试通过(真的能连通)的节点
      gemini_ok_names: set, 经过深度内容检测确认能正常使用 Gemini 的节点名字集合

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
                    print(f"第一层: 对 {len(alive)} 个存活节点快速筛查 Gemini 状态码 ...")
                    gemini_results = asyncio.run(_test_all(list(alive.keys()), GEMINI_TEST_URL))
                    gemini_candidates = [k for k, v in gemini_results.items() if v is not None]
                    print(f"状态码正常的候选: {len(gemini_candidates)} / {len(alive)}")

                    if gemini_candidates:
                        # 按延迟从低到高排, 优先深度验证更快的节点
                        gemini_candidates.sort(key=lambda n: alive.get(n, 999999))
                        n_test = min(len(gemini_candidates), DEEP_CHECK_MAX_COUNT)
                        print(f"第二层: 对其中 {n_test} 个抓取网页内容, 核实有没有地区限制提示 ...")
                        gemini_ok = asyncio.run(_deep_check_all(gemini_candidates))
                        print(f"深度核实后真正可用: {len(gemini_ok)} / {n_test}")

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
