# 美居 APK 登录功能逆向报告

目标：`美的美居_11.5.11.4_APKPure.apk`
（`SHA-256 E1D81B800E938D42976251432EE819856368F95ECDBBC52A70ABED6C0D556758`，
包名 `com.midea.ai.appliances`）。

工具：JADX 1.5.6（JDK 17）、Ghidra 12.2（JDK 25）、自建 DEX 阅读器
（`meiju-analysis/dexdump.py`）、生产网关实测。

---

## 1. 结论摘要

| 登录方式 | 端点 | 静态完整性 | 实测状态 |
|---|---|---|---|
| 密码登录 | `/v6/mj/user/login` | **字段与公式全部还原** | `1106`（native 哈希不可复现） |
| 短信验证码 | `/mj/user/getSmsCode` + `/mj/user/mobileVerify` | 完整 | ✅ `code 0`，但只返回 `randomCode`，**不是登录票据** |
| 本机号码一键登录 | `/v6/mj/user/onekey/auth` | 完整 | 需要极光 JVerification 的运营商 token，无法离线伪造 |
| 第三方登录 | `/v6/mj/user/third/login` | 完整 | 需要微信/QQ 的 `tAccessToken` |
| 自动登录 | `/mj/user/autoLogin` | 完整 | 需要此前登录拿到的 `tokenPwd` |
| 注册 / 重置密码 | `/v6/mj/user/register`、`/mj/user/resetPassword` | 完整 | 短信验证后可设密码（**会改账号密码**） |

**核心结论：这个 APK 没有"短信验证码直接登录"。** 短信验证码只用于
注册、重置密码、改绑手机号三条支线；`randomCode` 唯一的去处是这两个接口。

---

## 2. 登录入口与路由（来自 Retrofit 注解 + JADX）

`LoginApi` 的 15 个方法，别名由 DEX 注解静态读出（`meiju-analysis/annotations.py`）：

| 方法 | alias |
|---|---|
| `getSmsCode` | `/mj/user/getSmsCode` |
| `verifyMobile` | `/mj/user/mobileVerify` |
| `mobileVerifyLogin` | `/v6/mj/user/onekey/auth` |
| `mobileVerifyThirdBind` | `/v6/mj/user/onekey/bind` |
| `passwordLogin` | `/v6/mj/user/login` |
| `getLoginId` | `/v1/user/login/id/get` |
| `mobileRegister` | `/v6/mj/user/register` |
| `mobileRegisterBind` | `/v6/mj/user/mobile/register/bind` |
| `thirdLogin` | `/v6/mj/user/third/login` |
| `changePassword` | `/mj/user/changePassword` |
| `resetPassword` | `/mj/user/resetPassword` |
| `modifyMobile` | `/v1/user/mobile/modify` |
| `logout` | `/mj/user/logout` |
| `logoutOtherDevice` | `/v1/user/multi/logout/otherDevice` |
| `updateTokenPwdMobile` | `/v6/mj/user/update/tokenPwd/mobile` |

界面与调用的对应关系（JADX 全量 `classes7`，3043 个类）：

```
MobileInputActivity.getSmsCode() ──成功──> goSMSCodeVerify()
        └─ 路由 /login/register_next（RegisterNextActivity），带 key-type=mType
RegisterNextActivity.toNext(randomCode)
        ├─ mType=7  -> 改绑手机号
        ├─ mType=12 -> 第三方绑定
        └─ mType=3/2 -> RegisterSetpwdActivity（设置密码）
        └─ 错误码 1104（账号已存在）-> showPhoneAlreadyExistsDialog()

MobileVerifyActivity.login() -> JVerificationHelper.getLoginToken()
        -> requestLogin(token) -> mobileVerifyLogin -> /v6/mj/user/onekey/auth
        （该页没有任何短信输入框）

LoginActivity.login() -> getLoginId(account) -> passwordLogin(account, pwd, loginId, dxToken)
ResetPwdActivity -> getSmsCode(mobile, 9, ...) -> verifyMobile(type=9)
        -> resetPassword(mobile, password, randomCode)
```

---

## 3. 密码登录（还原度最高的一条）

### 3.1 `IotData` 构造参数真实顺序

`IotData` 有 27 个字段，但**主构造函数只有 25 个参数**（`reqId`/`stamp` 在构造体内
自动生成，不是参数）。顺序由构造函数体赋值逐行确定，与字段字母序**无关**：

