#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cron: 30 9 * * *
# new Env('什么值得买签到')
"""
什么值得买(SMZDM)每日签到脚本
适配青龙面板,凭据只读环境变量,纯标准库实现(无第三方依赖)。

功能:
  - 自动获取 Robot Token(user-api.smzdm.com/robot/token,md5 签名)
  - 每日签到(user-api.smzdm.com/checkin)
  - 查询签到奖励(user-api.smzdm.com/checkin/all_reward)
  - 自动识别: 签到成功 / 今日已签到 / Cookie 失效
  - 支持多账号(每行一条 Cookie)
  - 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

环境变量(在青龙面板配置):
  SMZDM_COOKIES           多账号,每行一条 Cookie(# 开头为注释,优先)
  SMZDM_COOKIE            单账号 Cookie
  PUSHPLUS_TOKEN          PushPlus 推送 token(可选)
  SENDKEY                 Server酱 SendKey(可选)

Cookie 获取:
  1. 浏览器登录 https://www.smzdm.com
  2. F12 → Application → Cookies → https://www.smzdm.com
  3. 至少复制 sess 字段;建议整段全复制(如 sess=xxx; smzdm_id=xxx)
  4. 推荐用移动端登录后的 Cookie(签到接口按 Android App 签名)
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("smzdm_android_V10.4.1 rv:841 (22021211RC;Android12;zh)smzdmapp")
# 固定签名密钥(SMZDM 客户端硬编码,公开可查)
SIGN_KEY = "apr1$AwP!wRRT$gJ/q.X24poeBInlUJC"
SK = "ierkM0OZZbsuBKLoAgQ6OJneLMXBQXmzX+LXkNTuKch8Ui2jGlahuFyWIzBiDq/L"

TOKEN_URL = "https://user-api.smzdm.com/robot/token"
CHECKIN_URL = "https://user-api.smzdm.com/checkin"
REWARD_URL = "https://user-api.smzdm.com/checkin/all_reward"


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
def _http(url, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, text)"""
    h = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://www.smzdm.com/",
    }
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        else:
            body = data.encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def _md5_sign(sign_str):
    """SMZDM 接口签名: md5 大写 hex"""
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()


# ---------------------------------------------------------------------------
# 签到
# ---------------------------------------------------------------------------
def get_robot_token(cookie_str):
    """获取 Robot Token,返回 (ok, token或错误信息)"""
    ts = int(round(time.time() * 1000))
    sign = _md5_sign("f=android&time=%s&v=10.4.1&weixin=1&key=%s" % (ts, SIGN_KEY))
    data = {
        "f": "android",
        "v": "10.4.1",
        "weixin": 1,
        "time": ts,
        "sign": sign,
    }
    hdrs = {
        "Host": "user-api.smzdm.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
    }
    st, txt = _http(TOKEN_URL, method="POST", data=data, headers=hdrs)
    if st != 200:
        return False, "HTTP %s: %s" % (st, txt[:200])
    try:
        d = json.loads(txt)
    except ValueError:
        return False, "返回非JSON: %s" % txt[:200]
    if str(d.get("error_code")) != "0":
        msg = d.get("error_msg") or "未知错误"
        if "token" in txt or "登录" in msg or "sess" in msg:
            return False, "Cookie失效: %s" % msg
        return False, "Token获取失败: %s" % msg
    token = d.get("data", {}).get("token")
    if not token:
        return False, "Token获取失败: 响应缺少 token"
    return True, token


def do_checkin(cookie_str, token):
    """执行签到,返回 (ok, msg, sign_data)"""
    ts = int(round(time.time() * 1000))
    sign = _md5_sign(
        "f=android&sk=%s&time=%s&token=%s&v=10.4.1&weixin=1&key=%s"
        % (SK, ts, token, SIGN_KEY)
    )
    data = {
        "f": "android",
        "v": "10.4.1",
        "sk": SK,
        "weixin": 1,
        "time": ts,
        "token": token,
        "sign": sign,
    }
    hdrs = {
        "Host": "user-api.smzdm.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
    }
    st, txt = _http(CHECKIN_URL, method="POST", data=data, headers=hdrs)
    if st != 200:
        return False, "HTTP %s: %s" % (st, txt[:200]), data
    try:
        d = json.loads(txt)
    except ValueError:
        return False, "返回非JSON: %s" % txt[:200], data
    ec = str(d.get("error_code", "-1"))
    msg = d.get("error_msg") or ""
    if ec == "0":
        return True, "签到成功", data
    if any(k in msg for k in ("已经", "重复", "已签")):
        return True, "今日已签到", data
    if ec != "0":
        return False, "Cookie失效: %s" % msg if msg else "签到失败(error_code=%s)" % ec, data
    return False, "签到失败: %s" % msg, data


