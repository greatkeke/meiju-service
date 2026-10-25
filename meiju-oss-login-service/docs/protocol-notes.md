# Meiju Login Protocol — Verified Notes

Target: `美的美居_11.5.11.4_APKPure.apk`
SHA-256 `E1D81B800E938D42976251432EE819856368F95ECDBBC52A70ABED6C0D556758`

Everything below marked **verified** was confirmed against the live production
gateway in this investigation. Nothing here bypasses a CAPTCHA, a risk control or
an account-security challenge, and the only account used was the owner's own.

## 1. Transport

| Item | Value | Status |
|---|---|---|
| Base URL | `https://mp-prod.smartmidea.net/mas/v5/app/proxy?alias=<alias>` | verified |
| Method | `POST`, JSON body | verified |
| Env switch | SIT `mp-sit...`, DEV `mp-dev...` (same path) | static |
| WAF | Alibaba `acw_tc` cookie issued on first response | verified |

`EnvConfig$PROConfig.<clinit>` holds the host constants; `AppConfig.initEnvConfig`
selects PRO/SIT/UAT/DEV.

## 2. Request signature (the hard part)

`MideaMasSignPolicy.sign` (classes8.dex) produces three headers:

```
random  = String.valueOf(System.currentTimeMillis())
content = (json body) + concat over sorted path params of (key + value)
sign    = md5(secret + content + random)      # lowercase hex
```

Headers sent: `sign`, `random`, `secret`.

Verified error ladder on `GET|POST /mj/user/getSmsCode`:

| Request | Gateway reply |
|---|---|
| no header | `44001 签名缺失 [sign is empty]` |
| `sign: 0000` | `44003 签名校验失败 [sign is wrong]` |
| computed sign + correct secret | passes signature, reaches business layer |

`X-Sign` is **not** honoured — the header name is exactly `sign`.

### Where the secret comes from

`EnvConfig$PROConfig.getMucSecret` is a **native** method
(`access_flags = 0x104 = protected | native`), implemented in
`lib/arm64-v8a/libKwProtectSDK.so`. The library exports

```
Java_com_midea_meiju_util_EnvConfig_00024PROConfig_getMucSecret
```

and carries the literals `prod_secret123@muc`, `sit_secret123@muc`,
`dev_secret123@muc`. The production value was accepted by the live gateway.

`MAS_KEY` / `IOT_SECRET` are additionally passed through `IEncryption.decode`
and injected into `MideaSDK.initSDKWithAppID(appId="900", ...)`.

## 3. SMS verification-code request

Alias `POST /mj/user/getSmsCode`.

Body is `CommonReqData`:

```json
{
  "data": {
    "appKey": "", "appVersion": "11.5.11.4", "deviceId": "...",
    "deviceName": "...", "mobile": "<mobile>", "osVersion": "13", "platform": 0
  },
  "iotData": { "mobile": "<mobile>", "type": 2, "reqId": "...", "stamp": "yyyyMMddHHmmss" },
  "timestamp": "yyyyMMddHHmmss"
}
```

`CommonReqData` fields (classes7): `data: MucData`, `iotData: IotData`, `timestamp`.

Error ladder that pinned the shape down:

| Body | Reply |
|---|---|
| `{}` | `400 中台参数不能为空` |
| `{"data":{}}` | `1 服务内部异常: null` |
| `{"data": MucData}` | `1000 System error! null` |
| `{"data": MucData, "iotData": {"mobile": ""}}` | `1006 mobile= value is illegal.` |
| `iotData.type` missing | `1002 非法的参数：type不能为空` |
| `type = 1` | `1104 The account is existed` (register flow) |
| `type = 0` | `1003 Unsupported type: 0` |
| `type = 2`, real mobile | `0 成功` → SMS delivered |

So **`iotData.type = 2` is the login SMS flow**, `1` is registration.

## 4. Verification

Alias `POST /mj/user/mobileVerify`, same envelope with