| # | 字段 | # | 字段 | # | 字段 |
|---|---|---|---|---|---|
| 1 | type | 10 | newIampwd | 19 | tNickname |
| 2 | mobile | 11 | randomCode | 20 | sex |
| 3 | src | 12 | clientType | 21 | headPicUrl |
| 4 | iotAppId | 13 | dxToken | 22 | birthDate |
| 5 | loginAccount | 14 | appVersion | 23 | loginToken |
| 6 | smsCode | 15 | deviceId | 24 | loginType |
| 7 | password | 16 | openUid | 25 | jPushAppCode |
| 8 | newPassword | 17 | tAccessToken | | |
| 9 | iampwd | 18 | force | | |

构造体内：`reqId = UUID.randomUUID().toString().replace("-","")`，
`stamp = new SimpleDateFormat("yyyyMMddHHmmss").format(new Date())`。

### 3.2 密码登录的真实报文

```java
passwordLogin(account, password, loginId, dxToken)   // dxToken = 风控验证码 token
  iotData.loginAccount = account
  iotData.loginId       = loginId                     // 先由 getLoginId(account) 换取
  iotData.password      = SHA256(loginId + SHA256(pwd) + IOT_KEY)   // getEncryptedPwd
  iotData.iampwd        = MD5(MD5(pwd))                            // getIamPwd
  iotData.iotAppId      = "900"
  iotData.clientType    = 1
  iotData.type / mobile / loginType = null（不传）
  data.platform         = 2，data.mobile 不传
```

`getLoginId(account)` 是**裸 JSON**（不是 `CommonReqData` 外壳）：

```json
{"loginAccount": "<account>", "reqId": "<uuid>", "stamp": "yyyyMMddHHmmss"}
```

实测：`{"code":0,"msg":null,"data":{"loginId":"73e0c3321d1a43ba9e684f63a62ebd98"}}`。
早前一直 `40500` 的原因就是把账号塞进了 `data/iotData` 外壳。

### 3.3 实测结果与原因

用真实账号、真实密码（含一个临时密码）各试一次，均返回：

```json
{"code":1106,"msg":"Authenticate failed, username or password is incorrect","data":null}
```

`1106` 在 `LoginActivity` 中对应「手机号或密码不正确」，**与图形验证码无关**。
（`1019`/`1020` 才弹验证码；`1018` 是密码错误次数过多锁 30 分钟。）

原因：`password` / `iampwd` 两处哈希都来自 native：

```
SecurityUtils.encodeSHA256(s) = UTILS.encodeSHA(s).toLowerCase()   ← 显式小写
SecurityUtils.encodeMD5(s)    = UTILS.encodeMD5(s)                  ← 无小写处理
UTILS = EncodeAndDecodeUtils.getInstance()  →  全部 native 方法
```

`iampwd` 的入参大小写、是否加盐、是否变体，全部在 native 里。

---

## 4. native 哈希为什么无法静态还原（三重证据）

用 Ghidra 12.2 反汇编 `libKwProtectSDK.so`（arm64-v8a）：

1. **23/23 个 `EncodeAndDecodeUtils` / `getIotSecret` JNI 函数都是控制流平坦化派发器**
   （`meiju-analysis/ghidra-iot-hashes.log`）：先写上百个 `DAT_*` 全局，再
   `(*(code *)(DAT_x + imm))()` 间接跳转；23 处
   "Could not recover jumptable"，4377 处全局写。
2. **函数体内没有任何 MD5/SHA 初始常量**（`67452301`/`efcdab89`/`98badcfe`/`10325476` 缺席）。
3. 全库字节扫描（`meiju-analysis/scan_hash_constants.py`）：
   - MD5 IV 命中 1 处（文件偏移 `0x2ffc20`，虚拟地址 `0x3ffc20`）；
   - **MD5 的 64 项 K 常量表完全不存在** → 不可能是常规 MD5 实现；
   - SHA-1 / SHA-256 的 IV 与 K 表均不存在。
   对该 IV 的唯一引用位于 `0x3df990`，Ghidra 未识别为函数；强制建函数反编译后
   只有一串 `undefined8` 栈变量——即该地址在 VM 解释器内部。

**结论：`encodeMD5`/`encodeSHA` 的算法在库自带的字节码 VM 里，静态反汇编不可还原。**
要拿到它，只能在 ARM64 上动态执行该库（QEMU/Android 模拟器 + JNI 桩），属于独立工程。

---

## 5. `IOT_KEY` 已经还原

