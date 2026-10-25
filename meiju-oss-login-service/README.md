# Meiju OSS Login Service

基于对美的美居 11.5.11.4 APK（`美的美居_11.5.11.4_APKPure.apk`）的静态分析整理的**开源后端服务**：
登录 + 设备列表 + 设备控制。

已复现的接口（路径与 APK 内的真实 alias 同名同形）：

```text
GET  /health                       健康检查（含 provider）
POST /v1/login/request-sms        发验证码
POST /v1/login/verify-sms         验证码登录
POST /v1/login/password           密码登录（本地完整可用；真实模式缺项返回 501）
POST /v1/login/refresh-token      刷新令牌
GET  /v1/me                       当前会话
POST /v1/logout                   退出

GET  /v1/user/device/list         设备列表
GET  /v1/thing/properties/{code}  读设备属性
POST /v1/thing/batch/properties   批量写属性（控制主入口）
POST /v1/thing/action/{code}      TSL 动作
```

设备与属性是 **TSL 模板驱动**的：每台设备带一份 schema（`switch / enum / slider /
number` + 取值范围 + 选项），前端据此渲染控件、后端据此校验。这与 APK 的做法一致——
它不硬编码机型属性表，而是运行时拉取 TSL 模板（详见
[docs/protocol-notes.md](docs/protocol-notes.md) §9）。

默认使用 `LocalProvider` + 3 台模拟设备，因此不依赖真实美的账号即可完整跑通流程。
**本地验证码固定 `123456`**（与手机短信无关，调试接口 `GET /v1/login/debug/local-code` 可取）。

## 快速启动

前置：uv（`pipx install uv` 或 `winget install astral-sh.uv`）；前端产物
`..\meiju-web\dist\meiju-web\browser`（见后文「前端」）。

```powershell
# 1) 一键启动：后端 :8080 + 前端 :4300（各弹一个窗口，关窗即停）
powershell -File scripts\dev.ps1          # 本地模拟（LocalProvider，验证码/密码都是 123456）

# 2) 真实环境启动：MIDEA_PROVIDER=midea，走美的生产网关
powershell -File scripts\dev.ps1 -Real    # 默认注入 MIDEA_SECRET，见 .env.example

# 3) 一键自检：health → 验证码登录 → 密码登录 → 设备列表 → 控制 → 读回 → 非法值拒绝（12 步）
powershell -File scripts\verify.ps1

# 4) 停止
powershell -File scripts\stop.ps1
```

真实模式说明：

- 签名密钥默认用 `prod_secret123@muc`（来源 `libKwProtectSDK.so`），可用
  `MIDEA_SECRET` 覆盖；
- 验证码登录会**向美的网关发起真实请求**（含真实短信），只应用于你自己的号码；
- 密码登录：`loginId` 走 `/v1/user/login/id/get`（**已实测打通**），加密用
  `SHA256(loginId+SHA256(pwd)+IOT_KEY)` 与 `MD5(MD5(pwd))`。只差 `IOT_KEY`，
  未注入时返回 **501**，不会发出任何密码请求；
- 配置项清单见 `.env.example`；不设任何变量即为本地模拟。

### 真实密码登录（可选，需要你自己的密码）

`IOT_KEY` 已用 APK 自带的解码算法从 `libKwProtectSDK.so` 还原
（详见 `docs/protocol-notes.md` §8.1）：

```powershell
$env:MIDEA_IOT_KEY = "PROD_VnoClJI9aikS8dyy"
powershell -File scripts\dev.ps1 -Real
```

然后打开 http://127.0.0.1:4300/ →「密码登录」→ 输入手机号与你的美居密码。
密码只在内存中参与哈希，不落盘、不进日志。**注意**：连续输错有风控/锁号风险，
建议确认密码无误再试。

### 真机模式（需要一个 accessToken）

设备接口默认返回 3 台模拟设备。拿到官方 App 的 `accessToken` 后，**同一套接口**
会自动改为转发到美的云端（`app/real_devices.py`）：