```json
"iotData": { "mobile": "<mobile>", "smsCode": "<code>", "type": 2 }
```

Live result: `{"code":0,"msg":"成功","data":{"randomCode":"<32 hex>"}}`.
The code is **single-use and consumed by this call**.

### Token exchange — NOT resolved

The `randomCode` must be turned into an `accessToken` by a follow-up call
(`LoginApiService.mobileVerifyLogin(String loginToken, ...)`), but every candidate
alias was rejected by the live gateway:

| Attempt | Reply |
|---|---|
| `/mj/user/autoLogin` with `loginToken` | `65012 令牌密码无效` |
| `/mj/user/autoLogin` with `tokenPwd` | `65012 令牌密码无效` |
| `/v6/mj/user/login` | `1002 iampwd must not be empty` / `password must not be empty` |
| `/v6/mj/user/onekey/auth` | `1002 jPushAppCode must not be null` (carrier one-key flow) |
| `/mj/user/mobile/login` + `smsCode` | `1100 ... sms verificationCode is skipped off or expired` (server-side switch is off) |
| `/mj/user/mobileVerify` + `loginToken` | `1002 smsCode must not be empty` |

#### 后续实测（一次发码 + 4 组矩阵，全部留存于 `probes/captures/`）

`getSmsCode` 响应头暴露了平台的鉴权头白名单与限流预算：

```
M-RateLimit-Limit: 100        M-RateLimit-Remaining: 99
Access-Control-Allow-Headers: access-token, x-auth-token, content-type, ...
X-APISIX-Upstream-Status: 200
```

即平台认的是 `access-token` / `x-auth-token` 这两个头名，而不是驼峰 `accessToken`。

矩阵结论（`randomCode=6177b784…` 单次有效期内的 20+ 次请求）：

| 探测 | 结果 | 含义 |
|---|---|---|
| `/v6/mj/user/login` + `loginToken` | `1002 loginAccount must not be empty` | 缺账号 |
| 同上 + `loginAccount` | `1002 password must not be empty` | **该端点是密码登录** |
| 同上 + 任意非空 `loginId` | `1002 password must not be empty` | 校验顺序：loginId → password |
| 同上 + 空串 `iampwd`/`password` | `1111 Login id is empty.` | 空串视为缺省，必须先有 loginId |
| `/v1/user/login/id/get`（stamp 在 `data`） | `1002 stamp must not be empty` | stamp 位置不对 |
| 同上（stamp 在 `data`，无 reqId） | `1002 reqId must not be empty` | 依次校验 stamp → reqId |
| 同上（`reqId` 在 **JSON 根层级**） | `40500 调用业务系统异常(业务服务内部错误)` | 通过校验，业务层报错 |
| 同上 + query 参数参与 | `44003 签名校验失败` | 加 query 会改变签名内容 |
| `/mj/user/mobile/login` + `smsCode=randomCode` | `1100 ... skipped off` | 该通道服务端关闭 |
| `/v6/mj/user/third/login` | `1002 src must not be null` | 第三方登录 |

由此确定：`/v6/mj/user/login` 是**密码登录**，短信登录不走它；`/v1/user/login/id/get`
需要根层级 `reqId`+`stamp`，但随后业务层 `40500`。**短信换 token 的真实端点仍未知**。

已实现但未完成的辅助：`meiju-analysis/annotations.py` 能解析 DEX 注解目录
（每个 API 方法的注解都能定位），但 encoded_value 里的 string 索引解析仍有偏差，
尚未直接读出 Retrofit 的 `@POST("proxy?alias=...")` 字面量。这是最值得继续攻的点：
一旦读出别名，就不必再靠枚举猜端点。

So the SMS→token exchange endpoint or its required field combination is still
unknown. The demo therefore stops at the verified step and labels any token it
returns with an `unverified_` prefix.

Token material is known to arrive in
`LoginRspData{mdata: LoginRspMData{accessToken, expired, tokenPwdInfo}, jwtToken, iotUserId, region, clusterId}`.

