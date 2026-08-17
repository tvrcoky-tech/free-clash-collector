import sys
import yaml
import requests
from parsers import parse_v2ray_base64_blob, parse_clash_yaml, parse_uri_line, clean_proxy

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; sub-fetcher/1.0)"}
TIMEOUT = 15


def fetch_one(name: str, url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f"  [WARN] {name}: 拉取失败 ({e})", file=sys.stderr)
        return []

    proxies = []
    stripped = text.strip()

    # 1) clash yaml
    if "proxies:" in text or stripped.startswith("proxies"):
        proxies = parse_clash_yaml(text)
        if proxies:
            for p in proxies:
                p["_source"] = name
            return [x for x in (clean_proxy(p) for p in proxies) if x]

    # 2) 明文,每行一个 uri
    if any(s in text for s in ("vmess://", "vless://", "trojan://", "ss://", "hysteria2://")):
        for line in stripped.splitlines():
            p = parse_uri_line(line)
            if p:
                p["_source"] = name
                proxies.append(p)
        return [x for x in (clean_proxy(p) for p in proxies) if x]

    # 3) 整块 base64
    proxies = parse_v2ray_base64_blob(stripped)
    for p in proxies:
        p["_source"] = name
    return [x for x in (clean_proxy(p) for p in proxies) if x]


def dedupe(proxies):
    seen = set()
    out = []
    for p in proxies:
        key = (p["type"], p["server"], p["port"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def fetch_all(sources_path="sources.yaml"):
    with open(sources_path, encoding="utf-8") as f:
        sources = yaml.safe_load(f)["sources"]

    all_proxies = []
    stats = {}
    for src in sources:
        name, url = src["name"], src["url"]
        got = fetch_one(name, url)
        stats[name] = len(got)
        print(f"  {name}: {len(got)} 个节点")
        all_proxies.extend(got)

    deduped = dedupe(all_proxies)
    print(f"合计抓取 {len(all_proxies)} 个, 去重后 {len(deduped)} 个")
    return deduped, stats


if __name__ == "__main__":
    fetch_all()