```powershell
# 方式一：启动时注入
$env:MIDEA_ACCESS_TOKEN = "<官方 App 的 accessToken>"
powershell -File scripts\dev.ps1 -Real

# 方式二：运行中注入（只存进程内存，重启失效）
curl.exe -X POST http://127.0.0.1:8080/v1/session/real-token -H "Content-Type: application/json" -d "{\"access_token\":\"<token>\"}"

# 确认当前模式
curl.exe http://127.0.0.1:8080/health     # device_mode: real / simulated
```

真机链路（alias 均来自反编译）：

| 用途 | alias |
|---|---|
| 设备列表 | `/v1/user/device/list` |
| TSL 模板 | `/v1/appliance/thing/template/get` |
| 读属性 | `/v1/thing/properties/{code}` |
| 写属性 | `/v1/thing/batch/properties` |
| 动作 | `/v1/thing/action/{moduleCode}/{actionCode}/{code}` |

TSL 模板的返回结构由服务端定义，`real_devices._schema_from()` 会尝试几种已知形状；
拿不到时退回通用空调 schema，并用设备的 `schemaSource` 标注 `template` / `fallback`，
不会假装是真实模板。

### 真实密码登录的限制

`encodeMD5` / `encodeSHA` 在 `libKwProtectSDK.so` 里被 VM 保护——Ghidra 反编译的
23 个 JNI 函数全是控制流平坦化派发器，看不到任何哈希初始常量，**算法无法静态还原**。
因此密码哈希无法在本地复现，密码登录目前返回 `1106`。详见
[docs/protocol-notes.md](docs/protocol-notes.md) 的「Ghidra 结论」。

### 逆向工具（JADX / Ghidra / JDK）

反编译 DEX 用 JADX（JDK 17），反汇编 native 库用 Ghidra（要求 JDK 25+），
两条脚本一键装卸（走 scoop）：

```powershell
powershell -File scripts\install-re-tools.ps1     # temurin17-jdk + temurin25-jdk + jadx + ghidra
powershell -File scripts\uninstall-re-tools.ps1   # 全部卸载并清理 JAVA_HOME/GHIDRA_INSTALL_DIR
powershell -File scripts\uninstall-re-tools.ps1 -KeepJavaBucket
```

卸载脚本会一并删除仓库内的 `.jadx-config` / `.jadx-cache` / `ghidra-proj` 临时目录。

Ghidra 反编译 native 库的现成脚本：`meiju-analysis/ghidra-scripts/DumpIotHashes.java`。


然后打开 **http://127.0.0.1:4300/** ，验证码填 `123456`。

手动启动等价于：

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080      # 后端
python -m http.server 4300 --bind 127.0.0.1 --directory ..\meiju-web\dist\meiju-web\browser
```

局域网/其他设备打开（如手机）：前端会自动用页面主机名请求 API
（`http://<同主机>:8080`）；前后端不在同一台机器时用 `?api=` 覆盖：

```
http://192.168.1.5:4300/?api=http://192.168.1.5:8080
```

后端 CORS 默认放行任意来源（演示用途，靠 Bearer 鉴权）；收紧用环境变量
`CORS_ORIGINS="http://a,http://b"`。健康检查：`GET /health`。

## 环境（uv）