## 5. Native-side request wrapper (for completeness)

`InlandMasReqWrapper.getJsonRequest` sets:

```
Content-Type: application/json, Connection: keep-alive, Accept-Encoding: identity
accessToken: <if any>, version: <versionName>, platform: 0, systemVersion: <Build.VERSION.RELEASE>
```

`getBaseBody` adds `reqId` and `stamp`. The app also sends `format=2`,
`language=<locale>` on the form path.

## 6. Login UI and route inventory (static)

Activities: `LoginActivity`, `MobileInputActivity`, `MobileVerifyActivity`,
`MobileVerifyBindActivity`, `RegisterForgetActivity`, `RegisterNextActivity`,
`RegisterSetpwdActivity`, `ResetBindMobileActivity`, `ResetPwdActivity`.

Key aliases: `/mj/user/getSmsCode`, `/mj/user/mobileVerify`, `/mj/user/autoLogin`,
`/mj/user/logout`, `/mj/user/changePassword`, `/mj/user/resetPassword`,
`/v6/mj/user/login`, `/v6/mj/user/register`, `/v6/mj/user/third/login`,
`/v6/mj/user/update/tokenPwd/mobile`, `/v1/user/login/id/get`,
`/v1/user/multi/logout/otherDevice`, `/v1/user/mobile/modify`.

Token plumbing: `TokenInterceptor`, `TokenService$forceRefresh`,
`LoginManager.updateToken`, `autoLogin` + cached-token refresh at launch.

## 7. Still unknown

- The SMS→token exchange endpoint (see §4); this is the one blocking the demo from
  returning a real credential.
- Exact server-side semantics of `randomToken` / `imgCode` (CAPTCHA) triggers.
- Behaviour under risk control (SMS rate limiting, device fingerprint checks).
- `MAS_KEY` / `IOT_SECRET` plaintext (only the encrypted form exists in the APK).

## 8. Reproducing

```powershell
$env:MIDEA_PROVIDER = "midea"
$env:MIDEA_SECRET   = "prod_secret123@muc"
python tools/live_login.py sms    <mobile>
python tools/live_login.py verify <mobile> <sms_code>
```

Offline signature check with the live-accepted vector:

```powershell
python -c "from app.mas import mas_sign; print(mas_sign('prod_secret123@muc','',{},'1792236849602'))"
# e277c3032f6fd76d39db11de6f76eae7
```

## 8.1 Retrofit alias 表（注解静态解析，不再靠猜）

`meiju-analysis/annotations.py` 解析 DEX 的 `annotations_directory_item`，把每个
Retrofit 方法上的 `@POST("proxy?alias=…")` 字面量读出来。这是本轮最硬的成果：
**接口名到真实路由的映射是确定的，不是推断的。**

| Kotlin/Java 方法 | 真实 alias |
|---|---|
| `LoginApi.getSmsCode` | `/mj/user/getSmsCode` |
| `LoginApi.verifyMobile` | `/mj/user/mobileVerify` |
| `LoginApi.mobileVerifyLogin` | **`/v6/mj/user/onekey/auth`** |
| `LoginApi.mobileVerifyThirdBind` | `/v6/mj/user/onekey/bind` |
| `LoginApi.passwordLogin` | `/v6/mj/user/login` |
| `LoginApi.getLoginId` | `/v1/user/login/id/get` |
| `LoginApi.mobileRegister` | `/v6/mj/user/register` |
| `LoginApi.mobileRegisterBind` | `/v6/mj/user/mobile/register/bind` |
| `LoginApi.thirdLogin` | `/v6/mj/user/third/login` |
| `LoginApi.changePassword` | `/mj/user/changePassword` |
| `LoginApi.resetPassword` | `/mj/user/resetPassword` |
| `LoginApi.modifyMobile` | `/v1/user/mobile/modify` |
| `LoginApi.logout` | `/mj/user/logout` |
| `LoginApi.logoutOtherDevice` | `/v1/user/multi/logout/otherDevice` |
| `LoginApi.updateTokenPwdMobile` | `/v6/mj/user/update/tokenPwd/mobile` |
| `TokenApiService.autoLogin` | `/mj/user/autoLogin` |
| `DeviceControlApiService.getDeviceInfoByIdOrSn` | `/v1/appliance/info/get` |

