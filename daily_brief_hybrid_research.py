"""
daily_brief_hybrid_research.py
在 hybrid 采集方案基础上，输出更像投研晨会材料的正式简报邮件。
"""

from __future__ import annotations

import html
import re
from datetime import datetime

import daily_brief_hybrid as base


FINANCE_SYSTEM = """宏观策略分析师。根据提供的新闻素材生成财经晨报。
规则：只用素材中的事实；禁止编造任何数字、涨跌幅、价格；素材不足就减少条数；语言简洁克制，不用情绪化词汇。"""


def finance_user_prompt(date_str: str, context: str) -> str:
    return f"""过去24小时财经新闻素材如下，仅基于此生成 {date_str} 财经晨报。

{context}

输出结构（Markdown）：

# 财经晨报 | {date_str}

## 今日核心判断
1-2句，市场在交易什么。

## 增量驱动（24h）
3-5条，每条：发生了什么 + 为什么重要。

## 市场含义
3-4条，对风险偏好、增长/政策预期、板块风格的影响。

## 风险提示
2-4条，当前最值得防范的风险。

## 今日观察清单
3-5条，接下来最值得跟踪的变量。

## 参考来源
实际使用的来源链接，Markdown 列表。"""


AI_SYSTEM = """科技与产业分析师。根据提供的新闻和社区讨论素材生成AI行业日报。
规则：只用素材中的事实；禁止编造数字；社区焦点只能来自社区素材；素材不足就减少条数；语言简洁克制，不用情绪化词汇。"""


def ai_news_prompt(date_str: str, news_context: str) -> str:
    return f"""过去24小时AI新闻素材如下，仅基于此生成 {date_str} AI行业日报新闻部分。

{news_context}

输出结构（Markdown）：

# AI行业日报 | {date_str}

## 今日主线
1-2句，AI行业最重要的变化。

## 增量动态（24h）
4-6条，每条：
- **标题：**
- **来源：**
- **核心内容：**
- **行业意义：**

## 行业影响
3-4条，对模型竞争、产品商业化、算力供给、监管或资本市场的影响。

## 参考来源
实际使用的来源链接，Markdown 列表。"""


def ai_social_prompt(date_str: str, social_context: str) -> str:
    return f"""过去24小时AI社区讨论素材如下，仅基于此生成社区焦点和观察清单。

{social_context}

输出结构（Markdown）：

## 社区焦点
2-4条，每条：
- **平台：**
- **话题：**
- **讨论要点：**
- **值得关注的原因：**

## 今日观察清单
3-5条，综合新闻与社区动态，接下来最值得跟踪的变量。"""


