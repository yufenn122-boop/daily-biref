"""
daily_brief_hybrid.py
先抓取公开新闻/RSS与社区讨论，再调用 OpenAI 兼容 chat/completions 生成财经晨报和 AI 日报。
适用于不具备官方 OpenAI web_search 能力、但可以调用普通聊天模型的中转接口。
"""

import html
import os
import re
import smtplib
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests


def get_required_env(name: str, legacy_name: str | None = None) -> str:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    if legacy_name:
        legacy_value = os.environ.get(legacy_name)
        if legacy_value and legacy_value.strip():
            print(f"[兼容模式] 未找到 {name}，改用旧变量 {legacy_name}")
            return legacy_value.strip()
    raise KeyError(f"缺少环境变量: {name}")


def get_env_or_default(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


API_KEY = get_required_env("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
OPENAI_BASE_URL = get_env_or_default("OPENAI_BASE_URL", "https://code.ppchat.vip/v1").rstrip("/")
OPENAI_MODEL = get_env_or_default("OPENAI_MODEL", "gpt-5.4")
API_ENDPOINT = f"{OPENAI_BASE_URL}/chat/completions"
COLLECT_TIMEOUT_SECONDS = int(get_env_or_default("COLLECT_TIMEOUT_SECONDS", "30"))

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASSWORD"]
TO_EMAIL = os.environ["TO_EMAIL"]

SGT = timezone(timedelta(hours=8))
UTC = timezone.utc
USER_AGENT = "Mozilla/5.0 (compatible; DailyBriefBot/1.0; +https://github.com/)"


def call_api(system_prompt: str, user_prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    wait = 20
    for attempt in range(5):
        resp = requests.post(API_ENDPOINT, headers=headers, json=payload, timeout=(30, 180))
        if resp.status_code == 429:
            print(f"  [限速] 等待 {wait}s 后重试（第{attempt + 1}次）...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
            continue
        if not resp.ok:
            raise RuntimeError(f"API 调用失败: HTTP {resp.status_code} - {resp.text}")
        return resp.json()["choices"][0]["message"]["content"].strip()

    raise RuntimeError("API 多次限速，放弃重试")


def fetch_text(url: str) -> str:
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=(15, COLLECT_TIMEOUT_SECONDS),
    )
    resp.raise_for_status()
    return resp.text


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def format_dt(dt: datetime | None) -> str:
    if not dt:
        return "未知"
    return dt.astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT")


def parse_pub_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def get_child_text(item: ET.Element, local_name: str) -> str:
    for child in item:
        if child.tag.split("}")[-1] == local_name:
            return (child.text or "").strip()
    return ""


def get_child_source(item: ET.Element) -> str:
    for child in item:
        if child.tag.split("}")[-1] == "source":
            return (child.text or "").strip()
    return ""


def fetch_google_news(query: str, limit: int = 10) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        xml_text = fetch_text(url)
        root = ET.fromstring(xml_text)
    except Exception as exc:
        print(f"[采集警告] Google News RSS 拉取失败: {query} -> {exc}")
        return []

    items = []
    for item in root.findall(".//item"):
        title = get_child_text(item, "title")
        link = get_child_text(item, "link")
        pub_date = parse_pub_date(get_child_text(item, "pubDate"))
        source = get_child_source(item) or "Google News"
        description = strip_html(get_child_text(item, "description"))
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published_at": pub_date,
                "summary": description,
            }
        )

    items.sort(key=lambda x: x["published_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)

    deduped = []
    seen = set()
    for item in items:
        key = (item["title"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def fetch_hn_discussions(limit: int = 8) -> list[dict]:
    since = int((datetime.now(UTC) - timedelta(hours=24)).timestamp())
    queries = ["AI", "OpenAI", "Anthropic", "Gemini", "NVIDIA"]
    items = []
    seen = set()

    for query in queries:
        url = (
            "https://hn.algolia.com/api/v1/search_by_date?"
            f"query={quote_plus(query)}&tags=story&numericFilters=created_at_i>{since}"
        )
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=(15, COLLECT_TIMEOUT_SECONDS),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[采集警告] Hacker News 拉取失败: {query} -> {exc}")
            continue
        for hit in data.get("hits", []):
            title = (hit.get("title") or "").strip()
            link = (hit.get("url") or "").strip()
            created_at = hit.get("created_at")
            try:
                published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(UTC)
            except Exception:
                published_at = None
            if not title:
                continue
            key = (title, link)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "platform": "Hacker News",
                    "title": title,
                    "link": link or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "published_at": published_at,
                }
            )

    items.sort(
        key=lambda x: (
            x["published_at"] or datetime.min.replace(tzinfo=UTC),
            x["points"],
            x["comments"],
        ),
        reverse=True,
    )
    return items[:limit]


def build_news_context(title: str, items: list[dict], include_metrics: bool = False) -> str:
    lines = [title]
    if not items:
        lines.append("- 无")
        return "\n".join(lines)

    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. 标题: {item.get('title', '无标题')}")
        if "platform" in item:
            lines.append(f"   平台: {item.get('platform', '未知')}")
        else:
            lines.append(f"   来源: {item.get('source', '未知')}")
        lines.append(f"   发布时间: {format_dt(item.get('published_at'))}")
        if include_metrics:
            lines.append(f"   热度: {item.get('points', 0)} points, {item.get('comments', 0)} comments")
        if item.get("summary"):
            lines.append(f"   摘要: {item['summary'][:240]}")
        lines.append(f"   链接: {item.get('link', '')}")
    return "\n".join(lines)


def collect_finance_context() -> str:
    queries = [
        "(stocks OR markets OR inflation OR fed OR treasury OR oil OR gold) when:1d",
        "(tariffs OR trade war OR China US trade OR S&P 500 OR Nasdaq) when:1d",
    ]
    items = []
    seen = set()
    for query in queries:
        for item in fetch_google_news(query, limit=8):
            key = (item["title"], item["source"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    items.sort(key=lambda x: x["published_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return build_news_context("过去24小时财经新闻素材：", items[:12])


def collect_ai_context() -> tuple[str, str]:
    ai_queries = [
        "(OpenAI OR Anthropic OR Google AI OR Gemini OR Meta AI OR Microsoft AI OR NVIDIA AI) when:1d",
        "(AI model OR LLM OR generative AI OR AI chip OR AI regulation) when:1d",
    ]
    news_items = []
    seen = set()
    for query in ai_queries:
        for item in fetch_google_news(query, limit=8):
            key = (item["title"], item["source"])
            if key in seen:
                continue
            seen.add(key)
            news_items.append(item)
    news_items.sort(key=lambda x: x["published_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)

    discussions = fetch_hn_discussions(limit=8)
    news_context = build_news_context("过去24小时AI新闻素材：", news_items[:12])
    social_context = build_news_context("过去24小时公开社区讨论素材：", discussions[:8], include_metrics=True)
    return news_context, social_context


FINANCE_SYSTEM = """你是一位专业的财经分析师，负责根据“已提供的新闻素材”生成结构清晰的财经晨报。
要求：
1. 只能基于用户给出的素材写作，不能杜撰没有出现在素材中的事实。
2. 专业、克制、清晰，像投研晨报摘要，不像情绪化媒体稿。
3. 禁止使用“血洗”“崩盘”“全面主导”等夸张词。
4. 不提供直接投资建议。
5. 如果素材不足，就减少条数，不要硬凑，也不要说自己不能联网。"""


def finance_user_prompt(date_str: str, context: str) -> str:
    return f"""以下是程序已收集到的过去24小时财经新闻素材，请仅基于这些素材生成 {date_str} 的财经晨报。

{context}

必须严格使用以下结构输出（Markdown格式）：

# 晨报 {date_str}

## 一句话判断
用1句话概括今天市场主线，直接点明市场在交易什么。

## 今日新驱动（24h）
3-6条，每条写清：发生了什么 + 为什么市场在意。
如无新的明确催化，直接写：**无新的明确催化，主要为旧逻辑延续或市场再定价**

## 背景逻辑
2-4条，只放24小时外但仍在影响市场的长期逻辑或风险背景。

## 市场全景
使用表格，包含：A股、港股、美股/外围、商品（如有必要补充黄金、原油、美元）

| 市场 | 表现 |
|------|------|

## 机构观点
如素材中没有明确机构观点，可以减少条数或用“暂未见高质量新增机构观点”。

## 观察重点
3-5条接下来最值得盯的变量。

## 结论
1段话总结当前市场主线、风险与后续观察方向。不提供买卖建议。

## 参考来源
列出你实际使用过的来源链接，使用 Markdown 列表。"""


AI_SYSTEM = """你是一位专业的AI行业分析师，负责根据“已提供的新闻素材和社区讨论素材”生成结构清晰的AI每日资讯简报。
要求：
1. 只能基于用户给出的素材写作，不能杜撰没有出现在素材中的事实。
2. 权威媒体资讯优先采用官方博客、公司官网、监管公告和权威媒体信息。
3. 社交平台热点只能基于已提供的公开讨论素材，不能把传闻、猜测写成正式事实。
4. 输出要清晰、简洁、信息密度高，像专业简报，不像公众号长文。
5. 如果素材不足，就减少条数，不要硬凑，也不要说自己不能联网。"""


def ai_user_prompt(date_str: str, news_context: str, social_context: str) -> str:
    return f"""以下是程序已收集到的过去24小时 AI 新闻素材和公开社区讨论素材，请仅基于这些素材生成 {date_str} 的AI日报。

{news_context}

{social_context}

必须严格使用以下结构输出（Markdown格式）：

# AI 每日资讯 {date_str}

## 一、权威媒体 AI 资讯
5-8条（信息不足可少于5条）。
每条格式：
- **标题：**
- **来源：**
- **核心内容：**
- **为什么重要：**

## 二、社交平台 AI 热点
3-6条（信息不足可少于3条）。
每条格式：
- **平台：**
- **话题：**
- **核心讨论点：**
- **为什么值得关注：**

## 三、今日 AI 观察重点
3-5条最值得继续追踪的方向。

## 四、一句话总结
1段话总结今天AI领域最突出的主线。

## 参考来源
列出你实际使用过的来源链接，使用 Markdown 列表。"""


def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, TO_EMAIL, msg.as_string())


def md_inline_to_html(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[(.+?)\]\((https?://.+?)\)", r'<a href="\2">\1</a>', text)
    return text


def md_to_html(md: str, title: str) -> str:
    lines = md.split("\n")
    html_lines = []
    in_table = False
    in_list = False

    for line in lines:
        if line.startswith("|"):
            if not in_table:
                html_lines.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;margin:12px 0;">')
                in_table = True
            if set(line.replace("|", "").replace("-", "").replace(" ", "")) == set():
                continue
            cells = [md_inline_to_html(cell.strip()) for cell in line.strip("|").split("|")]
            tag = "th" if html_lines[-1].startswith("<table") else "td"
            html_lines.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        if line.startswith("# "):
            html_lines.append(f'<h1 style="color:#1a1a2e;border-bottom:2px solid #1a1a2e;padding-bottom:8px;">{md_inline_to_html(line[2:])}</h1>')
        elif line.startswith("## "):
            html_lines.append(f'<h2 style="color:#2f5496;margin-top:24px;">{md_inline_to_html(line[3:])}</h2>')
        elif line.startswith("### "):
            html_lines.append(f'<h3 style="color:#333;">{md_inline_to_html(line[4:])}</h3>')
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{md_inline_to_html(line[2:])}</li>")
            continue
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if line.strip() == "":
                html_lines.append("<br>")
            else:
                html_lines.append(f"<p style='margin:4px 0;'>{md_inline_to_html(line)}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</table>")

    body = "\n".join(html_lines)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:'PingFang SC','Microsoft YaHei',sans-serif;max-width:860px;margin:0 auto;padding:24px;color:#222;line-height:1.7;">
{body}
<hr style="margin-top:40px;border:none;border-top:1px solid #eee;">
<p style="color:#999;font-size:12px;">由 GitHub Actions 自动生成 · {title}</p>
</body>
</html>"""


def main():
    now = datetime.now(SGT)
    date_str = f"{now.year}年{now.month}月{now.day}日"

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} SGT] 开始生成混合版简报...")
    print(f"使用模型: {OPENAI_MODEL}")
    print(f"接口地址: {API_ENDPOINT}")

    print("采集财经新闻素材...")
    finance_context = collect_finance_context()

    print("生成财经晨报...")
    finance_md = call_api(FINANCE_SYSTEM, finance_user_prompt(date_str, finance_context))
    finance_html = md_to_html(finance_md, f"财经晨报 {date_str}")
    send_email(f"📈 财经晨报 {date_str}", finance_html)
    print("财经晨报已发送")

    print("等待 20s 避免限速...")
    time.sleep(20)

    print("采集 AI 新闻与社区讨论素材...")
    ai_news_context, ai_social_context = collect_ai_context()

    print("生成 AI 日报...")
    ai_md = call_api(AI_SYSTEM, ai_user_prompt(date_str, ai_news_context, ai_social_context))
    ai_html = md_to_html(ai_md, f"AI日报 {date_str}")
    send_email(f"🤖 AI日报 {date_str}", ai_html)
    print("AI日报已发送")

    print("完成。")


if __name__ == "__main__":
    main()