### 由此推翻的两个错误假设

1. **`/v6/mj/user/login` 不是短信登录**，它是 `passwordLogin`。之前对着它试
   `loginToken` 全部失败，原因就在这里。
2. **`onekey/auth` 是运营商/极光「本机号码一键登录」**，`loginToken` 期望的是
   `JVerificationHelper` 回调回来的认证 token，**不是短信返回的 `randomCode`**。
   实测证据：`jPushAppCode: 1`（`BuildConfig.JPUSH_APP_CODE` 的真实值）能通过参数
   校验，错误从 `1003 Invalid param unsupport` 变成 `1000 system error`；但把
  短信 `randomCode` 塞进 `loginToken` 仍然是 `1000`，换成 `loginAccount`/`type`
  等 8 种组合也无变化。

### 仍未解决

短信 `randomCode` 到底喂给哪个端点，目前没有答案。可能它配合
`/v1/user/login/id/get` 换出 `loginId` 再走 `passwordLogin`（但 id/get 业务层
`40500`），也可能存在一个未在 `LoginApi` 中声明的路由。**已停止短信尝试**：
共消耗 5 条验证码，继续烧短信的边际收益已经很低。

### 代码级结论（推翻"短信登录"这个前提）

追调用链后确认：**这个版本的 App 没有"短信验证码登录"**。

- `/mj/user/mobileVerify` 只被 `RegisterNextActivity.verifySmsCode` 调用；
  该方法把 `randomCode` 通过 Intent 传给 `RegisterSetpwdActivity`
  （`putExtra("randomCode", …)`），即**注册/设密码**流程。
- `MobileVerifyActivity.login()` 走的是
  `JVerificationHelper.getLoginToken(context, callback, false)` → 回调 token →
  `requestLogin(token)` → `mobileVerifyLogin(token)` → `/v6/mj/user/onekey/auth`。
  也就是说 `onekey/auth` 的 `loginToken` 是**极光认证的运营商 token**，与短信无关。
- 因此 `LoginApi` 的 15 个方法里，登录只有三条：`passwordLogin`
  （`/v6/mj/user/login`）、`mobileVerifyLogin`（一键登录）、`thirdLogin`。
  UI 上"未注册的手机号将自动完成账号注册"指的正是注册流程。

对已有账号（如实测号码）而言，`type=1` 注册直接返回 `1104 The account is existed`，
所以**短信这条路对老账号完全走不通**，这与实测 5 条验证码全部失败一致。

### 密码登录的字段构造（`LoginApiService.passwordLogin`）

**用 JADX 反编译后已完全确定**（`scripts/install-re-tools.ps1` 装 JDK17+JADX）：

```java
passwordLogin(String account, String password, String loginId, String dxToken)
  body = createBody(new CommonReqData(getLoginMucData(),
          new IotData(... account ...,
                        getEncryptedPwd(loginId + encodeSHA256(password) + HttpUtil.IOT_KEY),
                        ...
                        getIamPwd(password),
                        ... "900" ... 1 ... dxToken ...
                        getAppVersion(), getDeviceId(), ...)))
```

```
getEncryptedPwd(v) = SecurityUtils.encodeSHA256(v)   → password = SHA256(loginId+SHA256(pwd)+IOT_KEY)
getIamPwd(v)       = encodeMD5(encodeMD5(v))         → iampwd   = MD5(MD5(pwd))
iotAppId = "900"    clientType = 1
```

实现见 `app/crypto_util.py`、`app/providers/midea.py::password_login`，
断言见 `tests/test_midea_password.py`。

### `loginId` 怎么拿（已实测打通）

`getLoginId(account)` 发的**不是** `CommonReqData` 外壳，而是裸 JSON：

