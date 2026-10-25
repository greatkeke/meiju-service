# Meiju Web — 美的美居登录页复刻（Angular）

按 `美的美居_11.5.11.4` 的登录流程复刻的前端，接本仓库的 FastAPI 登录服务。

## 界面还原依据

登录页在原 APK 里几乎全部由 Kotlin 代码绘制（`LoginActivity` / `MobileInputActivity` /
`MobileVerifyActivity`），XML 资源只有 2 个配色文件，所以：

- **文案**：从 `resources.arsc` 提取原 App 字符串，用的是真实措辞
  - `请输入手机号`、`获取验证码`、`请输入验证码`、`%sS后重新获取`、`重新获取`
  - `本机号码一键登录`、`密码登录`
  - `我们已发送一条验证码至%s`
  - `我已阅读并同意《用户协议》《隐私协议》《美的美居隐私协议》`
  - `未注册的手机号将自动完成账号注册`
- **配色/布局**：没有可复用的位图，按美的蓝系自行设计（`#00a7e1 → #0a63c4`），
  深蓝渐变 + 白色圆角卡片，桌面端套一层手机外框。

## 页面

| 路由 | 对应原 App | 内容 |
|---|---|---|
| `#/login` | `MobileInputActivity` | 手机号输入、获取验证码、协议勾选 |
| `#/verify` | `MobileVerifyActivity` | 6 位分格验证码、60s 倒计时重发 |
| `#/home` | 登录后首页 | 会话信息、退出登录 |

使用 hash 路由（`withHashLocation`），构建产物可用任意静态服务器直接打开。

## 运行

```powershell
# 1) 后端（uv 管理，见 backend README）
cd ..\meiju-oss-login-service
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080

# 2) 前端
cd ..\meiju-web
npm install --ignore-scripts     # 见下方说明
npx ng build --configuration development
python -m http.server 4300 --bind 127.0.0.1 --directory dist/meiju-web/browser
```

打开 <http://127.0.0.1:4300/>。

开发模式（热更新）用 `npx ng serve`，监听 4200；后端 CORS 已放行 4200。

## 两个环境坑（已踩过）

1. **`npm install` 要加 `--ignore-scripts`**
   受限沙箱里 npm 派生生命周期脚本子进程会 `EPERM`，依赖本身不需要 postinstall。

2. **`ng build` 需要放宽一次沙箱权限**
   Angular CLI 通过 esbuild 子进程管道通信，受限模式会报 `spawn EPERM`。
   普通机器上不存在这个问题。

## 后端联调

`src/app/core/auth.service.ts` 里的 `API_BASE` **默认跟随页面主机名**：
`http://<当前host>:8080`，本机和局域网打开都能直连后端。
两端不在同一台机器时用查询参数覆盖：`http://<前端host>:4300/?api=http://<后端host>:8080`。

后端默认在 `http://127.0.0.1:8080`，CORS 放行任意来源（演示用途）。

```
GET  /health                      健康检查
POST /v1/login/request-sms   { mobile }
POST /v1/login/verify-sms    { mobile, sms_code }
POST /v1/login/password      { mobile, password }         密码登录
GET  /v1/me                  Authorization: Bearer <token>
POST /v1/logout

GET  /v1/user/device/list         设备列表
GET  /v1/thing/properties/{code}  读属性
POST /v1/thing/batch/properties   写属性（控制）
POST /v1/thing/action/{code}      TSL 动作
```

## 两种登录方式

登录页顶部有「验证码登录 / 密码登录」切换（对应原 App 的
`MobileInputActivity.getSmsCode` 与 `passwordLogin` 两条路径）：

| 方式 | 前端行为 | 本地演示值 |
|---|---|---|
| 验证码登录 | `POST /v1/login/request-sms` → 跳 `#/verify` 输 6 位码 | `123456` |
| 密码登录 | `POST /v1/login/password` → 直接进 `#/home` | `123456`（`LOCAL_PASSWORD` 可改） |

密码登录在真实 provider 下会返回 **501** 并说明缺 `loginId` / `IOT_KEY`，
界面会把该 detail 原文显示出来（红色错误行），不会静默失败。

登录后进入 `#/home`，点「我的家 · 设备控制」进 `#/devices`，
再点卡片进 `#/devices/:applianceCode` 控制面板（控件由后端下发的 schema 决定）。

默认 provider 是 `LocalProvider`，本地验证码固定 `123456`
（`GET /v1/login/debug/local-code?mobile=...` 也可直接取）。
**在验证页面输真实短信验证码不会通过**——那是 `MideaProvider` 的行为，而 Midea 路径
尚未打通，见后端 `docs/protocol-notes.md`。

若 `access_token` 以 `unverified_` 开头，说明走的是真实上游但只到验证码环节，
token 不是美的线上凭据——首页会用黄色提示条说明。
