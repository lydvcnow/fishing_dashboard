#!/usr/bin/env python3
"""
fishreport.py
每日湾区鱼情简报 — 仅鱼情AI分析
GitHub Actions 每天5AM PT自动运行，输出 docs/fishing_report.html
"""

import os
import re
import requests
import datetime
import pytz
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.0-flash"
OUTPUT_DIR     = Path("docs")
FISH_URL       = "https://www.norcalfishreports.com/fish_reports/saltwater_reports.php"
PT             = pytz.timezone("America/Los_Angeles")

# ── 工具函数 ──────────────────────────────────────────

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_fish_reports():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; fishing-brief/1.0)"}
        r = requests.get(FISH_URL, timeout=20, headers=headers)
        r.raise_for_status()
        text = r.text
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>',  '', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()[:6000]
    except Exception as e:
        log(f"鱼情页面抓取失败: {e}")
        return ""

def ask_gemini(prompt):
    if not GEMINI_API_KEY:
        return "未配置GEMINI_API_KEY。"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
    }
    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        log(f"Gemini API调用失败: {e}")
        return f"鱼情分析暂时不可用（{e}）"

def analyze(raw_text, today_str):
    if not raw_text:
        return "鱼情网页抓取失败，无法分析。"
    prompt = f"""你是一个帮助北加州湾区岸钓爱好者的助手。

以下是 NorCal Fish Reports 网站 Saltwater 版块的最新内容（英文）：

---
{raw_text}
---

请根据以上内容，用中文简要总结以下四个钓点的最新鱼情：
1. Half Moon Bay（半月湾）
2. Santa Cruz / Monterey Bay（圣克鲁斯）
3. San Francisco Bay / Alameda（旧金山湾/阿拉米达）
4. Baker Beach / SF Ocean Beach（贝克海滩）

要求：
- 每个钓点单独一段，2-4句话
- 重点说：近期有什么鱼、上鱼情况好不好、用什么饵/方法
- 如果报告中没有提到某个钓点，根据季节（{today_str}）给出合理推测
- 不要编造具体数字或虚假信息
- 语言简洁，钓鱼爱好者口吻"""
    log("调用 Gemini API 分析鱼情...")
    return ask_gemini(prompt)

def build_html(date_str, gen_time, analysis):
    paras = "".join(
        f"<p>{line.strip()}</p>"
        for line in analysis.split("\n")
        if line.strip()
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>湾区鱼情简报 · {date_str}</title>
<style>
  :root {{--deep:#051820;--foam:#b8dce8;--wave:#4fa3c0;--gold:#e8c06a;--text:#d8eef6;--muted:#7aadc0;--border:rgba(79,163,192,0.2)}}
  * {{margin:0;padding:0;box-sizing:border-box}}
  body {{background:var(--deep);color:var(--text);font-family:'Noto Serif SC',serif;
         min-height:100vh;line-height:1.7;padding:2rem 1.2rem}}
  h1 {{color:var(--foam);font-size:1.4rem;margin-bottom:.3rem;letter-spacing:.06em}}
  .meta {{color:var(--muted);font-size:.72rem;margin-bottom:1.8rem;font-family:monospace}}
  .card {{background:linear-gradient(135deg,rgba(13,51,71,.85),rgba(10,42,58,.92));
           border:1px solid var(--border);border-radius:6px;padding:1.4rem 1.6rem;
           max-width:760px;margin:0 auto}}
  .card h2 {{font-size:1rem;color:var(--foam);margin-bottom:1rem;
              padding-bottom:.6rem;border-bottom:1px solid var(--border)}}
  p {{font-size:.9rem;margin-bottom:.9rem;line-height:1.8}}
  p:last-child {{margin-bottom:0}}
  .back {{display:inline-block;margin-top:1.4rem;color:var(--wave);
           font-size:.8rem;text-decoration:none}}
  .back:hover {{color:var(--foam)}}
  .source {{font-size:.65rem;color:var(--muted);font-family:monospace;
             margin-top:1rem;padding-top:.6rem;border-top:1px solid var(--border)}}
</style>
</head>
<body>
<div style="max-width:760px;margin:0 auto">
  <h1>🐟 湾区鱼情简报</h1>
  <div class="meta">{date_str} · 生成于 {gen_time} PT</div>
  <div class="card">
    <h2>近期鱼情（AI 分析）</h2>
    {paras}
    <div class="source">数据来源：NorCal Fish Reports · 由 {GEMINI_MODEL} 分析生成</div>
  </div>
  <a class="back" href="index.html">← 返回天气潮汐</a>
</div>
</body>
</html>"""

def main():
    now_pt   = datetime.datetime.now(PT)
    date_str = now_pt.strftime("%Y-%m-%d")
    gen_time = now_pt.strftime("%H:%M")

    log(f"=== 湾区鱼情简报 {date_str} ===")

    raw  = fetch_fish_reports()
    log(f"页面文本 {len(raw)} 字符")

    text = analyze(raw, date_str)
    log(f"分析完成 {len(text)} 字符")

    html = build_html(date_str, gen_time, text)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "fishing_report.html"
    out.write_text(html, encoding="utf-8")
    log(f"✓ 已写入: {out}")

if __name__ == "__main__":
    main()