`HttpUtil.IOT_KEY = IEncryption.decode(HTTP_SERVER_IOT_SECRET)`，
而 `IOTPWManager` 是**纯 Java**，用其自身算法即可解：

```java
decodeToBytes(str) = pwReplace(
    new IOTBase64().decode(
        new String(new IOTAES("rockykaikainihao").InvCipher(<64字节常量>)).toCharArray(),  // 字母表
        str))                                                                            // 数据
```

三处易错点：64 字节常量是**字母表密文**；`IOTAES` 用**非标准 S-box**；
`pwReplace` 除换位还按 `index%32` 做 `^/+/-`。

验证方式：从 DEX 取出两张 256 项表（`extract_iotaes_tables.py`），按 JADX 逻辑生成
Java 程序（`gen_iot_harness.py` → `harness/IotHarness.java`）跑真实算法，得字母表

```
tuvwxTUlmnopqrs7YZhbdecafgij8yz6543210VWXkABCDEFGHIJKLMNOPQRS9*/
```

再以「解出来必须是可打印 ASCII」为筛子扫描 `libKwProtectSDK.so` 的 139,626 个字符串，
仅 6 个命中，其中三个是环境密钥：

| 密文（库内） | 明文 IOT_KEY |
|---|---|
| `YMLoZ5tTttyYcKSPdJe13w0Gie5tTYtyzZ1tPe9pjXO=` | `PROD_VnoClJI9aikS8dyy` |
| `gUfHr5tTttybYTZdha9L5MHGZTGtTYtytu1tPbZpiW1=` | `SIT_4VjZdg19laDoIrut` |
| `r0efyYtTttyxbTgdfMTpzVL0ewdtTYtytu1tPeZlhXK=` | `DEV_TZJn6TXxmtTHclKk` |

代码：`app/providers/midea.py` 的 `IOT_KEY_PROD/SIT/DEV`，注入用 `MIDEA_IOT_KEY`。

---

## 6. 图形验证码与风控码

`LoginActivity` 使用第三方 `com.midea.service.captcha.MJCaptchaDialog`，
`appId = e83bb4b2069d516f9f0798584e0ac2c1`；验证通过的回调 token 作为 `dxToken`
传入 `passwordLogin`。

| 错误码 | 文案 | 处理 |
|---|---|---|
| 1018 | 密码错误次数过多，请 30 分钟后重试 | 不弹 |
| 1019 | 验证失败，请稍后再试（captcha token 失效） | 弹验证码 |
| 1020 | 输入密码次数超过上限 | 弹验证码 |
| 1105 | 手机号未注册 | — |
| 1106 | 手机号或密码不正确 | — |

---

## 7. 拿到真实 token 的三条路径（代码均已就绪）

1. **重置密码后登录（自洽，推荐）**
   `getSmsCode(type=9)` → `mobileVerify(type=9)` → `randomCode` →
   `resetPassword(mobile, pwd, randomCode)` → `passwordLogin`。
   `resetPassword` 只发 `SHA256(pwd)` 与 `MD5(MD5(pwd))`，**不经过 IOT_KEY**，
   全部由本地计算；服务端存的就是我们算的值，因此后续登录不再受 native 哈希影响。
   代码：`MideaProvider.reset_and_login()`、`POST /v1/login/reset-password`。
   **代价：会改动账号密码**（可设成用户已有的临时密码，则表面无变化）。
2. **注入官方 App 的 accessToken**（不改账号）
   `MIDEA_ACCESS_TOKEN` 或 `POST /v1/session/real-token`；
   之后 `/v1/user/device/list`、`/v1/thing/*` 自动转发到美的云端。
3. **ARM64 动态执行 native 库**（不改账号，工程量大）
   QEMU/Android 模拟器 + JNI 桩，直接调 `encodeMD5`/`encodeSHA` 得到真实哈希。

---

## 8. 交叉验证：HA 集成（midea_ac_lan）的实现

国内开源集成 `georgezhao2010/midea_ac_lan` 用的是同一套网关，但**三个密钥的
归属与我最初的判断相反**。取 `midea/core/cloud.py` 的 `clouds["美的美居"]`：

```
app_id    = "900"        app_key  = "46579c15"
login_key = "ad0ee21d48a64bf49f4fb583ab76e799"     ← 密码哈希用这个
iot_key   = "prod_secret123@muc"                   ← 我之前当「MAS 签名密钥」
hmac_key  = "PROD_VnoClJI9aikS8dyy"                ← 我之前当「IOT_KEY」
```

（后两者由大整数转十六进制得到，与我在 `.so` 里扫出的字符串一致。）

