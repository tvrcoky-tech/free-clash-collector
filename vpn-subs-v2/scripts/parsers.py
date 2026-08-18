"""
把各种订阅格式统一转换成 Clash/mihomo 的 proxy dict 结构。
支持: vmess://  vless://  trojan://  ss://  hysteria2:// (hy2://)  以及 clash yaml (proxies: [...])
"""
import base64
import json
import re
from urllib.parse import urlparse, parse_qs, unquote


def _b64pad(s: str) -> str:
    s = s.strip()
    return s + "=" * (-len(s) % 4)


def _safe_b64decode(s: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(_b64pad(s))
    except Exception:
        return base64.b64decode(_b64pad(s.replace("-", "+").replace("_", "/")))


def parse_vmess(uri: str):
    try:
        raw = uri[len("vmess://"):]
        data = json.loads(_safe_b64decode(raw).decode("utf-8", "ignore"))
        proxy = {
            "name": data.get("ps") or f"vmess-{data.get('add')}",
            "type": "vmess",
            "server": data["add"],
            "port": int(data["port"]),
            "uuid": data["id"],
            "alterId": int(data.get("aid", 0)),
            "cipher": data.get("scy", "auto"),
            "udp": True,
        }
        net = data.get("net", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {
                "path": data.get("path", "/"),
                "headers": {"Host": data.get("host", "")} if data.get("host") else {},
            }
        elif net == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {"grpc-service-name": data.get("path", "")}
        if str(data.get("tls", "")).lower() in ("tls", "1", "true"):
            proxy["tls"] = True
            if data.get("sni") or data.get("host"):
                proxy["servername"] = data.get("sni") or data.get("host")
        return proxy
    except Exception:
        return None


def _parse_uri_generic(uri: str, scheme: str, field_name: str):
    """通用解析 scheme://field@host:port?query#name"""
    try:
        body = uri[len(scheme) + 3:]
        if "#" in body:
            body, name = body.split("#", 1)
            name = unquote(name)
        else:
            name = None
        if "?" in body:
            body, query = body.split("?", 1)
            params = parse_qs(query)
        else:
            params = {}
        cred, hostport = body.rsplit("@", 1)
        host, port = hostport.rsplit(":", 1)
        return unquote(cred), host, int(port), params, name
    except Exception:
        return None


def parse_vless(uri: str):
    parsed = _parse_uri_generic(uri, "vless", "uuid")
    if not parsed:
        return None
    uuid, host, port, params, name = parsed
    g = lambda k, d=None: params.get(k, [d])[0]
    proxy = {
        "name": name or f"vless-{host}",
        "type": "vless",
        "server": host,
        "port": port,
        "uuid": uuid,
        "udp": True,
        "tls": g("security") in ("tls", "reality"),
        "flow": g("flow", "") or "",
    }
    if g("sni"):
        proxy["servername"] = g("sni")
    net = g("type", "tcp")
    if net == "ws":
        proxy["network"] = "ws"
        proxy["ws-opts"] = {"path": g("path", "/"), "headers": {"Host": g("host", "")} if g("host") else {}}
    elif net == "grpc":
        proxy["network"] = "grpc"
        proxy["grpc-opts"] = {"grpc-service-name": g("serviceName", "")}
    if g("security") == "reality":
        proxy["reality-opts"] = {"public-key": g("pbk", ""), "short-id": g("sid", "")}
        proxy["client-fingerprint"] = g("fp", "chrome")
    if not proxy.get("flow"):
        proxy.pop("flow", None)
    return proxy


def parse_trojan(uri: str):
    parsed = _parse_uri_generic(uri, "trojan", "password")
    if not parsed:
        return None
    password, host, port, params, name = parsed
    g = lambda k, d=None: params.get(k, [d])[0]
    proxy = {
        "name": name or f"trojan-{host}",
        "type": "trojan",
        "server": host,
        "port": port,
        "password": password,
        "udp": True,
    }
    if g("sni"):
        proxy["sni"] = g("sni")
    if g("type") == "ws":
        proxy["network"] = "ws"
        proxy["ws-opts"] = {"path": g("path", "/")}
    return proxy


def parse_hysteria2(uri: str):
    scheme = "hysteria2" if uri.startswith("hysteria2://") else "hy2"
    parsed = _parse_uri_generic(uri, scheme, "password")
    if not parsed:
        return None
    password, host, port, params, name = parsed
    g = lambda k, d=None: params.get(k, [d])[0]
    proxy = {
        "name": name or f"hy2-{host}",
        "type": "hysteria2",
        "server": host,
        "port": port,
        "password": password,
        "udp": True,
        "skip-cert-verify": g("insecure") in ("1", "true", "True"),
    }
    if g("sni"):
        proxy["sni"] = g("sni")
    return proxy


def parse_ss(uri: str):
    try:
        body = uri[len("ss://"):]
        name = None
        if "#" in body:
            body, name = body.split("#", 1)
            name = unquote(name)
        if "@" in body:
            # SIP002: ss://base64(method:pwd)@host:port  OR  ss://method:pwd@host:port
            cred, hostport = body.rsplit("@", 1)
            try:
                cred = _safe_b64decode(cred).decode()
            except Exception:
                pass
            method, password = cred.split(":", 1)
            host, port = hostport.split(":", 1)
        else:
            # legacy: ss://base64(method:pwd@host:port)
            decoded = _safe_b64decode(body).decode()
            cred, hostport = decoded.rsplit("@", 1)
            method, password = cred.split(":", 1)
            host, port = hostport.split(":", 1)
        return {
            "name": name or f"ss-{host}",
            "type": "ss",
            "server": host,
            "port": int(port),
            "cipher": method,
            "password": password,
            "udp": True,
        }
    except Exception:
        return None


SCHEME_PARSERS = {
    "vmess://": parse_vmess,
    "vless://": parse_vless,
    "trojan://": parse_trojan,
    "hysteria2://": parse_hysteria2,
    "hy2://": parse_hysteria2,
    "ss://": parse_ss,
}


def parse_uri_line(line: str):
    line = line.strip()
    for scheme, fn in SCHEME_PARSERS.items():
        if line.startswith(scheme):
            return fn(line)
    return None


def parse_v2ray_base64_blob(text: str):
    """整块 base64 编码的订阅内容,解码后每行一个 uri"""
    proxies = []
    try:
        decoded = _safe_b64decode(text).decode("utf-8", "ignore")
    except Exception:
        decoded = text  # 可能本来就是明文
    for line in decoded.splitlines():
        line = line.strip()
        if not line:
            continue
        p = parse_uri_line(line)
        if p:
            proxies.append(p)
    return proxies


def parse_clash_yaml(text: str):
    import yaml
    try:
        data = yaml.safe_load(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    proxies = data.get("proxies") or []
    out = []
    for p in proxies:
        if isinstance(p, dict) and p.get("server") and p.get("port"):
            out.append(p)
    return out


ALLOWED_TYPES = {"ss", "vmess", "vless", "trojan", "hysteria2"}

VALID_SS_CIPHERS = {
    "aes-128-gcm", "aes-192-gcm", "aes-256-gcm",
    "aes-128-cfb", "aes-192-cfb", "aes-256-cfb",
    "aes-128-ctr", "aes-192-ctr", "aes-256-ctr",
    "chacha20-ietf", "chacha20", "xchacha20",
    "chacha20-ietf-poly1305", "xchacha20-ietf-poly1305",
    "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
    "rc4-md5", "none",
}


def clean_proxy(p: dict):
    """标准化字段,过滤掉 mihomo 不支持的类型, 以及格式损坏的节点
    (脏数据会导致 mihomo 直接拒绝加载整份配置, 必须在这里挡住)"""
    if not p or p.get("type") not in ALLOWED_TYPES:
        return None
    if not p.get("server") or not p.get("port"):
        return None
    try:
        port = int(p["port"])
        if not (0 < port < 65536):
            return None
        p["port"] = port
    except Exception:
        return None

    if p["type"] == "ss":
        cipher = str(p.get("cipher", "")).lower()
        password = str(p.get("password", ""))
        if cipher not in VALID_SS_CIPHERS:
            return None
        if not password or any(c in password for c in ("@", " ", "\n", "\t")):
            return None
    elif p["type"] in ("vmess", "vless"):
        uuid = str(p.get("uuid", ""))
        if not uuid or any(c in uuid for c in (" ", "\n", "\t", "@")):
            return None
    elif p["type"] in ("trojan", "hysteria2"):
        password = str(p.get("password", ""))
        if not password or any(c in password for c in (" ", "\n", "\t")):
            return None

    p["name"] = str(p.get("name") or f"{p['type']}-{p['server']}")[:60]
    return p
