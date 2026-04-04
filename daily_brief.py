"""
daily_brief.py
每天自动生成财经晨报 + AI日报，发送到指定邮箱。
"""

import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import time
import requests

# ── 配置 ──────────────────────────────────────────────────────────────────────

API_KEY      = os.environ["ANTHROPIC_API_KEY"]
API_ENDPOINT = "https://code.ppchat.vip/v1/chat/completions"
MODEL        = "gpt-5.4"

SMTP_HOST    = "smtp.qq.com"
SMTP_PORT    = 465
SMTP_USER    = os.environ["SMTP_USER"]
SMTP_PASS    = os.environ["SMTP_PASSWORD"]
TO_EMAIL     = os.environ["TO_EMAIL"]

SGT = timezone(timedelta(hours=8))


# ── 调用 API ──────────────────────────────────────────────────────────────────

def call_api(system_prompt: str, user_prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    }
    wait = 30
    for attempt in range(5):
        resp = requests.post(API_ENDPOINT, headers=headers, json=payload, timeout=120)
        if resp.status_code == 429:
            print(f"  [限速] 等待 {wait}s 后重试（第{attempt+1}次）...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError("API 多次限速，放弃重试")


# ── 财经晨报 ──────────────────────────────────────────────────────────────────

FINANCE_SYSTEM = """你是一位专业的财经分析师，负责生成结构清晰的财经晨报。
要求：专业、克制、清晰，像投研晨报摘要，不像情绪化媒体稿。
禁止使用"血洗""崩盘""全面主导"等夸张词。不提供直接投资建议。"""

def finance_user_prompt(date_str: str) -> str:
    return f"""请生成 {date_str} 的财经晨报，整理过去24小时内的重要财经资讯。

必须严格使用以下结构输出（Markdown格式）：

# 晨报 {date_str}

## 一句话判断
用1句话概括今天市场主线，直接点明市场在交易什么。

## 今日新驱动（24h）
3–6条，每条写清：发生了什么 + 为什么市场在意。
如无新的明确催化，直接写：**无新的明确催化，主要为旧逻辑延续或市场再定价**

## 背景逻辑
2–4条，只放24小时外但仍在影响市场的长期逻辑或风险背景。

## 市场全景
使用表格，包含：A股、港股、美股/外围、商品（如有必要补充黄金、原油、美元）

| 市场 | 表现 |
|------|------|

## 机构观点
3–5条，每条只保留最核心判断。

## 观察重点
3–5条接下来最值得盯的变量。

## 结论
1段话总结当前市场主线、风险与后续观察方向。不提供买卖建议。

时效性要求：
- "今日新驱动"只能写最近24小时内的信息
- 超过24小时的信息只能放在"背景逻辑"
- 无法确认时间的信息不列入"今日新驱动"
- 信息不足时减少条数，不要硬凑"""


# ── AI 日报 ───────────────────────────────────────────────────────────────────

AI_SYSTEM = """你是一位专业的AI行业分析师，负责生成结构清晰的AI每日资讯简报。
要求：清晰、简洁、信息密度高，像专业简报，不像公众号长文。不标题党，不写空话。"""

def ai_user_prompt(date_str: str) -> str:
    return f"""请生成 {date_str} 的AI日报，整理过去24小时内的重要AI资讯。

必须严格使用以下结构输出（Markdown格式）：

# AI 每日资讯 {date_str}

## 一、权威媒体 AI 资讯
5–8条（信息不足可少于5条）。
每条格式：
- **标题：**
- **来源：**
- **核心内容：**
- **为什么重要：**

优先收集：OpenAI/Anthropic/Google/Meta/Microsoft/NVIDIA等官方发布、模型发布、产品更新、融资并购、AI政策监管、芯片算力动态。

## 二、社交平台 AI 热点
3–6条（信息不足可少于3条）。
每条格式：
- **平台：**
- **话题：**
- **核心讨论点：**
- **为什么值得关注：**

注意：传闻、爆料、猜测不能写成正式事实。

## 三、今日 AI 观察重点
3–5条最值得继续追踪的方向。

## 四、一句话总结
1段话总结今天AI领域最突出的主线。

时效性要求：
- 必须优先使用最近24小时内的信息
- 超过24小时只能作为背景补充
- 无法确认发布时间的不写入主要板块
- 信息不足时减少条数，不要硬凑"""


# ── 发送邮件 ──────────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, TO_EMAIL, msg.as_string())


def md_to_html(md: str, title: str) -> str:
    """简单的 Markdown → HTML 转换（不依赖额外库）"""
    lines = md.split("\n")
    html_lines = []
    in_table = False
    in_list = False

    for line in lines:
        # 表格
        if line.startswith("|"):
            if not in_table:
                html_lines.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;margin:12px 0;">')
                in_table = True
            if set(line.replace("|","").replace("-","").replace(" ","")) == set():
                continue  # 分隔行跳过
            cells = [c.strip() for c in line.strip("|").split("|")]
            tag = "th" if html_lines[-1] == '<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;margin:12px 0;">' else "td"
            html_lines.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # 标题
        if line.startswith("# "):
            html_lines.append(f'<h1 style="color:#1a1a2e;border-bottom:2px solid #1a1a2e;padding-bottom:8px;">{line[2:]}</h1>')
        elif line.startswith("## "):
            html_lines.append(f'<h2 style="color:#2f5496;margin-top:24px;">{line[3:]}</h2>')
        elif line.startswith("### "):
            html_lines.append(f'<h3 style="color:#333;">{line[4:]}</h3>')
        # 列表
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = line[2:]
            # **bold**
            import re
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
            html_lines.append(f"<li>{content}</li>")
            continue
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if line.strip() == "":
                html_lines.append("<br>")
            else:
                import re
                line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
                html_lines.append(f"<p style='margin:4px 0;'>{line}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</table>")

    body = "\n".join(html_lines)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;max-width:800px;margin:0 auto;padding:24px;color:#222;line-height:1.7;">
{body}
<hr style="margin-top:40px;border:none;border-top:1px solid #eee;">
<p style="color:#999;font-size:12px;">由 GitHub Actions 自动生成 · {title}</p>
</body>
</html>"""


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(SGT)
    date_str = now.strftime("%Y年%-m月%-d日")

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} SGT] 开始生成简报...")

    # 财经晨报
    print("生成财经晨报...")
    finance_md = call_api(FINANCE_SYSTEM, finance_user_prompt(date_str))
    finance_html = md_to_html(finance_md, f"财经晨报 {date_str}")
    send_email(f"📈 财经晨报 {date_str}", finance_html)
    print("财经晨报已发送")

    # 两次调用之间等待，避免限速
    print("等待 30s 避免限速...")
    time.sleep(30)

    # AI 日报
    print("生成 AI 日报...")
    ai_md = call_api(AI_SYSTEM, ai_user_prompt(date_str))
    ai_html = md_to_html(ai_md, f"AI日报 {date_str}")
    send_email(f"🤖 AI日报 {date_str}", ai_html)
    print("AI日报已发送")

    print("完成。")


if __name__ == "__main__":
    main()
