# pdf_report.py — YouTube Audit Pro v2 (Elite PDF, text-first)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Flowable
)
from datetime import datetime
from statistics import mean
import math

SUPPORTLY_BLUE = colors.HexColor("#27A6FF")
NAVY_BG = colors.HexColor("#0B1C3D")
PANEL_BG = colors.HexColor("#0E2452")
TEXT = colors.white

# ---------- helpers ----------
def _safe(x, default="—"):
    return default if x is None or x == "" else x

def _pct(x, default="—"):
    try:
        return f"{float(x):.1f}%"
    except:
        return default

def _num(x, default="—"):
    try:
        if abs(float(x)) >= 1000:
            return f"{float(x):,.0f}"
        return f"{float(x):.0f}"
    except:
        return default

def _hrs(x, default="—"):
    try:
        return f"{float(x):.1f}"
    except:
        return default

def _avg(seq):
    seq = [float(x) for x in seq if isinstance(x,(int,float,str)) and str(x).strip() not in ("", "—", "nan", "None")]
    return mean(seq) if seq else 0.0

def _wrap_bullets(items, styles):
    ps = []
    for it in items:
        ps.append(Paragraph(f"• {_safe(it)}", styles["Body"]))
    return ps

def _draw_header(canvas, doc, channel_name, audit_date):
    canvas.saveState()
    canvas.setFillColor(NAVY_BG)
    canvas.rect(0, doc.height + doc.topMargin, doc.width + doc.leftMargin + doc.rightMargin, 40, fill=True, stroke=False)
    canvas.setFillColor(TEXT)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(20, doc.height + doc.topMargin + 14, "YOUTUBE AUDIT PRO — Elite")
    canvas.setFont("Helvetica", 9)
    right = doc.width + doc.leftMargin + doc.rightMargin - 20
    canvas.drawRightString(right, doc.height + doc.topMargin + 14, f"{_safe(channel_name)}")
    canvas.drawRightString(right, doc.height + doc.topMargin + 4, f"Audit: {audit_date}")
    canvas.restoreState()

def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PANEL_BG)
    canvas.rect(0, 0, doc.width + doc.leftMargin + doc.rightMargin, 28, fill=True, stroke=False)
    canvas.setFillColor(TEXT)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(20, 10, "Supportly • YouTube Audit Pro v2 • supportly.co.uk")
    canvas.drawRightString(doc.width + doc.leftMargin + doc.rightMargin - 20, 10, f"Page {doc.page}")
    canvas.restoreState()