HA 的登录实现（生产可用）与我此前的差异：

| 项 | 我的实现 | HA / 正确实现 |
|---|---|---|
| 端点 | `/v6/mj/user/login` | **`/mj/user/login`** |
| 密码哈希 key | 误用 iot/hmac key | **`login_key`** |
| `iampwd` | 曾误改大写 | **`MD5(MD5(pwd))` 小写** |
| `data.appKey` | 空串 | **`46579c15`** |
| 签名 | md5(secret+body+params+random) | **HMAC-SHA256(hmac_key, iot_key+json+random)** |

按 HA 公式重写后（`meiju-lan/cn_login.py`），`/v1/user/login/id/get` 返回
`code 0`（签名被接受），`/mj/user/login` 仍返回 `1106`
——也就是说**请求构造已经正确，卡在账号密码本身**。

## 9. 局域网直控（绕开云登录的可行路径）

同一局域网的美的设备可用 UDP 6445 发现。实测本机 `10.0.0.0/24` 扫到 **5 台**，
全部为 **V3 协议**（AES，需要每台设备各自的 `token`+`key`）：

| IP | SN | 设备类型 |
|---|---|---|
| 10.0.0.217 | 0000CA31…FM1D | 202 |
| 10.0.0.116 | 0000B621…KF6P | 182 |
| 10.0.0.153 | 0000E321…X25Z | 227 |
| 10.0.0.160 | 0000DC31…CSU2 | 220 |
| 10.0.0.213 | 0000E131…TBU | 225 |

V3 的 `token`/`key` 由云接口 `/v1/iot/secure/getToken` 按 `udpid` 下发
（`udpid = SHA256(applianceId.to_bytes(6,big/little))` 前 16 字节折半异或）。
拿到后即可**完全离线**控制，不再需要任何云端调用。
`getToken` 需要已登录会话，因此仍需先解决登录。

## 10. 状态与控制的通道（实测排除记录）

登录拿到真实会话后，逐条验证了设备状态/控制的可能通道：

| 通道 | 端点/方法 | 实测结果 |
|---|---|---|
| thing 属性（GET） | `GET /v1/thing/properties/{code}?requestId=..&sync=0` | HTTP 200，只回 `{"requestId":...}`，**无数据** |
| thing 批量属性 | `PUT /v1/thing/batch/properties` | 未使用（读就空） |
| 批量状态 | `POST /v1/appliance/batch/getStatus {"applianceCodes":[id]}` | `code 0`，但 `status:{}` |
| LAN 密钥 | `POST /v1/iot/secure/getToken {"udpid":..}` | **40404 访问地址不存在**（secretVersion 1/2/3 均如此） |
| 在线控制 token | `POST /v1/appliance/remote/control/online/token/get {"snList":[sn]}` | `code 0`，`data: []`（空） |
| 首页卡片控制 | `POST /v1/appliance/all/control` | `AllControlReq(homegroupId, cardId, roomId, type, Properties)`，`Properties(power, targetTemperature, workMode)` —— **空调卡片专用** |
| MQTT | `prod-wsmqtt.smartmidea.net:8883` | 客户端在 native SDK 内，Java 只传域名（`MqttInfoManager.getMqttDomain()`），凭据无明文 |

设备能力字段解释了「读不到」：13 台设备全部
`supportThing=None`、`supportWot=False`、`isSupportFetchStatus=0`。

而 APK 里存在 `SingleBlueToothModule` / `WeexBleDeviceStatusManager`
（`singleble` 包）与「面板网关 0x1A」设备——说明这批家电的实际控制通道是
**BLE 网状网 + 网关代理**，云端只做转发，因此 HTTP 层拿不到状态。

## 11. 未解问题（明确列出）

- `encodeMD5` / `encodeSHA` 的确切算法（大小写、盐、变体）——被 VM 保护。
  （注：`login_key` 路径已用 HA 公式绕过，密码登录已打通，此项不再阻碍登录。）
- `getMUCAppKey()`（`MucData.appKey`）是 native；美居这条链实测用 `46579c15` 即可。
- `onekey/auth` 所需的 JVerification token 由极光 SDK 在真机上生成，无法离线构造。
- `randomCode` 是否还有未发现的消费者（已确认的只有 register/resetPassword）。
- **设备状态/控制的 HTTP 通道**：这批设备的 `isSupportFetchStatus=0`，
  云端不提供状态；真实通道为 BLE 网关（见 §10），尚未逆向。
