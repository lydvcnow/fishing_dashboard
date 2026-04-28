#!/usr/bin/env python3
"""
saltwaterbrief.py
每日湾区海钓简报 — 天气/潮汐 + 鱼情分析（Gemini API版）
GitHub Actions: 每天5AM PT自动运行，输出到docs/目录，由GitHub Pages托管
"""

import json
import os
import requests
import datetime
import pytz
import sys
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.0-flash"
OUTPUT_DIR     = Path("docs")  # GitHub Pages 用 docs/ 目录

PT = pytz.timezone("America/Los_Angeles")

SPOTS = [
    {"name": "半月湾",       "name_en": "Half Moon Bay",          "lat": 37.4636, "lon": -122.4286, "noaa": "9414290", "bay": False},
    {"name": "圣克鲁斯码头", "name_en": "Santa Cruz Wharf",        "lat": 36.9612, "lon": -122.0192, "noaa": "9413450", "bay": False},
    {"name": "阿拉米达海岸", "name_en": "Alameda Shoreline",        "lat": 37.7724, "lon": -122.2997, "noaa": "9414750", "bay": True },
    {"name": "贝克海滩",     "name_en": "Baker Beach · SF",         "lat": 37.7957, "lon": -122.4836, "noaa": "9414290", "bay": False},
]

FISH_URL = "https://www.norcalfishreports.com/fish_reports/saltwater_reports.php"

WMO = {
    0:"晴",1:"大部晴",2:"多云",3:"阴",
    45:"薄雾",48:"雾凇",
    51:"小毛雨",53:"毛毛雨",55:"大毛雨",
    61:"小雨",63:"中雨",65:"大雨",
    71:"小雪",73:"中雪",75:"大雪",
    80:"阵雨",81:"中阵雨",82:"大阵雨",
    95:"雷雨",96:"雷暴夹冰雹",99:"大雷暴"
}
WDIRS = ['北','北东北','东北','东北东','东','东南东','东南','南东南',
         '南','南西南','西南','西南西','西','西北西','西北','北西北']

# ── 工具函数 ──────────────────────────────────────────

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def wind_dir(deg):
    return WDIRS[round(deg / 22.5) % 16]

def beaufort(ms):
    if ms < 1:    return "无风"
    if ms < 3.4:  return "微风"
    if ms < 5.5:  return "轻风"
    if ms < 8:    return "和风"
    if ms < 10.8: return "清风"
    if ms < 13.9: return "强风"
    return "大风⚠"

def score_conds(wind, wave, code):
    s = 100
    if wind > 8:   s -= 30
    elif wind > 5.5: s -= 12
    if wave is not None:
        if wave > 2:   s -= 30
        elif wave > 1.2: s -= 15
    if code in [80,81,82,95,96,99,65,63]: s -= 25
    elif code in [61]: s -= 12
    if s >= 80: return "佳"
    if s >= 60: return "良"
    if s >= 40: return "一般"
    return "差"

def fmt_time(s):
    """NOAA 'YYYY-MM-DD HH:MM' → 'HH:MM'"""
    try:
        d = datetime.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
        return d.strftime("%H:%M")
    except:
        return s[-5:]

# ── 数据抓取 ──────────────────────────────────────────

def fetch_weather(lat, lon, date_str):
    """抓取指定日期全天逐小时天气，返回小时列表，失败重试3次"""
    import time
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,weathercode,windspeed_10m,winddirection_10m,precipitation"
        f"&wind_speed_unit=ms&timezone=America%2FLos_Angeles&forecast_days=2"
    )
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            d = r.json()
            hours = []
            for i, t in enumerate(d["hourly"]["time"]):
                if t.startswith(date_str):
                    hours.append({
                        "time":   t,
                        "temp":   d["hourly"]["temperature_2m"][i],
                        "code":   d["hourly"]["weathercode"][i],
                        "wind":   d["hourly"]["windspeed_10m"][i],
                        "wdir":   d["hourly"]["winddirection_10m"][i],
                        "precip": d["hourly"]["precipitation"][i],
                    })
            return hours
        except Exception as e:
            last_err = e
            if attempt < 2:
                log(f"  天气抓取失败(第{attempt+1}次)，5秒后重试: {e}")
                time.sleep(5)
    raise last_err