```json
{"loginAccount": "<account>", "reqId": "<...>", "stamp": "<yyyyMMddHHmmss>"}
```

`reqId`/`stamp` 由 MAS 请求包装器注入。实测：

```
POST /v1/user/login/id/get  {"loginAccount":"186****1901"}
→ {"code":0,"msg":null,"data":{"loginId":"73e0c3321d1a43ba9e684f63a62ebd98"}}
```

早期一直 `40500 业务异常` 的原因就是把 `mobile` 塞进了 `data/iotData` 外壳里——
`loginAccount` + 裸 body 才是对的。loginId 每次请求都不同（疑似带时效），
所以 `password_login` 里是**先取 loginId 再立即登录**。

### IOT_KEY 已还原（`IOTPWManager.decode` 破解）

JADX 给出的真实实现纠正了三处误判：

```java
decodeToBytes(str) = pwReplace(
    new IOTBase64().decode(
        new String(new IOTAES("rockykaikainihao").InvCipher(<64字节常量>)).toCharArray(),  // 字母表
        str))                                                                             // 真正的密文
```

1. 那段 64 字节常量**不是**密文，解密后是**自定义 base64 字母表**（64 字符，唯一）；
2. `IOTAES` 用**非标准 S-box**（`Sbox[0]=40`，标准是 99），所以标准 AES 必然解不出；
3. `pwReplace` 不只换位，还按 `index % 32` 做 `^/+/-` 运算（`+27` 处即 `0x1b`）。

验证方式：把 DEX 里的两张 256 项表原样取出（`extract_iotaes_tables.py`），
按 JADX 逻辑生成独立 Java 程序（`gen_iot_harness.py` → `harness/IotHarness.java`），
用 JDK 跑 APK 自己的算法。字母表解得：

```
tuvwxTUlmnopqrs7YZhbdecafgij8yz6543210VWXkABCDEFGHIJKLMNOPQRS9*/
```

再用「解出来必须可打印」当筛子扫描 `libKwProtectSDK.so` 的 139,626 个字符串，
只有 6 个命中，其中三个正是环境密钥：

| 密文（.so 内） | 明文 IOT_KEY |
|---|---|
| `YMLoZ5tTttyYcKSPdJe13w0Gie5tTYtyzZ1tPe9pjXO=` | `PROD_VnoClJI9aikS8dyy` |
| `gUfHr5tTttybYTZdha9L5MHGZTGtTYtytu1tPbZpiW1=` | `SIT_4VjZdg19laDoIrut` |
| `r0efyYtTttyxbTgdfMTpzVL0ewdtTYtytu1tPeZlhXK=` | `DEV_TZJn6TXxmtTHclKk` |

注入方式：`MIDEA_IOT_KEY=PROD_VnoClJI9aikS8dyy`（`dev.ps1 -Real` 只在显式设置时启用，
避免误触发真实密码尝试）。

`libKwProtectSDK.so` 中另已确认可用的 MUC 密钥 `prod_secret123@muc`。

**曾经走过弯路（记录以免重犯）**：`iot_decode_variants.py` / `iot_decode_pipeline.py`
试了 ECB/CBC 各种 IV、去头去尾、先置换后解密、以及拿候选串当 AES key 反向解，
全部失败。失败原因是假设错了——那段常量是**字母表密文**而非待解数据，
且 `IOTAES` 用**非标准 S-box**。换句话说：标准 AES 库永远解不出来，
必须用 APK 自带的实现。

### 密码登录在本项目中的落点

- `app/providers/local.py::password_login` —— 本地可完整跑通（`LOCAL_PASSWORD`，
  默认 `123456`），端点 `POST /v1/login/password`。
- `app/providers/midea.py::password_login` —— 真实链路已完整实现：
  `get_login_id()` 取 loginId（**已实测通**）→ 计算 `password`/`iampwd` →
  `POST /v6/mj/user/login`。缺 `IOT_KEY` 时返回 **501**，不会发任何密码请求。
