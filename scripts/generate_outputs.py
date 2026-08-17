import base64
import json
import os
import time
import requests
import yaml

FLAGS = {
    "US": "🇺🇸", "JP": "🇯🇵", "HK": "🇭🇰", "SG": "🇸🇬", "TW": "🇹🇼", "KR": "🇰🇷",
    "DE": "🇩🇪", "GB": "🇬🇧", "FR": "🇫🇷", "NL": "🇳🇱", "CA": "🇨🇦", "AU": "🇦🇺",
    "RU": "🇷🇺", "IN": "🇮🇳", "BR": "🇧🇷", "TR": "🇹🇷", "IR": "🇮🇷", "UA": "🇺🇦",
}


def geo_lookup(ip_or_host: str, cache: dict):
    if ip_or_host in cache:
        return cache[ip_or_host]
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip_or_host}?fields=countryCode", timeout=4
        )
        cc = r.json().get("countryCode", "")
    except Exception:
        cc = ""
    cache[ip_or_host] = cc
    time.sleep(0.05)  # ip-api 免费额度 ~45 req/min, 简单限速
    return cc


def build_clash_yaml(alive_proxies, path="output/clash.yaml"):
    names = [p["name"] for p in alive_proxies]
    cfg = {
        "mixed-port": 7890,
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "proxies": [{k: v for k, v in p.items() if not k.startswith("_")} for p in alive_proxies],
        "proxy-groups": [
            {
                "name": "♻️ 自动选优",
                "type": "url-test",
                "proxies": names,
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
            },
            {"name": "🚀 手动选择", "type": "select", "proxies": ["♻️ 自动选优"] + names},
            {
                "name": "🔯 故障转移",
                "type": "fallback",
                "proxies": names,
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
            },
        ],
        "rules": [
            "DOMAIN-SUFFIX,cn,DIRECT",
            "GEOIP,CN,DIRECT",
            "MATCH,🚀 手动选择",
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def proxy_to_uri(p: dict):
    t = p["type"]
    name = p["name"]
    if t == "vless":
        params = []
        if p.get("tls"):
            params.append("security=" + ("reality" if p.get("reality-opts") else "tls"))
        if p.get("servername"):
            params.append(f"sni={p['servername']}")
        if p.get("network"):
            params.append(f"type={p['network']}")
        q = "&".join(params)
        return f"vless://{p['uuid']}@{p['server']}:{p['port']}?{q}#{name}"
    if t == "trojan":
        q = f"sni={p['sni']}" if p.get("sni") else ""
        return f"trojan://{p['password']}@{p['server']}:{p['port']}?{q}#{name}"
    if t == "ss":
        cred = base64.urlsafe_b64encode(f"{p['cipher']}:{p['password']}".encode()).decode().rstrip("=")
        return f"ss://{cred}@{p['server']}:{p['port']}#{name}"
    if t == "hysteria2":
        return f"hysteria2://{p['password']}@{p['server']}:{p['port']}#{name}"
    if t == "vmess":
        obj = {
            "v": "2", "ps": name, "add": p["server"], "port": p["port"],
            "id": p["uuid"], "aid": p.get("alterId", 0), "net": p.get("network", "tcp"),
            "type": "none", "tls": "tls" if p.get("tls") else "",
        }
        return "vmess://" + base64.b64encode(json.dumps(obj).encode()).decode()
    return None


def build_v2ray_base64(alive_proxies, path="output/v2ray-base64.txt"):
    uris = [u for u in (proxy_to_uri(p) for p in alive_proxies) if u]
    blob = "\n".join(uris)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(blob.encode()).decode())


def annotate_and_sort(proxies, delays: dict):
    cache = {}
    out = []
    for p in proxies:
        d = delays.get(p["name"])
        if d is None:
            continue
        cc = geo_lookup(p["server"], cache)
        flag = FLAGS.get(cc, "🌐")
        p["_delay"] = d
        p["_country"] = cc
        p["name"] = f"{flag}{cc or '??'} | {d}ms | {p['name'][:24]}"
        out.append(p)
    out.sort(key=lambda x: x["_delay"])

    # 最终保险: 确保节点名全局唯一 (Clash 客户端对重名节点会直接拒绝整份订阅)
    seen = {}
    for p in out:
        base = p["name"]
        n = base
        i = 2
        while n in seen:
            n = f"{base} #{i}"
            i += 1
        seen[n] = True
        p["name"] = n

    return out


def write_stats(alive_proxies, source_stats, path="output/stats.json"):
    stats = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "alive_count": len(alive_proxies),
        "sources": source_stats,
        "protocol_mix": {},
        "country_mix": {},
    }
    for p in alive_proxies:
        stats["protocol_mix"][p["type"]] = stats["protocol_mix"].get(p["type"], 0) + 1
        cc = p.get("_country") or "??"
        stats["country_mix"][cc] = stats["country_mix"].get(cc, 0) + 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats
