#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
139云盘(中国移动云盘) 云朵任务自动执行脚本
适配青龙面板,纯 Python 标准库实现,无需 pip 安装任何依赖。

功能:
  - 自动获取 SSO Token 与 JWT Token(认证链)
  - 拉取云朵任务列表(sign_in_3)
  - 自动完成可纯接口完成的任务:
      * 319 小云互动礼(循环点击直至完成,送云朵)
      * 118 调查问卷(单次点击即完成)
  - 可选尝试所有未完成任务(YDYP_TRY_ALL=true)
  - 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

环境变量:
  YDYP_ACCOUNTS  多账号,每行一个,格式: 手机号|Basic <token> (优先)
  YDYP_PHONE     单账号手机号 (与 YDYP_AUTH 配合)
  YDYP_AUTH      单账号 Authorization 值(Basic 开头,可省略 Basic 前缀)
  YDYP_TRY_ALL   是否尝试所有未完成任务,默认 false
  YDYP_INTERACT_LIMIT  小云互动礼最大点击次数,默认 30
  PUSHPLUS_TOKEN PushPlus 推送 token(可选)
  SENDKEY        Server酱 SendKey(可选)
"""
import base64
import hashlib
import json
import os
import random
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

BASE_URL = "https://yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken"
TYRZ_URL = "https://caiyun.feixin.10086.cn/portal/auth/tyrzLogin.action"
TASKLIST_URL = "https://caiyun.feixin.10086.cn/market/signin/task/taskList"
CLICK_URL = "https://caiyun.feixin.10086.cn/market/signin/task/click"

MARKET_NAME = "sign_in_3"

# 纯接口即可完成的任务(其余任务需要真实操作:上传/分享/PC端/阅读等)
AUTO_TASKS = {
    319: "小云互动礼",   # 循环点击直至 FINISH,每次送云朵
    118: "调查问卷",     # 单次点击即 FINISH
}


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
def _http(url, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, text)"""
    h = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://yun.139.com/w/",
    }
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            h.setdefault("Content-Type", "application/json;charset=UTF-8")
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


def _basic(phone, auth_token):
    """
    构造 Authorization 值。
    用户提供的 token 通常已是 base64("pc:phone:authToken") 完整值;
    若为裸 authToken 则自动包装。
    """
    t = auth_token.strip()
    # 尝试解码,若以 pc: 开头说明已是完整 base64 值
    try:
        dec = base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "replace")
        if dec.startswith("pc:"):
            return "Basic " + t
    except Exception:  # noqa: BLE001
        pass
    return "Basic " + base64.b64encode(
        ("pc:%s:%s" % (phone, t)).encode("utf-8")
    ).decode("utf-8")


def sanitize_token(val):
    """去掉 Basic 前缀、引号与空白"""
    if not val:
        return val
    v = val.strip().strip("'").strip('"')
    if v.lower().startswith("basic "):
        v = v[6:].strip()
    return v


# ---------------------------------------------------------------------------
# 认证链: SSO Token -> JWT Token
# ---------------------------------------------------------------------------
def get_sso_token(phone, basic):
    """POST querySpecToken -> ssoToken"""
    payload = {
        "toSourceId": "001005",
        "account": phone,
        "commonAccountInfo": {"account": phone, "accountType": 1},
    }
    hdrs = {"Authorization": basic, "Content-Type": "application/json;charset=UTF-8"}
    st, txt = _http(BASE_URL, method="POST", data=payload, headers=hdrs)
    if st != 200:
        return None, "SSO接口HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "SSO返回非JSON"
    if not d.get("success") and d.get("code") != "0":
        return None, "SSO失败: %s %s" % (d.get("code"), d.get("message", ""))
    tok = (d.get("data") or {}).get("token")
    if not tok:
        return None, "SSO无token字段"
    return tok, None


def get_jwt_token(sso):
    """GET tyrzLogin.action?ssoToken= -> jwtToken"""
    url = TYRZ_URL + "?ssoToken=" + urllib.parse.quote(sso, safe="")
    st, txt = _http(url, method="GET")
    if st != 200:
        return None, "JWT接口HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "JWT返回非JSON"
    if d.get("code") != 0:
        return None, "JWT失败: %s %s" % (d.get("code"), d.get("msg", ""))
    tok = (d.get("result") or {}).get("token")
    if not tok:
        return None, "JWT无token字段"
    return tok, None


def jwt_headers(jwt):
    """任务接口需要的 jwt 双头"""
    return {"Cookie": "jwtToken=%s" % jwt, "jwtToken": jwt}


# ---------------------------------------------------------------------------
# 任务接口
# ---------------------------------------------------------------------------
def get_task_list(jwt, market=MARKET_NAME):
    """返回任务列表 dict: id -> task dict; 或 (None, err)"""
    hdrs = jwt_headers(jwt)
    url = "%s?marketname=%s&clientVersion=" % (TASKLIST_URL, market)
    st, txt = _http(url, method="GET", headers=hdrs)
    if st != 200:
        return None, "taskList HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "taskList返回非JSON"
    if d.get("code") != 0:
        return None, "taskList失败: %s %s" % (d.get("code"), d.get("msg", ""))
    result = d.get("result") or {}
    tasks = {}
    groups = {}
    if isinstance(result, dict):
        for gname, lst in result.items():
            if not isinstance(lst, list):
                continue
            groups[gname] = lst
            for t in lst:
                if isinstance(t, dict) and t.get("id") is not None:
                    tasks[t.get("id")] = t
    elif isinstance(result, list):
        for grp in result:
            if not isinstance(grp, dict):
                continue
            gname = grp.get("group") or grp.get("groupid") or "other"
            lst = grp.get("taskList") or grp.get("list") or []
            groups[gname] = lst
            for t in lst:
                if isinstance(t, dict) and t.get("id") is not None:
                    tasks[t.get("id")] = t
    return {"tasks": tasks, "groups": groups}, None


def click_task(jwt, tid):
    """点击任务,返回 (ok, msg)"""
    url = "%s?key=task&id=%s" % (CLICK_URL, tid)
    st, txt = _http(url, method="GET", headers=jwt_headers(jwt))
    if st != 200:
        return False, "click HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return False, "click返回非JSON"
    if d.get("code") == 0:
        result = d.get("result") or d.get("data") or ""
        return True, str(result) if result else "success"
    return False, "click失败: %s %s" % (d.get("code"), d.get("msg", ""))