def fetch_marine(lat, lon, date_str):
    """抓取海浪数据，失败返回 None"""
    try:
        url = (
            f"https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=wave_height,swell_wave_height,wave_period"
            f"&timezone=America%2FLos_Angeles&forecast_days=2"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        d = r.json()
        hours = []
        for i, t in enumerate(d["hourly"]["time"]):
            if t.startswith(date_str):
                hours.append({
                    "time":   t,
                    "wave":   d["hourly"]["wave_height"][i],
                    "swell":  d["hourly"]["swell_wave_height"][i],
                    "period": d["hourly"]["wave_period"][i],
                })
        return hours
    except Exception as e:
        log(f"  marine API 失败: {e}")
        return None

def fetch_tides(station_id, date_str):
    """抓取 NOAA 高低潮，返回列表"""
    try:
        noaa_date = date_str.replace("-", "")
        url = (
            f"https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
            f"?product=predictions&application=web_services"
            f"&begin_date={noaa_date}&range=26&datum=MLLW&station={station_id}"
            f"&time_zone=lst_ldt&interval=hilo&units=metric&format=json"
        )
        r = requests.get(url, timeout=15)
        d = r.json()
        if "error" in d or "predictions" not in d:
            return []
        return [p for p in d["predictions"] if p["t"].startswith(date_str)]
    except Exception as e:
        log(f"  tides 失败: {e}")
        return []

def fetch_fish_reports():
    """抓取 norcalfishreports saltwater 页面文本"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; fishing-brief/1.0)"}
        r = requests.get(FISH_URL, timeout=20, headers=headers)
        r.raise_for_status()
        # 简单提取可见文本
        text = r.text
        # 去掉 script / style 块
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>',  '', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 只保留前 6000 字符（足够覆盖最新报告）
        return text.strip()[:6000]
    except Exception as e:
        log(f"  鱼情页面抓取失败: {e}")
        return ""

# ── 最佳时窗计算 ──────────────────────────────────────

def calc_windows(tides, w_hours, m_hours, is_bay):
    if not tides or not w_hours:
        return []

    def pick_hour(hours, h):
        target = f"T{h:02d}:00"
        return next((x for x in hours if x["time"].endswith(target)), hours[0])

    windows = []
    for tide in tides:
        t_str  = tide["t"].replace(" ", "T")
        t_dt   = datetime.datetime.fromisoformat(t_str)
        t_h    = t_dt.hour + t_dt.minute / 60
        is_high = tide["type"] == "H"

        for seg in [
            {"label": f"{'高' if is_high else '低'}潮前", "s": t_h - 1.5, "e": t_h},
            {"label": f"{'高' if is_high else '低'}潮后", "s": t_h,       "e": t_h + 1.5},
        ]:
            mid_h   = (seg["s"] + seg["e"]) / 2
            clamp_h = max(0, min(23, round(mid_h)))

            wh = pick_hour(w_hours, clamp_h)
            mh = pick_hour(m_hours, clamp_h) if m_hours else None

            wind = wh["wind"] if wh else 5
            wave = mh["wave"] if mh else (0.15 if is_bay else None)
            code = wh["code"] if wh else 0

            score = 80
            if wind > 10:   score -= 35
            elif wind > 7:  score -= 20
            elif wind > 5:  score -= 8
            if not is_bay and wave is not None:
                if wave > 2.5:   score -= 30
                elif wave > 1.5: score -= 15
                elif wave > 1.0: score -= 5
            if code in [80,81,82,95,96,99,65,63]: score -= 20
            elif code in [61]: score -= 8

            is_eve   = 17 <= mid_h < 20
            is_aft   = 13 <= mid_h < 17
            is_night = 20 <= mid_h <= 23
            if is_eve:   score += 8
            if is_aft:   score += 4
            if is_night: score += 3

            if seg["e"] < 6: continue

            ds = max(6,    seg["s"])
            de = min(23.5, seg["e"])
            if de <= ds: continue

            def h_str(h):
                hh = int(h)
                mm = "30" if h % 1 >= 0.5 else "00"
                return f"{hh:02d}:{mm}"

            stars = "★★★" if score >= 82 else ("★★☆" if score >= 65 else "★☆☆")
            tag   = " 傍晚✓" if is_eve else (" 下午✓" if is_aft else (" 夜钓" if is_night else ""))
            meta  = f"风{wind:.1f}m/s"
            if not is_bay and wave is not None:
                meta += f" 浪{wave:.1f}m"

            windows.append({
                "start_h":  ds,
                "time_str": f"{h_str(ds)}–{h_str(de)}",
                "label":    seg["label"] + tag,
                "meta":     meta,
                "stars":    stars,
                "score":    score,
                "is_eve":   is_eve,
                "is_aft":   is_aft,
            })

    # 去重 + 排序
    windows.sort(key=lambda x: -x["score"])
    kept = []
    for w in windows:
        if not any(abs(k["start_h"] - w["start_h"]) < 1.2 for k in kept):
            kept.append(w)
        if len(kept) >= 5:
            break
    kept.sort(key=lambda x: x["start_h"])
    return kept

# ── Ollama 调用 ───────────────────────────────────────

def ask_gemini(prompt):
    if not GEMINI_API_KEY:
        return "未配置GEMINI_API_KEY，跳过AI分析。"
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
        log(f"  Gemini API调用失败: {e}")
        return f"鱼情分析暂时不可用（{e}）"

def analyze_fish_reports(raw_text, today_str):
    if not raw_text:
        return "鱼情网页抓取失败，无法分析。"

    prompt = f"""你是一个帮助北加州湾区岸钓爱好者的助手。

以下是 NorCal Fish Reports 网站 Saltwater（海钓）版块的最新内容（英文）：

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
- 如果报告中没有提到某个钓点，就写"本周暂无该钓点报告，根据季节（{today_str}）推测：…"然后给出合理的季节性推测
- 不要编造具体数字或虚假信息
- 语言简洁，钓鱼爱好者口吻"""

    log("  调用 Gemini API 分析鱼情...")
    return ask_gemini(prompt)

# ── HTML 生成 ─────────────────────────────────────────

def spot_html(spot, date_str):
    """生成单个钓点的天气/潮汐 HTML 块"""
    log(f"  抓取 {spot['name']} 数据...")
    try:
        w_hours = fetch_weather(spot["lat"], spot["lon"], date_str)
        m_hours = fetch_marine(spot["lat"], spot["lon"], date_str)
        tides   = fetch_tides(spot["noaa"], date_str)
    except Exception as e:
        return f'<div class="spot-card error"><div class="spot-name">{spot["name"]}</div><p>数据抓取失败：{e}</p></div>'

    if not w_hours:
        return f'<div class="spot-card error"><div class="spot-name">{spot["name"]}</div><p>无天气数据</p></div>'

    # 取正午代表值
    def pick(hours, h):
        return next((x for x in hours if x["time"].endswith(f"T{h:02d}:00")), hours[0])

    rw = pick(w_hours, 12)
    rm = pick(m_hours, 12) if m_hours else None

    temp  = rw["temp"]
    code  = rw["code"]
    wind  = rw["wind"]
    wdir  = rw["wdir"]
    precip_total = sum(h["precip"] or 0 for h in w_hours)

    wave  = rm["wave"]   if rm else None
    swell = rm["swell"]  if rm else None
    period= rm["period"] if rm else None

    sc    = score_conds(wind, wave, code)
    wdesc = WMO.get(code, "未知")
    bf    = beaufort(wind)
    wd    = wind_dir(wdir)

    # 潮汐pills
    tide_pills = ""
    for t in tides:
        cls   = "high" if t["type"] == "H" else ""
        arrow = "▲高" if t["type"] == "H" else "▽低"
        tide_pills += f'<span class="tide-pill {cls}">{arrow} {fmt_time(t["t"])} {float(t["v"]):.1f}m</span>'
    if not tide_pills:
        tide_pills = '<span class="no-data">无数据</span>'

    # 海浪行
    if wave is not None:
        wave_cls = "warn" if wave > 1.5 else "hi"
        wave_row = f"""
        <div class="data-row">
          <span class="icon">🌊</span>
          <div><div class="label">海浪 / 涌浪</div>
          <div class="value">浪高 <span class="{wave_cls}">{wave:.1f}m</span>
          {f'/ 涌 {swell:.1f}m' if swell else ''}
          {f'<span class="muted"> 周期{period:.0f}s</span>' if period else ''}</div></div>
        </div>"""
    else:
        wave_row = """
        <div class="data-row">
          <span class="icon">🌊</span>
          <div><div class="label">海浪</div><div class="value muted">湾内，以潮流为主</div></div>
        </div>"""

    # 最佳时窗
    windows = calc_windows(tides, w_hours, m_hours, spot["bay"])
    win_rows = ""
    for w in windows:
        t_cls = "eve" if w["is_eve"] else ("aft" if w["is_aft"] else "")
        win_rows += f"""
        <div class="win-row">
          <span class="win-time {t_cls}">{w['time_str']}</span>
          <span class="win-stars">{w['stars']}</span>
          <span class="win-desc">{w['label']} <span class="muted">{w['meta']}</span></span>
        </div>"""
    if not win_rows:
        win_rows = '<span class="no-data">潮汐数据不足</span>'

    wind_cls = "warn" if wind > 8 else ("ok" if wind < 5.5 else "")
    wdesc_cls = "ok" if code in [0,1,2] else ("bad" if code in [80,81,82,95,96,99,65] else "")
    precip_str = f'<span class="warn"> · 降水{precip_total:.1f}mm</span>' if precip_total > 0.5 else ''

    return f"""
<div class="spot-card">
  <div class="spot-header">
    <div>
      <div class="spot-name">{spot['name']}</div>
      <div class="spot-name-en">{spot['name_en']}</div>
    </div>
    <div class="badge {'badge-good' if sc=='佳' else 'badge-ok' if sc=='良' else 'badge-fair' if sc=='一般' else 'badge-poor'}">{sc}</div>
  </div>
  <div class="spot-body">
    <div class="data-row">
      <span class="icon">🌤</span>
      <div><div class="label">天气 / 气温（正午）</div>
      <div class="value"><span class="{wdesc_cls}">{wdesc}</span> · <span class="hi">{temp:.0f}°C</span>{precip_str}</div></div>
    </div>
    <div class="data-row">
      <span class="icon">💨</span>
      <div><div class="label">风力 / 风向（正午）</div>
      <div class="value"><span class="{wind_cls}">{bf}</span> · {wd}风 · <span class="hi">{wind:.1f} m/s</span>
      <span class="muted"> ({wind*1.944:.0f}kt)</span></div></div>
    </div>
    {wave_row}
    <div class="data-row">
      <span class="icon">🌀</span>
      <div><div class="label">今日潮汐（NOAA · MLLW）</div>
      <div class="tide-list">{tide_pills}</div></div>
    </div>
    <div class="windows-section">
      <div class="label">⏱ 推荐出钓时窗</div>
      {win_rows}
    </div>
  </div>
</div>"""

def build_html(date_str, spot_blocks, fish_analysis, generated_at):
    spots_html = "\n".join(spot_blocks)
    # 鱼情段落格式化
    fish_paras = "".join(
        f"<p>{line.strip()}</p>"
        for line in fish_analysis.split("\n")
        if line.strip()
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>湾区海钓简报 · {date_str}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
  :root {{
    --deep:#051820;--foam:#b8dce8;--wave:#4fa3c0;--gold:#e8c06a;
    --amber:#d4872a;--green:#5eb87a;--red:#e07060;--text:#d8eef6;
    --muted:#7aadc0;--border:rgba(79,163,192,0.2);
  }}
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:var(--deep);color:var(--text);font-family:'Noto Serif SC',serif;
        min-height:100vh;line-height:1.6}}
  body::before{{content:'';position:fixed;inset:0;
    background:radial-gradient(ellipse at 20% 80%,rgba(10,42,58,.8) 0%,transparent 60%),
               radial-gradient(ellipse at 80% 20%,rgba(5,24,32,.9) 0%,transparent 50%),
               linear-gradient(180deg,#051820 0%,#0a2a3a 40%,#0d3347 100%);z-index:-1}}

  header{{padding:2rem 1.5rem 1.2rem;text-align:center;border-bottom:1px solid var(--border)}}
  .hfish{{font-size:1.8rem;display:block;margin-bottom:.3rem;filter:drop-shadow(0 0 10px rgba(79,163,192,.5))}}
  h1{{font-size:1.55rem;font-weight:700;color:var(--foam);letter-spacing:.08em}}
  .meta{{color:var(--muted);font-size:.72rem;margin-top:.3rem;font-family:'JetBrains Mono',monospace}}

  /* WEATHER GRID */
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
         gap:1.1rem;padding:1.3rem 1.1rem;max-width:1300px;margin:0 auto}}
  .spot-card{{background:linear-gradient(135deg,rgba(13,51,71,.85),rgba(10,42,58,.92));
              border:1px solid var(--border);border-radius:4px;overflow:hidden}}
  .spot-header{{padding:.9rem 1.1rem .75rem;border-bottom:1px solid var(--border);
                display:flex;align-items:flex-start;justify-content:space-between;gap:.5rem}}
  .spot-name{{font-size:1rem;font-weight:700;color:var(--foam)}}
  .spot-name-en{{font-size:.65rem;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-top:.08rem}}
  .badge{{flex-shrink:0;width:38px;height:38px;border-radius:50%;
          display:flex;align-items:center;justify-content:center;
          font-size:.8rem;font-weight:700;border:2px solid currentColor}}
  .badge-good{{color:var(--green)}}.badge-ok{{color:var(--gold)}}
  .badge-fair{{color:var(--amber)}}.badge-poor{{color:var(--red)}}
  .spot-body{{padding:.9rem 1.1rem}}
  .data-row{{display:flex;align-items:flex-start;margin-bottom:.65rem;gap:.55rem}}
  .icon{{font-size:.95rem;width:1.3rem;flex-shrink:0;text-align:center;margin-top:.05rem}}
  .label{{font-size:.63rem;color:var(--muted);letter-spacing:.06em;
          font-family:'JetBrains Mono',monospace;margin-bottom:.06rem}}
  .value{{font-size:.87rem;color:var(--text)}}
  .hi{{color:var(--gold);font-weight:600}}.ok{{color:var(--green)}}
  .warn{{color:var(--amber)}}.bad{{color:var(--red)}}.muted{{color:var(--muted);font-size:.82em}}
  .tide-list{{display:flex;flex-wrap:wrap;gap:.28rem;margin-top:.2rem}}
  .tide-pill{{background:rgba(79,163,192,.08);border:1px solid rgba(79,163,192,.25);
              border-radius:2px;padding:.14rem .38rem;
              font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--wave)}}
  .tide-pill.high{{border-color:rgba(232,192,106,.4);color:var(--gold);background:rgba(232,192,106,.07)}}
  .no-data{{color:var(--muted);font-size:.75rem}}
  .windows-section{{margin-top:.75rem;padding-top:.75rem;border-top:1px solid var(--border)}}
  .win-row{{display:flex;align-items:baseline;gap:.42rem;margin-bottom:.32rem}}
  .win-time{{font-family:'JetBrains Mono',monospace;font-size:.73rem;color:var(--wave);min-width:100px;flex-shrink:0}}
  .win-time.eve{{color:var(--green)}}.win-time.aft{{color:var(--wave)}}
  .win-stars{{color:var(--gold);font-size:.66rem;letter-spacing:-1px;flex-shrink:0}}
  .win-desc{{font-size:.77rem;color:var(--text)}}
  .spot-card.error{{padding:1rem 1.1rem;color:var(--red)}}

  /* FISH REPORT */
  .fish-section{{max-width:1300px;margin:0 auto;padding:0 1.1rem 2rem}}
  .fish-card{{background:linear-gradient(135deg,rgba(13,51,71,.85),rgba(10,42,58,.92));
              border:1px solid var(--border);border-radius:4px;padding:1.3rem 1.5rem}}
  .fish-card h2{{font-size:1.05rem;color:var(--foam);margin-bottom:1rem;
                 padding-bottom:.6rem;border-bottom:1px solid var(--border)}}
  .fish-card p{{font-size:.88rem;color:var(--text);margin-bottom:.8rem;line-height:1.7}}
  .fish-card p:last-child{{margin-bottom:0}}
  .source-note{{font-size:.68rem;color:var(--muted);font-family:'JetBrains Mono',monospace;
                margin-top:.9rem;padding-top:.6rem;border-top:1px solid var(--border)}}

  footer{{text-align:center;padding:1rem;color:var(--muted);font-size:.68rem;
          font-family:'JetBrains Mono',monospace;border-top:1px solid var(--border)}}
</style>
</head>
<body>
<header>
  <span class="hfish">🎣</span>
  <h1>湾区海钓简报</h1>
  <div class="meta">{date_str} · 生成于 {generated_at} PT</div>
</header>

<div class="grid">
{spots_html}
</div>

<div class="fish-section">
  <div class="fish-card">
    <h2>🐟 近期鱼情（AI 分析）</h2>
    {fish_paras}
    <div class="source-note">数据来源：NorCal Fish Reports · Saltwater Reports · 由 {GEMINI_MODEL} 分析生成</div>
  </div>
</div>

<footer>天气/海浪：Open-Meteo · 潮汐：NOAA CO-OPS · 鱼情：NorCal Fish Reports · 时区：America/Los_Angeles</footer>
</body>
</html>"""

# ── 主程序 ────────────────────────────────────────────

def main():
    now_pt    = datetime.datetime.now(PT)
    date_str  = now_pt.strftime("%Y-%m-%d")
    gen_time  = now_pt.strftime("%H:%M")
    out_path  = OUTPUT_DIR / f"saltwaterfishing_{date_str.replace('-','')}.html"

    log(f"=== 湾区海钓简报 {date_str} ===")
    log(f"输出路径: {out_path}")

    # 1. 天气/潮汐
    spot_blocks = []
    for spot in SPOTS:
        log(f"处理: {spot['name']}")
        block = spot_html(spot, date_str)
        spot_blocks.append(block)

    # 2. 鱼情
    log("抓取鱼情页面...")
    raw_fish = fetch_fish_reports()
    log(f"  页面文本 {len(raw_fish)} 字符")
    fish_analysis = analyze_fish_reports(raw_fish, date_str)
    log(f"  鱼情分析完成 ({len(fish_analysis)} 字符)")

    # 3. 生成 HTML
    html = build_html(date_str, spot_blocks, fish_analysis, gen_time)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    log(f"✓ 已写入: {out_path}")

    # 更新 index.html 重定向到最新报告
    index = OUTPUT_DIR / "index.html"
    index.write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0;url=saltwaterfishing_{date_str.replace('-','')}.html">
<title>湾区海钓简报</title></head>
<body><a href="saltwaterfishing_{date_str.replace('-','')}.html">查看今日报告</a></body>
</html>""", encoding="utf-8")
    log(f"✓ index.html 已更新")

if __name__ == "__main__":
    main()
