# Personal Scripts — 自用脚本仓库

个人日常自动化脚本集合,适配青龙面板(Qinglong)。

## 脚本列表

| 脚本 | 说明 |
|---|---|
| `139_yunduo.py` | 中国移动云盘(139云盘)云朵任务自动执行 |

---

## 139 云盘云朵任务自动执行(`139_yunduo.py`)

自动完成中国移动云盘「云朵中心」中可纯接口执行的任务,运行结束发送通知总结。

### 功能

- 自动认证链:querySpecToken → ssoToken → tyrzLogin → jwtToken(全自动,无需手动换 token)
- **AI豆余额统计**:读取当前 AI豆余额,并与上次运行对比,计算"本次获取多少云朵"
- **每日自动签到**:`startSignIn` 接口签到(+3豆,自动判断今日是否已签,避免重复)
- 拉取云朵任务列表(`taskListV3`,含每任务豆数说明)
- 自动完成纯接口即可完成的任务:
  - **319 小云互动礼** — 循环点击直至完成,每次送云朵
  - **118 调查问卷** — 单次点击即完成
  - **106 手动上传一个文件** — 自动上传小文件触发完成,完成后自动删除文件(可关)
- 可选尝试所有未完成任务(`YDYP_TRY_ALL=true`)可额外完成:
  - 605 体验 MClaw 智能助手
  - 606 和 AI 助手对话
  - 431 去使用移动云手机
- 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

### 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `YDYP_PHONE` | 二选一 | 单账号手机号 |
| `YDYP_AUTH` | 二选一 | 单账号 Authorization 值(`Basic` 开头,可省略 `Basic` 前缀) |
| `YDYP_ACCOUNTS` | 二选一 | 多账号,每行一个 `手机号\|Basic <token>`,`#` 开头为注释(优先于单账号) |
| `YDYP_TRY_ALL` | 否 | `true` 时尝试所有未完成任务,默认 `false` |
| `YDYP_INTERACT_LIMIT` | 否 | 小云互动礼最大点击次数,默认 `30` |
| `YDYP_UPLOAD` | 否 | 是否执行上传任务(106),默认 `true` |
| `YDYP_DELETE_AFTER` | 否 | 上传完成后是否删除该文件,默认 `true` |
| `YDYP_STATE_FILE` | 否 | 余额历史记录文件路径,默认脚本目录下 `139_yunduo_state.json` |
| `PUSHPLUS_TOKEN` | 否 | PushPlus 推送 token |
| `SENDKEY` | 否 | Server酱 SendKey |

### 如何获取 token

1. 电脑浏览器**登录** <https://yun.139.com> (网页版)
2. 打开开发者工具(F12) → 网络(Network)
3. 刷新页面,任选一个 `api.yun.139.com` 或 `yun.139.com` 的请求
4. 查看请求头 **Authorization** 字段(形如 `Basic xxxxxx`)
5. 将该值连同手机号填入环境变量

### 青龙面板配置

1. 将 `139_yunduo.py` 放入青龙面板的 **脚本管理**,路径建议 `scripts/` 或自建目录
2. 在青龙面板 **环境变量** 中新增:
   - 单账号:`YDYP_PHONE=13800138000`、链 `YDYP_AUTH=Basic xxxxxx`
   - 或多账号:`YDYP_ACCOUNTS`(值内换行可拆分账号,每行 `手机号|Basic token`)
3. 新建定时任务:
   - 命令:`python3 139_yunduo.py`(脚本所在目录需在命令中引用,如 `task /scripts/139_yunduo.py`)
   - 定时规则示例:`0 9 * * *`(每天 9 点)
   - 尽量设置在**月初/周末**,因部分任务有每周/每月周期
4. 青龙内置通知:脚本会自动调用青龙自带的通知推送,无需额外配置

### 多账号格式(`YDYP_ACCOUNTS`)

```text
13800138000|Basic BASE64TOKEN_AAA
13900139000|Basic BASE64TOKEN_BBB
```

### 本机运行示例

```bash
# 单账号
YDYP_PHONE=13800138000 YDYP_AUTH=Basic xxxxxx python3 139_yunduo.py

# 多账号,尝试所有任务
YDYP_ACCOUNTS=$'13800138000|Basic AAA\n13900139000|Basic BBB' \
YDYP_TRY_ALL=true python3 139_yunduo.py
```

### 已知限制

- **AI豆余额统计**:首次运行只记录余额,运行两次后才会显示"较上次 +N"。任务奖励需在云朵中心手动领取后才会计入余额(每周日-周六未领的豆次周周日24:00失效)。
- 任务如 434(分享文件)、409(访问云朵中心)、113(PC客户端)、604(云盘图书馆阅读)需要真实人工操作,纯接口点击无法完成 `FINISH`。
- 上传任务(106)上传的文件默认会自动删除(`YDYP_DELETE_AFTER=true`);若关闭,文件会保留在云盘根目录。

### 免责声明

本脚本仅用于个人学习与自动化测试。使用涉及的真实账号、文件等一切后果由使用者自行承担,请勿用于商业用途或违法用途。