- `app/providers/midea.py::get_login_id` —— 裸 body + `reqId`/`stamp`，实测返回 loginId。
- 上游网络故障返回 **502**（`GatewayError`），不抛 500。

**唯一未验证的一环**：真实密码本身（我无法、也不应持有）。用
`MIDEA_IOT_KEY=PROD_VnoClJI9aikS8dyy` 启动真实模式后，在页面「密码登录」输入
自己的密码即可完成——密码不落盘，只在内存中参与 SHA256/MD5 计算。

### 这个 APK 没有「短信验证码登录」（JADX 复核，推翻早先的手工结论方向）

反编译 `classes7` 全量 3043 个类后确认：

- `MobileInputActivity.getSmsCode()` 成功后调 `goSMSCodeVerify(mobile)`，
  路由到 `/login/register_next`（`RegisterNextActivity`），并把 `key-type=mType` 带过去；
- `RegisterNextActivity.toNext(randomCode)` 只处理四种：`7` 改绑手机号、
  `12` 第三方绑定、`3`/`2` 跳 `RegisterSetpwdActivity` **设置密码**；
- `RegisterNextActivity` 对错误码 `1104`（账号已存在）弹
  `showPhoneAlreadyExistsDialog()`，引导回密码登录；
- `MobileVerifyActivity` 字段里没有任何短信输入框，`login()` 走
  `JVerificationHelper.getLoginToken()` → `mobileVerifyLogin` → `/v6/mj/user/onekey/auth`；
- `passwordLogin` 的唯一调用方是 `LoginActivity`（密码登录页），与短信无关。

结论：短信验证码只服务**注册 / 改密 / 改绑手机号**；对已注册账号没有
「验证码直接登录」的入口，`verifyMobile` 的 `randomCode` 也不会换出 token。
这也解释了此前 5 条验证码全部无功而返。

### 图形验证码是第三方风控，不是普通图片码

`LoginActivity` 用 `com.midea.service.captcha.MJCaptchaDialog`，
`appId = e83bb4b2069d516f9f0798584e0ac2c1`（极光/风控类 SDK，非 `imgCode` 图片码），
失败分支：

| 错误码 | 文案 | 是否弹验证码 |
|---|---|---|
| `1018` | 密码错误次数过多，请 30 分钟后重试 | 否 |
| `1019` | 验证失败，请稍后再试（captcha token 失效） | 是 |
| `1020` | 输入密码次数超过上限 | 是 |
| `1105` | 手机号未注册 | 否 |
| `1106` | 手机号或密码不正确 | 否 |

验证通过后 SDK 回调的 token 作为 `dxToken` 传入
`passwordLogin(account, password, loginId, dxToken)`。
所以 `1106` 与验证码无关，是**凭据哈希不一致**。

### `iampwd` 大小写（`1106` 的头号嫌疑）

`SecurityUtils.encodeMD5` → `EncodeAndDecodeUtils.encodeMD5` 是 **native**，
且调用处**没有** `toLowerCase()`；而 `encodeSHA256` 显式调了 `.toLowerCase()`
（说明 native 的十六进制输出是大写）。据此本项目把 `iampwd` 改为
**大写双层 MD5**（`MIDEA_IAMPWD_LOWER=1` 可回退小写）。
`password` 字段仍是小写 SHA256 链，与 `encodeSHA256` 的行为一致。

**实测结果：大写仍是 `1106`**，说明 native 侧不是"朴素 MD5 + 大小写差异"这么简单。

### Ghidra 结论：native 哈希被 VM 保护，静态不可还原

用 `scoop install extras/ghidra`（+ JDK 25）装好 Ghidra 12.2，脚本
`meiju-analysis/ghidra-scripts/DumpIotHashes.java` 对 `libKwProtectSDK.so`
反编译了全部 23 个 `EncodeAndDecodeUtils` / `EnvConfig.getIotSecret` JNI 符号。
结果（见 `meiju-analysis/ghidra-iot-hashes.log`）：