def md_inline_to_html(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[(.+?)\]\((https?://.+?)\)", r'<a href="\2" style="color:#17437c;text-decoration:none;">\1</a>', text)
    return text


def md_to_html(md: str, title: str, brief_type: str) -> str:
    lines = md.split("\n")
    body = []
    in_table = False
    in_list = False
    in_section = False

    for line in lines:
        if line.startswith("|"):
            if not in_table:
                if not in_section:
                    body.append('<div class="section">')
                    in_section = True
                body.append('<table class="brief-table">')
                in_table = True
            if set(line.replace("|", "").replace("-", "").replace(" ", "")) == set():
                continue
            cells = [md_inline_to_html(c.strip()) for c in line.strip("|").split("|")]
            tag = "th" if body[-1] == '<div class="section"><table class="brief-table">' else "td"
            body.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            body.append("</table></div>")
            in_table = False

        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if in_list:
                body.append("</ul></div>")
                in_list = False
                in_section = False
            if in_table:
                body.append("</table></div>")
                in_table = False
                in_section = False
            if in_section:
                body.append("</div>")
            body.append(f'<div class="section"><h2>{md_inline_to_html(line[3:])}</h2>')
            in_section = True
            continue
        if line.startswith("- "):
            if not in_list:
                body.append('<ul class="brief-list">')
                in_list = True
            body.append(f"<li>{md_inline_to_html(line[2:])}</li>")
            continue
        if line.strip() == "":
            body.append("")
        else:
            body.append(f'<p>{md_inline_to_html(line)}</p>')

    if in_list:
        body.append("</ul></div>")
        in_section = False
    if in_table:
        body.append("</table></div>")
        in_section = False
    if in_section:
        body.append("</div>")

    now = datetime.now(base.SGT)
    body_html = "\n".join(part for part in body if part)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin:0; padding:24px 12px; background:#edf2f7; color:#10233f; font-family:"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif; line-height:1.75; }}
    .sheet {{ max-width:880px; margin:0 auto; background:#fff; border:1px solid #dbe3ec; box-shadow:0 18px 42px rgba(16,35,63,.08); }}
    .masthead {{ padding:28px 32px 22px; background:linear-gradient(135deg,#10233f 0%,#1d4f91 100%); color:#fff; }}
    .eyebrow {{ font-size:12px; letter-spacing:1.8px; text-transform:uppercase; opacity:.78; margin-bottom:10px; }}
    .masthead h1 {{ margin:0; font-family:Georgia,"Times New Roman",serif; font-size:32px; }}
    .meta {{ margin-top:14px; font-size:13px; color:rgba(255,255,255,.84); }}
    .content {{ padding:28px 32px 36px; }}
    .section {{ margin:0 0 18px; padding:18px 20px; background:#f6f8fb; border:1px solid #d7dde6; }}
    .section h2 {{ margin:0 0 12px; padding-bottom:10px; border-bottom:1px solid #ccd6e3; color:#153b6f; font-size:18px; }}
    .brief-list {{ margin:0; padding-left:18px; }}
    .brief-list li {{ margin:7px 0; }}
    .brief-table {{ width:100%; border-collapse:collapse; background:#fff; }}
    .brief-table th,.brief-table td {{ border:1px solid #d3dbe6; padding:10px 12px; text-align:left; vertical-align:top; }}
    .brief-table th {{ background:#e8eef7; color:#153b6f; }}
    .footer {{ padding:16px 32px 28px; color:#607086; font-size:12px; }}
  </style>
</head>
<body>
  <div class="sheet">
    <div class="masthead">
      <div class="eyebrow">Daily Research Brief</div>
      <h1>{html.escape(title)}</h1>
      <div class="meta">简报类型：{html.escape(brief_type)}｜口径：公开信息自动整理｜生成时间：{now.strftime("%Y-%m-%d %H:%M")} SGT</div>
    </div>
    <div class="content">{body_html}</div>
    <div class="footer">本邮件由 GitHub Actions 自动生成，适合作为晨会材料或日内跟踪摘要，不构成投资建议。</div>
  </div>
</body>
</html>"""


def main():
    now = datetime.now(base.SGT)
    date_str = f"{now.year}年{now.month}月{now.day}日"

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} SGT] 开始生成投研风格版简报...")
    print(f"使用模型: {base.OPENAI_MODEL}")
    print(f"接口地址: {base.API_ENDPOINT}")

    print("采集财经新闻素材...")
    finance_context = base.collect_finance_context()
    print("生成财经晨报...")
    finance_md = base.call_api(FINANCE_SYSTEM, finance_user_prompt(date_str, finance_context))
    finance_html = md_to_html(finance_md, f"财经晨报 | {date_str}", "财经晨报")
    base.send_email(f"📘 财经晨报 | {date_str}", finance_html)
    print("财经晨报已发送")

    print("等待 20s 避免限速...")
    base.time.sleep(20)

    print("采集 AI 新闻与社区讨论素材...")
    ai_news_context, ai_social_context = base.collect_ai_context()
    print("生成 AI 行业日报（新闻部分）...")
    ai_news_md = base.call_api(AI_SYSTEM, ai_news_prompt(date_str, ai_news_context))
    print("等待 15s 避免限速...")
    base.time.sleep(15)
    print("生成 AI 行业日报（社区部分）...")
    ai_social_md = base.call_api(AI_SYSTEM, ai_social_prompt(date_str, ai_social_context))
    ai_md = ai_news_md + "\n\n" + ai_social_md
    ai_html = md_to_html(ai_md, f"AI行业日报 | {date_str}", "AI行业日报")
    base.send_email(f"📗 AI行业日报 | {date_str}", ai_html)
    print("AI行业日报已发送")
    print("完成。")


if __name__ == "__main__":
    main()