def get_checkin_reward(cookie_str, sign_data):
    """查询签到奖励,返回 (ok, msg)"""
    hdrs = {
        "Host": "user-api.smzdm.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
    }
    st, txt = _http(REWARD_URL, method="POST", data=sign_data, headers=hdrs)
    if st != 200:
        return False, ""
    try:
        d = json.loads(txt)
    except ValueError:
        return False, ""
    if str(d.get("error_code")) != "0":
        return False, ""
    data = d.get("data", {})
    reward = data.get("normal_reward", {})
    content = (reward.get("reward_add") or {}).get("content", "")
    sub = reward.get("sub_title", "")
    parts = [p for p in (content, sub) if p]
    return True, " ".join(parts)


def sign_in(cookie_str):
    """完整签到流程,返回 (ok, msg)"""
    ok, result = get_robot_token(cookie_str)
    if not ok:
        return False, result
    token = result

    ok, msg, sign_data = do_checkin(cookie_str, token)
    if not ok:
        return False, msg

    # 签到成功后查询奖励(失败不影响主结果)
    r_ok, r_msg = get_checkin_reward(cookie_str, sign_data)
    if r_ok and r_msg:
        msg = "%s,奖励: %s" % (msg, r_msg)
    return True, msg


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------
def ql_notify(title, content):
    """青龙面板内置通知"""
    try:
        sys.path.insert(0, "/ql/scripts")
        from notify import send  # noqa: PLC0415
        send(title, content)
        return True
    except Exception:  # noqa: BLE001
        try:
            subprocess.run(
                ["ql", "notify", "%s\n%s" % (title, content)],
                capture_output=True, timeout=30,
            )
            return True
        except Exception:  # noqa: BLE001
            return False


def pushplus_notify(title, content):
    tok = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not tok:
        return False
    try:
        st, txt = _http(
            "http://www.pushplus.plus/send",
            method="POST",
            data={"token": tok, "title": title, "content": content},
            headers={"Content-Type": "application/json"},
        )
        return st == 200
    except Exception:  # noqa: BLE001
        return False


def serverchan_notify(title, content):
    key = os.environ.get("SENDKEY", "").strip()
    if not key:
        return False
    try:
        url = "https://sctapi.ftqq.com/%s.send" % key
        body = urllib.parse.urlencode({"title": title, "desp": content})
        req = urllib.request.Request(
            url, data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def send_notify(title, content):
    """按优先级通知: 青龙 -> PushPlus -> Server酱"""
    if ql_notify(title, content):
        return "青龙通知"
    if pushplus_notify(title, content):
        return "PushPlus"
    if serverchan_notify(title, content):
        return "Server酱"
    return "无可用通知渠道"


# ---------------------------------------------------------------------------
# 账号解析
# ---------------------------------------------------------------------------
def parse_accounts():
    """优先 SMZDM_COOKIES(多账号, 每行一条);否则 SMZDM_COOKIE(单账号)"""
    accounts = []
    multi = os.environ.get("SMZDM_COOKIES", "").strip()
    if multi:
        for line in multi.splitlines():
            line = line.strip().strip("'").strip('"')
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                accounts.append(line)
    else:
        ck = os.environ.get("SMZDM_COOKIE", "").strip().strip("'").strip('"')
        if ck:
            accounts.append(ck)
    return accounts


def mask_cookie(cookie_str):
    """脱敏展示 Cookie,仅显示字段名与值首尾"""
    parts = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            v = v.strip()
            if len(v) > 10:
                v = v[:6] + "***" + v[-4:]
            parts.append("%s=%s" % (k, v))
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    accounts = parse_accounts()
    if not accounts:
        print("未配置 Cookie。请在青龙面板环境变量中设置:")
        print("  多账号: SMZDM_COOKIES (每行一条 Cookie)")
        print("  单账号: SMZDM_COOKIE")
        sys.exit(1)

    print("=" * 50)
    print("什么值得买签到开始,共 %d 个账号" % len(accounts))
    print("=" * 50)

    all_lines = []
    ok_count = 0
    for i, ck in enumerate(accounts, 1):
        label = "账号%d(%s)" % (i, mask_cookie(ck))
        print("\n>>> %s" % label)
        ok, msg = sign_in(ck)
        icon = "✅" if ok else "❌"
        print("  %s %s" % (icon, msg))
        all_lines.append("【账号%d】%s %s" % (i, icon, msg))
        if ok:
            ok_count += 1
        time.sleep(random.uniform(1, 3))

    summary = "\n".join(all_lines)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)

    title = "什么值得买签到: %d/%d 账号成功" % (ok_count, len(accounts))
    channel = send_notify(title, summary)
    print("\n通知渠道: %s" % channel)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
