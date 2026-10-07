# KDDI Marzban 扩展与部署存档

这是官方 [Gozargah/Marzban](https://github.com/Gozargah/Marzban) 的部署 fork。目的：把实际使用的本体补丁、配套源码、配置结构和踩坑留在 GitHub，而不是只留在服务器与聊天历史里。上游版权和根目录 AGPL-3.0 许可证保留。

## 固定基线与真实改动

- 基线：`v0.8.4`，commit `7f396db3e703d71a28060bc9ce4a532ec64cb1f4`；工作分支 `kddi-v0.8.4`。
- 运行方式：官方镜像 `gozargah/marzban@sha256:8e422c21997e5d2e3fa231eeff73c0a19193c20fc02fa4958e9368abb9623b8d` + 只读挂载修改后的根目录 `main.py`。
- **Marzban 本体只有一行修改**：`uvicorn.run(..., access_log=False, ...)`，避免订阅令牌路径进入访问日志。额度统计、首次流量激活仍使用 Marzban 原生机制，并未重写核心。
- 其余代码均位于本目录：券CLI、会话断流守护器、Pages分享入口、不透明链接发行、私人不限额账户、轻量出口IP记录。
- 当前仓库只存源码与脱敏模板。部署实例仍在其原目录，本次存档不会重启服务或把运行实例切到 fork。

## 目录

- `coupons/coupons.py`：1GB十进制、首次真实流量后7天的库存券；未使用不计时，发券失败保留issuance journal。
- `coupons/session_guard.py`：Xray FIFO的用户/客户端四元组映射，额度或到期时定向`ss -K`断流、撤销与删除；解析/API故障时仅该Reality端口fail-closed。
- `coupons/share_links.py`：SQLite幂等保存随机分享码，写入专用CF KV并精确读回；普通guard/API轮询不增加KV写入。
- `coupons/personal.py`：单独的私人自用账户创建工具；不是发卡券。
- `share/site/_worker.js`：Cloudflare Pages advanced-mode Worker，服务端直接返回订阅而不重定向。
- `share/deploy_pages.py`：指定专用Pages/KV项目的部署与读回验证，默认不执行写操作，需`--apply`。
- `monitor/kddi_egress_watch.py`：每次独立连接经专用SOCKS采样公网出口；SQLite持久记录、成功静默、不调用模型。
- `deploy/`：compose、Xray、Caddy、systemd与私密配置的**占位示例**，不能原样运行。
- `scripts/check.sh`：离线回归；GitHub Actions自动执行，不访问生产实例，不需要生产凭据。

仓库快照对生产源码仅作必要便携化：guard入口IP改从`/etc/kddi-coupon-guard.env`读取；分享模块改为相对源码导入；CF凭据路径支持环境覆盖。Pages部署脚本去除了旧实例专用备份/域名和已退休的额外源站密钥门禁。生产路径保留在默认值/部署说明中，但生产IP、域名、身份和凭据不上传。

## 架构与限制

客户端 → VPS VLESS Reality/Vision TCP → 固定SS2022 TCP落地 → KDDI出口。落地只允许VPS来源；两段SS入口如果同时保留，使用不同密钥。NAT不提供UDP，不承诺QUIC/HTTP3。落地需DNS解析后拦截私网/回环/保留地址，别把代理变成内网跳板。

管理面仅`127.0.0.1:18002`，通过SSH隧道访问。公开Caddy仅转发有效订阅令牌及客户端格式路径，后台/API/docs返回404。Pages/KV只隐藏原订阅域名，**不能隐藏节点中的VPS IP**；分享码与原订阅令牌都是使用凭据，不要公开。

Reality目标由部署方自行核验：优先入口同ASN、TLS1.3/h2、证书与真实链路合格的站点，排查无鉴权回落流量滥用；连通或同ASN都不等于免封。不要盲用热门Apple目标。示例中的SNI、密钥、短ID、入口/落地地址都需私下填入。

## 两类账户：不要混用

**库存发卡券**：用户名`kc_`，`on_hold`、无hold timeout、1GB=`1_000_000_000`字节上传+下载，首次非零代理流量起7天；先用尽或先到期作废。订阅下载不激活，自动测速会算实际使用。计量轮询有少量超额，并非逐字节硬截断。

**私人自用账户**：用户名`personal_owner_`，`active`、`data_limit=null`、`expire=null`、hold/auto-delete/next-plan均null，不纳入库存券回收。创建API传`data_limit=0`会被Marzban持久化为null，订阅头`total=0; expire=0`代表无限额/无到期。商家线路总流量约束仍存在，也不强制只有一台设备；凭据不要外传。共享入口基础设施闸门仍会在故障时影响自用连接，不要为自用关闭守护器。

旧订阅撤销不能只换链接：应先验证新身份，再禁用/删除旧用户、删除旧KV映射，验证旧实际交付URL返回404、旧UUID无法新建连接、已建立连接被切断。私人身份不被kc会话映射追踪，手工轮换时必须另外安排定向连接撤销；不要把单纯API删除当成已有连接必断的保证。

## 恢复定位（人工审核后执行，不是全自动重装脚本）

```bash
git clone --branch kddi-v0.8.4 https://github.com/xieyuanqing/marzban-kddi.git
cd marzban-kddi
NODE=node bash contrib/kddi/scripts/check.sh
```

1. 先备份现有配置与SQLite：使用SQLite backup API，不直接复制使用中的db文件。生产身份/余额在私密DB中，**GitHub不能替代这份数据备份**。
2. 将根`main.py`与`deploy/compose.yaml.example`放到`/opt/marzban-kddi/`，compose示例重命名为`compose.yaml`；将env/Xray示例复制到`.env`、`xray.json`，在仓库之外填写真实值。compose仅挂载目录中这份`main.py`，否则官方镜像仍会打印访问路径。
3. 将`coupons/`的源码放到`/opt/kddi-coupons/`、`share/`放到`/opt/kddi-share/`。在`/root/.secrets/auth/kddi-marzban.json`填写真正DB管理员，`kddi-share.json`填写独立Pages项目/KV/后端域名；CF token在`/root/.secrets/credentials.env`，全部0600。**不要用.env SUDO虚拟身份长期创建用户**：v0.8.4无owner用户删除可能通知500，删除后必须读回。
4. 配置专用systemd guard及`/etc/kddi-coupon-guard.env`；填写入口IPv4。运行需`nft`、`ss`/NET_ADMIN及正确FIFO日志路径。生产Xray不能用`loglevel=none`，否则会抑制FIFO记录；隔离`xray run -test`时反而应关闭验证副本的FIFO日志，避免不存在的生产挂载造成假校验失败。
5. Caddy只合并示例订阅vhost，**不能覆盖其他站点**。先验证配置再重载。无需额外私有头/CF源门禁；GET与HEAD均需兼容，保持管理面隔离和no-store。
6. 需要分享部署时：`python3 /opt/kddi-share/deploy_pages.py --config /root/.secrets/auth/kddi-share.json --apply`。需要Node/npm/npx和Pages/KV范围token；函数请求和KV操作受账号免费限额，免费域名无商业SLA。部署后读回精确deployment再实测。
7. 券仍使用`coupons.py batch --count 100 --traffic-gb 1 --days 7 --label stock`；命令创建真实库存，**不要拿它当无副作用自测**。私人账户可先运行`personal.py --username personal_owner_example`看计划，再显式`--apply`创建；该工具不会删除任何旧账户。
8. 出口监测脚本放入Hermes的`~/.hermes/scripts/`，通过`hermes cron create '*/5 * * * *' --script kddi_egress_watch.py --no-agent --deliver local --failure-deliver local`静默运行。只经`127.0.0.1:10840`的专用落地SOCKS请求，失败不回退VPS直连，也不消耗客户券额度；不会主动重拨。

这些步骤需已有固定SS2022落地与防火墙，不替代商家NAT映射/来源白名单的部署和验收。模板只存本次结构；上游升级必须旁路迁移再回归，不能直接merge最新上游覆盖线上。

## 最容易忘的兼容问题

- **Karing导入配置是GET，刷新流量/期限是HEAD**。只允许GET会造成导入和联网正常，但刷新弹`http statusCode:404`。HEAD要返回200、空正文、有效`subscription-userinfo`。`405`在Karing可回退下载，404不会。
- Worker兼容GET/HEAD，忽略刷新附加query、不转发，允许Pages项目别名Host；仍只能代理KV指定的固定后端路径，`?url=`不能把它变成任意代理。
- 不返回HTML订阅页、后台回链、任意重定向或后端错误中的额度头。保留节点格式和有效流量/到期头，无内容缓存，身份撤销后同分享链接404，即使KV映射读缓存未失效也不复活内容。
- 无论使用什么客户端，直接验证**实际交付的URL**；Marzban重新生成URL不代表以前的JWT被撤销。测试时别拿默认Python UA的CF403当后端404，头名统一小写比较。
- NAT容器里续租内网DHCP不等于重拨家宽。新TCP/源端口/查IP目标只检验出口分配规律；没有商家重拨接口时不能宣称能强制换公网IP。

## 测试与提交纪律

```bash
bash contrib/kddi/scripts/check.sh
```

离线覆盖：券CLI模拟HTTP测试、KV幂等/删除读回测试、guard库存/无限额/私人豁免/到期流程、出口采样失败与变化、Pages部署的模拟精确读回，以及Worker方法/参数/格式/错误/HEAD头兼容。模拟测试不等于完整生产重装；真实链路、首次流量计时、小额度/短到期断流与Karing更新另在私有实例做过实测，不提供用户数据。

修改后只提交源码和示例，先审阅`git diff --cached`并扫描秘密，再push并检查对应commit的CI。不得上传`.env`、API token、SSH/SS/Reality私钥、真实UUID/short-ID/订阅、SQLite、发卡TXT/CSV/journal、含目标或令牌的访问日志。一次性的生产迁移/身份旋转脚本与私密操作归档不作为可复用安装器发布。