项目用 [uv](https://docs.astral.sh/uv/) 管理，`pyproject.toml` + `uv.lock` 是唯一依赖来源。

```bash
uv sync                                  # 建 .venv，按 uv.lock 安装
uv run uvicorn app.main:app --port 8080  # 启动（热重载加 --reload）
uv run pytest -q                         # 跑测试
uv add <pkg>                             # 加依赖
uv lock --upgrade                        # 升级并刷新 lock
```

`.python-version` 已固定 `3.12`。没有 uv 时：

```bash
pipx install uv        # 或 winget install astral-sh.uv
```

## 设计边界

- `LocalProvider` 是开源项目默认后端，用于开发、集成和 CI。
- `MideaProvider` 是真实上游适配器，已按实测还原签名与请求结构，不包含绕过验证码、风控、短信配额或安全控制的逻辑。
- 真实上游登录需要用户自有账号；本项目不会内置账号、密码或验证码凭据。
- 服务端只保存登录状态所需的最小字段，默认内存存储；生产环境可替换为 Redis/PostgreSQL。

## 真实上游（已实测打通到验证码环节）

```powershell
$env:MIDEA_SECRET = "prod_secret123@muc"   # 由 libKwProtectSDK.so 提供
uv run python tools/live_login.py sms    <mobile>          # 发登录验证码
uv run python tools/live_login.py verify <mobile> <code>   # 提交验证码
```

实测结果：

| 环节 | 状态 |
|---|---|
| 网关签名 `sign/random/secret` | 通过 |
| `POST /mj/user/getSmsCode`（`iotData.type=2`） | `code 0 成功`，短信下发 |
| `POST /mj/user/mobileVerify` | `code 0`，返回 `randomCode` |
| `randomCode` → `accessToken` | **未打通**，见 `docs/protocol-notes.md` §4 |

由于最后一步未还原，`MideaProvider` 返回的 token 会带 `unverified_` 前缀，避免被误当成真实凭据。

## 本地 API 示例

创建测试手机号和验证码：

```http
POST /v1/login/request-sms
Content-Type: application/json

{"mobile": "13800138000"}
```

查看验证码（仅 LocalProvider）：

```http
GET /v1/login/debug/local-code?mobile=13800138000
```

登录：

```http
POST /v1/login/verify-sms
Content-Type: application/json

{
  "mobile": "13800138000",
  "sms_code": "123456"
}
```

保存返回的 `access_token`，后续请求：

```http
GET /v1/me
Authorization: Bearer <access_token>
```

## 代码结构

```text
pyproject.toml           uv 项目定义（依赖 / dev 组 / pytest 配置）
uv.lock                  锁定版本，可复现安装
.python-version          固定 Python 3.12
.env.example             环境变量清单（真实模式 / 本地模式 / CORS）
app/
  main.py               FastAPI 入口
  config.py             环境配置（MIDEA_* / LOCAL_PASSWORD / CORS）
  domain.py             登录领域模型（含 PasswordLoginRequest）
  api.py                对外 API 路由 + /health + 可配 CORS + 挂载设备路由
  deps.py               共享依赖（Bearer 鉴权）
  devices.py            模拟设备 + TSL schema + 按 schema 校验
  device_api.py         设备列表 / 属性读写 / TSL 动作
  mas.py                MAS 网关签名（md5(secret+body+params+random)）
  crypto_util.py        密码登录哈希（SHA256 链 / 双层 MD5，均来自字节码）
  real_devices.py       真机转发层（注入 accessToken 后走美的云端）
  providers/
    base.py             Provider 抽象（含 password_login）
    local.py            默认本地登录 Provider（验证码与密码都是 123456）
    midea.py            真实上游适配器 + GatewayError（网络故障 → 502）
  store.py              内存态登录存储
scripts/
  dev.ps1               一键启动前后端（-Real 切真实环境）
  verify.ps1            一键自检 12 步（health→验证码→密码→列表→控制→读回→校验）
  stop.ps1              按端口 8080/4300 停止
  install-re-tools.ps1  装逆向工具（scoop: temurin17-jdk + jadx）
  uninstall-re-tools.ps1 卸载上述工具并清理 JAVA_HOME / 临时目录
tests/
  test_local_login.py   本地登录链路
  test_mas_signing.py   签名回归（用的是网关真实接受过的向量）
  test_device_control.py 设备列表 / 控制 / 校验 / TSL 动作
  test_password_login.py 密码登录 + 哈希链向量
  test_midea_password.py 真实 provider 请求构造（loginId / 加密字段 / 错误传播）
tools/
  e2e_check.py          登录→设备→控制 端到端脚本
  check_real_mode.py    真实模式接线自检（不联网、不发短信）
  gateway_probe.py      单条请求探针（可自动签名）
  live_login.py         真实上游 sms / verify 命令
  live_capture.py       真实上游采集器（请求+响应全量落盘）
  live_control.py       真实上游设备控制（列设备/读属性/写属性/动作）
probes/                 协议探测脚本
  captures/             真机协议记录 56 份（含签名头、bodyRaw、响应头）
```

## 协议观察记录

详细静态观察、证据位置、参数名和未知项见 [docs/protocol-notes.md](docs/protocol-notes.md)。

## License

本项目使用 MIT License。