- **23/23 函数体都是控制流平坦化的派发器**：先写上百个 `DAT_*` 全局，
  再 `(*(code *)(DAT_x + imm))()` 间接跳转（23 处 "Could not recover jumptable"）；
- 函数体内 **4377 处全局写**，**没有任何 MD5/SHA 初始常量**
  （`67452301` / `efcdab89` / `98badcfe` / `10325476` 均未出现）。

即哈希真正逻辑跑在这个库自带的字节码 VM 里，不是可直接读的
`MessageDigest` 调用。**结论：`encodeMD5` / `encodeSHA` 的具体算法无法靠静态
反汇编还原**，要么在 ARM64 上动态执行该 .so，要么绕开密码登录。

### 真机设备：接口层已完全闭环

从 JADX 全量反编译中确认的三条真机链路（均需 `access-token` 头）：

| 用途 | alias |
|---|---|
| 设备列表 | `POST /v1/user/device/list` |
| TSL 模板（属性码来源） | `POST /v1/appliance/thing/template/get` |
| 读属性 | `POST /v1/thing/properties/{applianceCode}` |
| 写属性 | `POST /v1/thing/batch/properties` |
| 动作 | `POST /v1/thing/action/{moduleCode}/{actionCode}/{applianceCode}` |

控制路径还带查询参数 `?requestId=<uuid>&sync=<bool>&params=<json>`
（见 `TSLModelService`）。本项目已据此实现真机转发层
`app/real_devices.py`：注入 `MIDEA_ACCESS_TOKEN`（或 `POST /v1/session/real-token`）
后，同一套 `/v1/user/device/list`、`/v1/thing/*` 接口自动改走美的云端，
`/health` 的 `device_mode` 会变成 `real`。

### REA 工具链状态（更新）

`npx -y rea-agents@6.5.0 doctor --json`：`node`/`host` 正常、skill v42 对齐，
但 `hopper`/`ghidra`/`ida` 不可用，`jadx` 缺 `REA_JADX_MCP_JAR`，
`dsh`/`hermes`/`copilot_cli` 三处 MCP 注册 missing。

**JADX CLI 已通过 scoop 装好并投入使用**（`scripts/install-re-tools.ps1`
装 `java/temurin17-jdk` + `extras/jadx`，`scripts/uninstall-re-tools.ps1` 卸载）。
本文的 Java 级结论（`decodeToBytes`、`IOTBase64`、`passwordLogin` 字段、
`getLoginId` 裸 body）**均来自 JADX 反编译**，不再是手工字节码推测。

REA 的 MCP Android provider 仍不可用，因为它要求单独提供的
`jadx-headless-mcp 0.7.1` JAR（REA 不代装）；本轮改用 JADX CLI 直接反编译，
证据等价且已由实测接口校验。

## 8.2 Reproducing

## 9. Device list and control（设备列表与控制）

### 9.1 路由

**真机侧已验证（无 token 探测，签名通过、仅缺用户票据）**：

| 请求 | 生产网关应答 |
|---|---|
| `POST /v1/user/device/list` | `{"code":40002,"msg":"移动平台Token校验不通过 [user token not exist]"}` |
| `POST /v1/thing/properties/{code}` | 同上 `40002` |
| `POST /v1/thing/batch/properties` | 同上 `40002` |
| `POST /v1/thing/action/{code}` | 同上 `40002` |

这组应答同时证明三件事：路由真实存在、我们的 MAS 签名被接受、鉴权只依赖
`accessToken` 请求头。也就是说**真机控制的唯一剩余阻塞项就是用户 token**，
而不是签名或报文格式。

静态提取到的设备/家庭相关 alias：

