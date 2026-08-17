# free-vpn-subscriptions (self-hosted)

自动聚合公开的免费代理节点，用 [mihomo](https://github.com/MetaCubeX/mihomo)（Clash.Meta 内核）
**真实建立连接**测试是否存活，每小时刷新一次，输出可以直接导入 Clash / v2rayN 等客户端的订阅文件。

## 用法

把这个仓库 push 到你自己的 GitHub 账号（已经是空仓库的话直接把本目录内容提交上去），
然后在仓库 `Settings → Actions → General → Workflow permissions` 里勾选
**"Read and write permissions"**（否则 Actions 没权限 commit 结果回仓库）。

之后订阅链接就是（把 `YOUR_NAME/YOUR_REPO` 换成你自己的）：

```
Clash:   https://raw.githubusercontent.com/YOUR_NAME/YOUR_REPO/main/output/clash.yaml
v2ray:   https://raw.githubusercontent.com/YOUR_NAME/YOUR_REPO/main/output/v2ray-base64.txt
```

第一次需要手动跑一次 Actions（`Actions → Update VPN Subscriptions → Run workflow`），
之后每小时自动跑。

## 为什么比同类仓库更严格 / 更省心

1. **真实连接测试，不是 TCP ping**：脚本会真的启动 mihomo 内核，把每个候选节点跑起来，
   通过它的 REST API 让节点真实发出一次 HTTP 请求（`generate_204`），只有请求成功、
   拿到实际往返延迟的节点才会进最终订阅——端口开着但协议/密钥不对、或者已经被墙检测
   阻断的"假活"节点会被过滤掉。
2. **去重按 `(协议, 服务器, 端口)` 三元组**，不同来源抓到同一台机器不会重复出现。
3. **按延迟自动排序 + 自动分组**：生成的 `clash.yaml` 自带 `♻️ 自动选优`（url-test）、
   `🚀 手动选择`、`🔯 故障转移` 三个策略组，不用自己配。
4. **节点名自动加国旗+延迟**，一眼看出哪个节点在哪、快不快。
5. **来源可插拔**：改 `sources.yaml` 加一行就能接入新的公开订阅源，不用碰代码。

## 目录结构

```
sources.yaml          # 订阅源列表,自己加/删
scripts/
  parsers.py           # 各协议 URI / clash yaml 解析器
  fetch_sources.py      # 拉取 + 解析 + 去重
  mihomo_test.py         # 用 mihomo 内核做真实连通性测试
  generate_outputs.py     # 打标签、排序、生成 clash.yaml / v2ray-base64.txt
  pipeline.py               # 总入口
output/                # 生成的订阅文件(由 Actions 自动提交,不用手动改)
.github/workflows/update.yml  # 定时任务
```

## 本地调试

```bash
pip install -r requirements.txt
curl -L $(curl -s https://api.github.com/repos/MetaCubeX/mihomo/releases/latest \
  | grep browser_download_url | grep linux-amd64 | grep -v go120 | grep -v sha256 \
  | head -1 | cut -d '"' -f4) -o mihomo.gz
gunzip mihomo.gz && mv mihomo* mihomo && chmod +x mihomo
cd scripts && python pipeline.py
```

## 免责声明

本项目只是抓取、测试、重新打包**第三方公开发布**的免费节点信息，不运营任何代理服务器，
不保证节点的可用性、速度或安全性；节点由发布者自行控制，请勿用其传输敏感信息。
仅供学习/个人使用，请遵守所在地法律法规。