# ---------- public API ----------
def build_youtube_audit_pdf(
    out_path:str,
    channel_info:dict,
    videos:list,
    ai_recos:list=None,
    roadmap:dict=None
):
    """
    channel_info keys:
      name, url, subscribers, total_views, avg_engagement_rate(%), date(optional)
    videos: list of dicts with keys:
      title, description, comments, likes, views, watch_time_hours, ctr, seo_score, upload_date
      optional: keywords (list[str])
    ai_recos: list[str]
    roadmap: {"7d":[...], "30d":[...], "60d":[...]}

    Writes PDF to out_path. Returns out_path.
    """
    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=14, textColor=SUPPORTLY_BLUE, spaceAfter=6))
    styles.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=11, textColor=SUPPORTLY_BLUE, spaceBefore=6, spaceAfter=4))
    styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=9, textColor=colors.black, leading=12))
    styles.add(ParagraphStyle(name="Small", fontName="Helvetica", fontSize=8, textColor=colors.black, leading=11))
    styles.add(ParagraphStyle(name="Muted", fontName="Helvetica-Oblique", fontSize=8, textColor=colors.grey))

    margin = 14*mm
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=margin, rightMargin=margin, topMargin=24*mm, bottomMargin=18*mm
    )
    channel_name = _safe(channel_info.get("name","Channel"))
    audit_date = _safe(channel_info.get("date") or datetime.utcnow().strftime("%Y-%m-%d"))
    story = []

    # Header block (text cards)
    kpis = [
        ["Channel URL", _safe(channel_info.get("url"))],
        ["Subscribers", _num(channel_info.get("subscribers"))],
        ["Total Views", _num(channel_info.get("total_views"))],
        ["Avg Engagement Rate", _pct(channel_info.get("avg_engagement_rate"))],
    ]
    story.append(Paragraph("Channel Overview", styles["H1"]))
    t = Table(kpis, hAlign="LEFT", colWidths=[45*mm, None])
    t.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#dddddd")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e6e6e6")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f6f8ff")),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#333333")),
    ]))
    story.extend([t, Spacer(1,6)])

    # Video performance table
    story.append(Paragraph("Video Performance", styles["H1"]))
    headers = ["TITLE","DESCRIPTION","COMMENTS","LIKES","VIEWS","WATCH TIME (HRS)","CTR %","SEO SCORE /100","UPLOAD DATE"]
    rows = [headers]
    for v in videos:
        rows.append([
            _safe(v.get("title")),
            _safe(v.get("description")),
            _num(v.get("comments")),
            _num(v.get("likes")),
            _num(v.get("views")),
            _hrs(v.get("watch_time_hours")),
            _pct(v.get("ctr")),
            _num(v.get("seo_score")),
            _safe(v.get("upload_date")),
        ])
    # wide table with dynamic height
    col_widths = [45*mm, 60*mm, 20*mm, 20*mm, 22*mm, 30*mm, 18*mm, 28*mm, 26*mm]
    vt = Table(rows, colWidths=col_widths, repeatRows=1)
    vt.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eaf4ff")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.HexColor("#00395b")),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cfd7e6")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.extend([vt, Spacer(1,8)])

    # Insights under table
    by_views = sorted(videos, key=lambda x: float(x.get("views",0) or 0), reverse=True)
    highest = by_views[0]["title"] if by_views else "—"
    lowest = by_views[-1]["title"] if len(by_views) > 0 else "—"
    engagements = [_safe(v.get("likes",0),0) for v in videos]  # proxy if no ER/video
    variance = "—"
    try:
        mn = _avg([float(x) for x in engagements if x != "—"])
        mx = max([float(x) for x in engagements if x != "—"]) if engagements else 0
        variance = f"{((mx - mn) / (mn or 1))*100:.1f}%"
    except:
        pass
    insight_tbl = Table([
        ["Highest-performing video", highest],
        ["Lowest-performing video", lowest],
        ["Engagement variance", variance],
    ], colWidths=[60*mm, None])
    insight_tbl.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e6e6e6")),
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#fafafa")),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
    ]))
    story.extend([insight_tbl, Spacer(1,10)])

    # Specific improvements per video
    story.append(Paragraph("Specific Improvements", styles["H1"]))
    for v in videos:
        story.append(Paragraph(f"{_safe(v.get('title'))}", styles["H2"]))
        bullets = []
        # rule-based suggestions
        ctr = float(v.get("ctr") or 0)
        seo = float(v.get("seo_score") or 0)
        wt = float(v.get("watch_time_hours") or 0)
        desc = v.get("description") or ""
        if ctr < 3:
            bullets.append("Increase CTR: tighten title to 55–60 chars and add a power word; refresh thumbnail with clear subject + contrast.")
        if seo < 70:
            bullets.append("Expand description to 200–300 words with primary and 2–3 secondary keywords; add 3 tags matching search intent.")
        if wt < 1:
            bullets.append("Shorten intro to <10s and add an early promise; insert a pattern interrupt at 30–45s.")
        if len(desc) < 100:
            bullets.append("Add timestamps and a 2-line value proposition in the first 3 lines.")
        bullets.append("End screen: add 2 elements and a strong verbal CTA.")
        story.extend(_wrap_bullets(bullets, styles))
        story.append(Spacer(1,4))
    story.append(Spacer(1,6))

    # Improvement summary (Top 5 global)
    story.append(Paragraph("Improvement Summary", styles["H1"]))
    global_top5 = [
        "Upgrade thumbnails for Top 5 videos to raise CTR by 1–2 pp.",
        "Hook first 8–10s with outcome statement and visual motion.",
        "Standardise descriptions: 250 words, keywords early, timestamps.",
        "Pin comments with a question and link to next video or offer.",
        "Post cadence: at least 1 long-form + 2 Shorts per week.",
    ]
    story.extend(_wrap_bullets(global_top5, styles))
    story.append(Spacer(1,6))

    # Video wins
    story.append(Paragraph("Video Wins", styles["H1"]))
    wins = [
        "Strong audience retention on mid-video segments.",
        "Above-average CTR compared to niche baseline.",
        "Comments show clear product-market interest.",
        "Titles match thumbnails, reducing bounce.",
        "End screen clicks trending upward.",
    ]
    story.extend(_wrap_bullets(wins, styles))
    story.append(Spacer(1,6))

    # Top keywords (aggregate)
    story.append(Paragraph("Top Keywords (AI-Derived)", styles["H1"]))
    kw_rows = [["Keyword","Volume","Difficulty","Ranking Video","CTR %"]]
    for v in videos[:5]:
        kws = v.get("keywords") or []
        if not kws: 
            continue
        for k in kws[:2]:
            kw_rows.append([_safe(k), "—", "—", _safe(v.get("title")), _pct(v.get("ctr"))])
    if len(kw_rows) == 1:
        kw_rows.append(["—","—","—","—","—"])
    kwt = Table(kw_rows, repeatRows=1, colWidths=[50*mm, 22*mm, 22*mm, 65*mm, 18*mm])
    kwt.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cfd7e6")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eaf4ff")),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.extend([kwt, Spacer(1,8)])

    # Averages
    story.append(Paragraph("Averages", styles["H1"]))
    avg_views = _avg([v.get("views") for v in videos])
    avg_wt = _avg([v.get("watch_time_hours") for v in videos])
    avg_ctr = _avg([v.get("ctr") for v in videos])
    avg_seo = _avg([v.get("seo_score") for v in videos])
    avg_tbl = Table([
        ["Average Views", f"{avg_views:,.0f}"],
        ["Average Watch Time (hrs)", f"{avg_wt:.1f}"],
        ["Average CTR", f"{avg_ctr:.1f}%"],
        ["Average SEO Score", f"{avg_seo:.0f}/100"],
    ], colWidths=[70*mm, None])
    avg_tbl.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e6e6e6")),
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#fafafa")),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
    ]))
    story.extend([avg_tbl, Spacer(1,8)])

    # Channel Health Score (weighted)
    # Pillars: Content Optimization 25, Engagement 20, Consistency 15, SEO 20, Retention 20
    pillars = {}
    pillars["Content Optimization"] = min(100, avg_seo)  # proxy
    # engagement proxy: likes+comments per 1k views if data present
    try:
        er_list = []
        for v in videos:
            likes = float(v.get("likes") or 0)
            comments = float(v.get("comments") or 0)
            views = float(v.get("views") or 0)
            if views > 0:
                er_list.append((likes+comments)/views*100)
        pillars["Engagement"] = max(0, min(100, (_avg(er_list)*2))) if er_list else max(0, min(100, avg_ctr))  # crude proxy
    except:
        pillars["Engagement"] = max(0, min(100, avg_ctr))
    pillars["Consistency"] = 70  # fill from cadence later if available
    pillars["SEO/Discoverability"] = min(100, avg_ctr*10) if avg_ctr else min(100, avg_seo*0.9)
    pillars["Retention"] = min(100, 50 + (avg_wt*5))  # rough until AVD is wired

    weights = {"Content Optimization":25,"Engagement":20,"Consistency":15,"SEO/Discoverability":20,"Retention":20}
    overall = sum(pillars[k]*weights[k] for k in pillars)/100.0
    story.append(Paragraph("Channel Health Score", styles["H1"]))
    score_tbl = [["Overall", f"{overall:.0f}/100"]]
    for k in weights:
        score_tbl.append([k, f"{pillars[k]:.0f}/100"])
    stbl = Table(score_tbl, colWidths=[70*mm, None])
    stbl.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e6e6e6")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f6f8ff")),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
    ]))
    story.extend([stbl, Spacer(1,8)])

    # AI recommendations
    story.append(Paragraph("AI-Generated Recommendations", styles["H1"]))
    default_recos = [
        "Target titles at 55–60 chars; front-load the primary keyword.",
        "Add a mid-video cliffhanger at 40–60s to protect retention.",
        "Use 2 end-screen elements and a pinned comment question.",
        "Batch redesign thumbnails with consistent face, angle, and contrast.",
        "Publish at a fixed weekly slot to train viewers."
    ]
    story.extend(_wrap_bullets(ai_recos or default_recos, styles))
    story.append(Spacer(1,8))

    # Roadmap
    story.append(Paragraph("Roadmap", styles["H1"]))
    roadmap = roadmap or {
        "7d": ["Optimise descriptions for Top 5 videos", "Add timestamps and links above the fold"],
        "30d": ["Thumbnail revamp for Top 5", "Create 3 series playlists with keyword-rich names"],
        "60d": ["Increase cadence to 1 long + 2 Shorts weekly", "Launch community poll cadence"],
    }
    for horizon, items in [("Next 7 Days", roadmap.get("7d",[])),
                           ("Next 30 Days", roadmap.get("30d",[])),
                           ("Next 60 Days", roadmap.get("60d",[]))]:
        story.append(Paragraph(horizon, styles["H2"]))
        story.extend(_wrap_bullets(items, styles))
        story.append(Spacer(1,4))

    # Build
    def on_first_page(canvas, doc):
        _draw_header(canvas, doc, channel_name, audit_date)
        _draw_footer(canvas, doc)

    def on_later_pages(canvas, doc):
        _draw_header(canvas, doc, channel_name, audit_date)
        _draw_footer(canvas, doc)

    doc.build(story, onFirstPage=on_first_page, onLaterPages=on_later_pages)
    return out_path

# -------------- Streamlit usage example --------------
if __name__ == "__main__":
    # Minimal demo dataset
    channel = {
        "name":"Demo Channel",
        "url":"https://youtube.com/@demo",
        "subscribers": 15430,
        "total_views": 1250043,
        "avg_engagement_rate": 3.2,
    }
    vids = [
        {
            "title":"How to Brew Espresso",
            "description":"Learn espresso basics and dial-in steps.",
            "comments": 82, "likes": 740, "views": 42000,
            "watch_time_hours": 1.8, "ctr": 4.3, "seo_score": 78,
            "upload_date":"2025-10-01",
            "keywords":["espresso","dial in","barista"]
        },
        {
            "title":"Latte Art 5 Levels",
            "description":"Beginner to pro patterns explained.",
            "comments": 60, "likes": 590, "views": 38000,
            "watch_time_hours": 1.4, "ctr": 3.5, "seo_score": 72,
            "upload_date":"2025-10-15",
            "keywords":["latte art","coffee art"]
        },
    ]
    out = build_youtube_audit_pdf("youtube_audit_pro_demo.pdf", channel, vids)
    print("Wrote:", out)