| alias | 用途 |
|---|---|
| `GET /v1/user/device/list` | 账号下设备列表 |
| `GET /v1/appliance/home/list/get`、`/v1/appliance/home/list/aggregate` | 家庭首页聚合（房间 + 设备） |
| `GET /v1/appliance/home/list/cardOrder` | 卡片排序 |
| `POST /v1/thing/batch/properties` | **批量写属性**（控制主入口） |
| `GET /v1/thing/properties/{applianceCode}` | 读单个设备属性 |
| `GET /v1/thing/batch/properties?applianceCodes=` | 批量读属性 |
| `POST /v1/thing/action/{applianceCode}` | TSL 动作 |
| `GET /v1/thing/batch/history?requestId=` | 历史 |
| `/v1/appliance/mqtt/bind` | MQTT 绑定 |
| `/v1/appliance/all/control` | 全屋控制 |

### 9.2 控制请求体（高置信度，来自 bean 字段名）

`com.midea.business.datacenter.bean.req.NewTSLBatchPropertiesReq`：

```
applianceCode: long
props: List            # 元素为 {code, value}
disablePropIntegrityCheck: boolean
disablePropLegalityCheck: boolean
```

`com.midea.business.datacenter.bean.rsp.DeviceControlRsp`：

```
applianceCode, sn, onlineStatus, smartProductId, type, modelNumber, decodeSn
```

TSL 动作模型 `TSLAction { method, params, url }`；
UI 模型 `TSLStyleData { style, value, extral }`，
`TSLModelBase` 的模块类型常量：`JS_1`、`JS_ONE_BUTTON`、`JS_MULTI_BUTTON`、
`JS_ONE_SLIDER`、`JS_TWO_SLIDER`。

### 9.3 属性码来自服务端模板，不是硬编码

全量扫描 11 个 DEX：`"power"` 只出现 1 次、`"mode"` 3 次，其余属性名（`windSpeed`
`targetTemperature` `swingVertical` `brightness` …）**没有任何带引号的字面量**。

对应实现是运行时下载 TSL 模板：
`ITSLTemplateService` / `NewTSLTemplateApiService` → 落库 `TSLTemplateDao_Impl` →
`TSLTemplateManager` → `TSLModelService` 解析 → 按模块类型渲染控件。

**结论**：不同机型、不同固件的属性集合与取值范围由云端模板决定，客户端不含机型级
属性表。这也是本项目前端改成「按 schema 渲染」而不是写死空调面板的直接依据。

### 9.4 真正的控制通道是 MQTT，不是 HTTP

`DeviceControlService.controlDeviceState / sendDeviceData / sendDeviceDataBySN`
三条路径都先 `addDeviceInfoToMideaSDK(...)`，再回调进 **MideaSDK（native）**：

```
controlDeviceState  -> addDeviceInfoToMideaSDK -> subscribe(lambda: SDK 调用)
sendDeviceData      -> addDeviceInfoToMideaSDK -> subscribe(lambda: SDK 调用)
sendDeviceDataBySN  -> addDeviceInfoToMideaSDK -> subscribe(lambda: SDK 调用)
```

MQTT 侧线索：

- broker `prod-wsmqtt.smartmidea.net:8883`（多地域配置见 `assets/multi_cloud_host_config.json`）
- topic 族：`thing/set/properties`、`thing/properties/report`、
  `thing/properties/change`、`thing/action/response`、
  `thing/batch/query/properties/response`、`thing/query/properties/response`、
  `thing/card/update`、`thing/event/trigger`
- HTTP `/v1/thing/*` 是同一套 thing 模型的控制面旁路

因此「真机控制」需要：有效 accessToken + MQTT 绑定 + native SDK 的报文封装。
本轮只做到**协议形状级复现**，未实现真机下发。

### 9.5 本项目如何复现

`app/devices.py` 用 TSL 同构模型（`PropSchema{code,label,kind,min,max,step,options}` +
`props`）描述三台模拟设备，写属性时按 schema 校验；`app/device_api.py` 暴露与上表
同名同形的接口，前端按 `kind`（switch / enum / slider / number）渲染控件。

未实现（明确记录）：真机 MQTT 下发、TSL 模板拉取、`/v1/appliance/home/list/aggregate`
的房间分组。