# ---------------------------------------------------------------------------
# 单账号执行
# ---------------------------------------------------------------------------
def run_account(phone, auth_token, try_all=False, interact_limit=30):
    """
    执行一个账号的全部云朵任务。
    返回 (ok, lines)  lines 为总结行列表
    """
    lines = []
    phone_masked = phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else phone

    # 1) 认证
    basic = _basic(phone, auth_token)
    sso, err = get_sso_token(phone, basic)
    if not sso:
        lines.append("❌ %s 认证失败(SSO): %s" % (phone_masked, err))
        return False, lines
    jwt, err = get_jwt_token(sso)
    if not jwt:
        lines.append("❌ %s 认证失败(JWT): %s" % (phone_masked, err))
        return False, lines
    lines.append("✅ %s 认证成功" % phone_masked)

    # 2) 任务列表
    data, err = get_task_list(jwt)
    if err:
        lines.append("❌ %s 拉取任务失败: %s" % (phone_masked, err))
        return False, lines
    tasks = data["tasks"]

    if not tasks:
        lines.append("ℹ️ %s 无任务数据" % phone_masked)
        return True, lines

    # 3) 执行任务
    finished = 0
    wait_ids = []

    def state_of(t):
        return t.get("state", "WAIT") or "WAIT"

    import re as _re
    for tid, t in sorted(tasks.items(), key=lambda kv: kv[1].get("sort", 0) or 0):
        raw_name = t.get("name") or str(tid)
        # 清理任务名中的 HTML 标签(如 span)
        name = _re.sub(r"<[^>]+>", "", raw_name).strip()
        if state_of(t) == "FINISH":
            finished += 1
            lines.append("✅ 任务 %s[%s] 已完成" % (name, tid))
            continue
        if t.get("enable") == 0:
            lines.append("🚫 任务 %s[%s] 已停用" % (name, tid))
            continue

        if tid == 319:  # 小云互动礼: 循环点击
            clicked = 0
            reward = None
            while clicked < interact_limit:
                ok, msg = click_task(jwt, tid)
                clicked += 1
                if ok and msg and ("云朵" in msg or "云" in msg):
                    reward = msg
                time.sleep(0.3)
                # 每 5 次检查一次状态
                if clicked % 5 == 0:
                    d2, _ = get_task_list(jwt)
                    if d2 and tid in d2["tasks"]:
                        t = d2["tasks"][tid]
                if state_of(t) == "FINISH":
                    break
            if state_of(t) == "FINISH":
                finished += 1
                extra = (" (%s)" % reward) if reward else ""
                lines.append("✅ 任务 %s[%s] 完成,点击%d次%s" % (name, tid, clicked, extra))
            else:
                wait_ids.append(tid)
                lines.append("⏳ 任务 %s[%s] 点击%d次未完成,需手动" % (name, tid, clicked))

        elif tid in AUTO_TASKS:  # 其他纯点击任务(118等)
            ok, msg = click_task(jwt, tid)
            time.sleep(0.3)
            d2, _ = get_task_list(jwt)
            if d2 and tid in d2["tasks"]:
                t = d2["tasks"][tid]
            if ok and state_of(t) == "FINISH":
                finished += 1
                lines.append("✅ 任务 %s[%s] 完成" % (name, tid))
            elif ok:
                wait_ids.append(tid)
                lines.append("⏳ 任务 %s[%s] 已点击但未完成" % (name, tid))
            else:
                wait_ids.append(tid)
                lines.append("❌ 任务 %s[%s] 失败: %s" % (name, tid, msg))

        elif try_all:  # 尝试所有未完成任务
            ok, msg = click_task(jwt, tid)
            time.sleep(0.3)
            d2, _ = get_task_list(jwt)
            if d2 and tid in d2["tasks"]:
                t = d2["tasks"][tid]
            if ok and state_of(t) == "FINISH":
                finished += 1
                lines.append("✅ 任务 %s[%s] 完成" % (name, tid))
            else:
                wait_ids.append(tid)
                lines.append("⏳ 任务 %s[%s] 需要真实操作(上传/分享/PC端等)" % (name, tid))
        else:
            wait_ids.append(tid)
            lines.append("⏳ 任务 %s[%s] 需要真实操作,未尝试" % (name, tid))

    # 4) 汇总
    total = len(tasks)
    lines.append("📊 %s 任务汇总: 完成 %d/%d" % (phone_masked, finished, total))
    return True, lines


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
    """
    优先 YDYP_ACCOUNTS(多账号, 每行 手机号|Basic <token>);
    否则 YDYP_PHONE + YDYP_AUTH。
    """
    accounts = []
    multi = os.environ.get("YDYP_ACCOUNTS", "").strip()
    if multi:
        for line in multi.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                phone = parts[0].strip()
                tok = sanitize_token(parts[1])
                if phone and tok:
                    accounts.append((phone, tok))
    else:
        phone = os.environ.get("YDYP_PHONE", "").strip()
        tok = sanitize_token(os.environ.get("YDYP_AUTH", "").strip())
        if phone and tok:
            accounts.append((phone, tok))
    return accounts


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    accounts = parse_accounts()
    if not accounts:
        print("未配置账号。请设置环境变量:")
        print("  多账号: YDYP_ACCOUNTS (每行 手机号|Basic <token>)")
        print("  单账号: YDYP_PHONE + YDYP_AUTH")
        sys.exit(1)

    try_all = os.environ.get("YDYP_TRY_ALL", "").strip().lower() in ("1", "true", "yes", "on")
    interact_limit = int(os.environ.get("YDYP_INTERACT_LIMIT", "30"))

    print("=" * 50)
    print("139云盘 云朵任务开始,共 %d 个账号" % len(accounts))
    print("=" * 50)

    all_lines = []
    ok_count = 0
    for phone, tok in accounts:
        print("\n>>> 账号 %s..." % (phone[:3] + "****" + phone[-4:]))
        ok, lines = run_account(phone, tok, try_all=try_all, interact_limit=interact_limit)
        for ln in lines:
            print("  " + ln)
        all_lines.append("【%s】" % (phone[:3] + "****" + phone[-4:]))
        all_lines.extend(lines)
        if ok:
            ok_count += 1

    # 总结
    summary = "\n".join(all_lines)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)

    # 通知
    title = "139云盘云朵任务: %d/%d 账号成功" % (ok_count, len(accounts))
    channel = send_notify(title, summary)
    print("\n通知渠道: %s" % channel)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
