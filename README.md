# meiju-service

逆向美的美居（Midea Meiju）APK 后重建的**局域网真机控制**服务：登录一次换取设备密钥，
之后完全离线地发现设备、读取状态、下发控制。

> 仅用于自己的设备与自己的账号。设备密钥等同控制权，请勿外泄。

## 目录

```text
meiju-oss-login-service/     后端（Python / FastAPI，:8080）
  app/api.py                 登录、会话、以及挂载 LAN 路由
  app/lan/service.py         设备发现 / 密钥 / 状态 / 控制
  app/lan/router.py          /lan/* HTTP 接口
  app/providers/midea.py     真实网关适配（MAS 签名、登录、验证码）
  vendor/midealocal/         vendored midea-local（MIT，见其 LICENSE）
  docs/                      登录逆向报告与协议笔记
  scripts/                   dev.ps1 / stop.ps1 / verify.ps1 等
  var/                       运行时数据（密钥缓存，已在 .gitignore）

meiju-web/                   前端（Angular，静态产物由任意 http server 提供）
  src/app/pages/login/       登录页：美居账号换设备密钥
  src/app/pages/lan/         真机页：扫描 / 读状态 / 下发控制
  src/app/core/lan.service.ts 后端接口封装（统一 8080）
```

## 快速开始

```powershell
# 后端
cd meiju-oss-login-service
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080

# 前端
cd ../meiju-web
npm install --ignore-scripts
npx ng build --configuration development
python -m http.server 4300 --directory dist/meiju-web/browser
```

打开 http://127.0.0.1:4300/ → 用美居账号密码登录一次 → 密钥缓存到
`meiju-oss-login-service/var/lan_keys.json` → 之后直接进「真机」页。

也可以用 `meiju-oss-login-service/scripts/dev.ps1` 一次性启动前后端。

## 认证模型（关键）

- 控制设备**不需要用户登录**，需要的是每台设备一对 `token` + `key`
- 这对密钥由云端按设备下发：`POST /v2/iot/secure/getToken`
  （美居已下线 `/v1/iot/secure/getToken`）
- 云端换密钥需要账号密码，**只在登录时使用一次**，不落盘
- 密钥缓存后即可离线控制；设备侧密钥轮换时缓存失效，页面会要求重新登录

## 局域网发现

- UDP 6445 广播；若广播穿不过（如 Tailscale 等三层隧道），自动降级为**单播扫网段**
  （默认 `10.0.0.0/24`，可用 `?cidr=` 或环境变量 `MEIJU_LAN_CIDR` 指定）
- 控制走 TCP 6444，美的 V3 协议（AES，密钥来自 device token/key）

## License

本项目代码 MIT。`vendor/midealocal` 遵循其自身 MIT 许可。
