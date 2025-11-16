# YouTube Audit Pro — unified single-file app
# Features:
# - License/Admin sidebar
# - Audit with SEO scoring per video
# - Elite PDF export (text-first, clean wrapping, includes SEO table + SEO summary)
# - Competition tab
# - Audience Retention tab (OAuth + analytics, ownership-checked)
# - Thumbnail & Video Ideas tabs
# Requires env vars:
#   YOUTUBE_API_KEY or YT_API_KEY
#   GOOGLE_CLIENT_SECRET_FILE (defaults to client_secret.json)
#   GOOGLE_OAUTH_TOKEN_FILE   (defaults to token.json)

from __future__ import annotations

import os
import io
import json
import base64
import ssl
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from dotenv import load_dotenv
from googleapiclient.discovery import build

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request, AuthorizedSession

# ---------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------
ssl._create_default_https_context = ssl._create_unverified_context

load_dotenv()
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

st.set_page_config(page_title="YouTube Audit Pro", layout="wide")

THEME = """
<style>
:root { --bg:#0b1c3d; --panel:#0e2452; --text:#ffffff; --muted:#b6c5ff; --accent:#27a6ff; }
html, body, [class*="block-container"] { background:var(--bg) !important; color:var(--text) !important; }
section[data-testid="stSidebar"] { background:var(--panel) !important; }
section[data-testid="stSidebar"] * { color:var(--text) !important; }
div[data-baseweb="input"] input, textarea, .stTextInput>div>div>input {
  background:#071632 !important; color:var(--text) !important; border:1px solid #1e3b79;
}
.stButton>button { background:var(--panel); color:var(--text); border:1px solid var(--accent); }
.stTabs [data-baseweb="tab"] { background:#0e2452; color:#e8eeff; border-radius:10px; padding:10px 14px; }
.stTabs [aria-selected="true"] { border:1px solid var(--accent); }
table { color:#e8eeff !important; }
</style>
"""
st.markdown(THEME, unsafe_allow_html=True)

OPENAI_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
YOUTUBE_API_KEY = (os.getenv("YOUTUBE_API_KEY") or os.getenv("YT_API_KEY") or "").strip()
if not YOUTUBE_API_KEY:
    st.sidebar.warning("Set YOUTUBE_API_KEY (or YT_API_KEY) in your .env")

# OAuth config for retention
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
CLIENT_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json")
TOKEN_FILE = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "token.json")

# ---------------------------------------------------------------------
# License store + Admin
# ---------------------------------------------------------------------
LICENSE_FILE = os.getenv("LICENSE_STORE_FILE", "licenses.json")


def _lic_load() -> dict:
    if not os.path.exists(LICENSE_FILE):
        return {}
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _lic_save(store: dict):
    tmp = LICENSE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp, LICENSE_FILE)


def has_license(key: str) -> bool:
    return key.strip() in _lic_load()


def add_license(key: str, note: str = ""):
    s = _lic_load()
    s[key.strip()] = {"note": note, "created_utc": datetime.utcnow().isoformat()}
    _lic_save(s)


def delete_license(key: str):
    s = _lic_load()
    if key.strip() in s:
        del s[key.strip()]
        _lic_save(s)


st.sidebar.title("YouTube Audit Pro")
lic_in = st.sidebar.text_input("Enter license key", value="")

if "licensed" not in st.session_state:
    st.session_state["licensed"] = False

if st.sidebar.button("Activate"):
    if has_license(lic_in):
        st.session_state["licensed"] = True
        st.sidebar.success("License valid. Access enabled.")
    else:
        st.session_state["licensed"] = False
        st.sidebar.error("License not found.")

st.sidebar.subheader("Admin")
admin_pwd = st.sidebar.text_input("Admin password", type="password", value="")
if st.sidebar.button("Open Admin") and admin_pwd:
    st.session_state["admin"] = True
if "admin" not in st.session_state:
    st.session_state["admin"] = False

if st.session_state["admin"]:
    st.sidebar.success("Admin mode")
    new_key = st.sidebar.text_input("Add license key")
    note = st.sidebar.text_input("Note")
    if st.sidebar.button("Add key") and new_key:
        add_license(new_key, note)
        st.sidebar.success("Key added")

    del_key = st.sidebar.text_input("Delete key")
    if st.sidebar.button("Delete key") and del_key:
        delete_license(del_key)
        st.sidebar.warning("Key deleted")

    store = _lic_load()
    if store:
        dfk = pd.DataFrame(
            [
                {
                    "key": k,
                    "note": v.get("note", ""),
                    "created_utc": v.get("created_utc", ""),
                }
                for k, v in store.items()
            ]
        )
        st.sidebar.dataframe(dfk, use_container_width=True, height=180)


def require_license():
    if st.session_state.get("admin"):
        return
    if not st.session_state.get("licensed", False):
        st.warning("Enter a valid license key and click Activate to use YouTube Audit Pro.")
        st.stop()


# ---------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def yt_key_service():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY, cache_discovery=False)


@st.cache_resource(show_spinner=False)
def yt_oauth_clients():
    """
    Return (youtube_v3_client, AuthorizedSession) via OAuth.
    AuthorizedSession is used for YouTube Analytics v2 HTTP calls.
    """
    if not os.path.exists(CLIENT_FILE):
        raise RuntimeError(f"Missing OAuth client file: {CLIENT_FILE}")

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    session = AuthorizedSession(creds)
    return youtube, session


def yta_reports(session: AuthorizedSession, params: dict) -> dict:
    url = "https://youtubeanalytics.googleapis.com/v2/reports"
    r = session.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def parse_channel_or_id(s: str) -> str:
    s = s.strip()
    if s.startswith("UC"):
        return s
    if "youtube.com" in s and "/channel/" in s:
        return s.split("/channel/")[1].split("/")[0]
    return s


@st.cache_data(show_spinner=False, ttl=120)
def resolve_channel_id(_yt, text: str) -> str | None:
    t = text.strip()
    if t.startswith("UC"):
        return t
    if "youtube.com" in t:
        p = urlparse(t)
        if p.path.startswith("/channel/"):
            return p.path.split("/channel/")[1]
        if p.path.startswith("/@"):
            handle = p.path[2:]
            r = (
                _yt.search()
                .list(part="snippet", q=handle, type="channel", maxResults=1)
                .execute()
            )
            it = r.get("items", [])
            return it[0]["snippet"]["channelId"] if it else None
    r = (
        _yt.search()
        .list(part="snippet", q=t, type="channel", maxResults=1)
        .execute()
    )
    it = r.get("items", [])
    return it[0]["snippet"]["channelId"] if it else None


@st.cache_data(show_spinner=False, ttl=120)
def channel_upload_playlist_id(_yt, channel_id: str) -> str | None:
    ch = _yt.channels().list(part="contentDetails", id=channel_id).execute()
    items = ch.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


@st.cache_data(show_spinner=False, ttl=120)
def fetch_recent_videos(_yt, channel_id: str, n: int = 10) -> pd.DataFrame:
    pid = channel_upload_playlist_id(_yt, channel_id)
    vids, token = [], None
    while len(vids) < n and pid:
        resp = (
            _yt.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=pid,
                maxResults=50,
                pageToken=token,
            )
            .execute()
        )
        for it in resp.get("items", []):
            vids.append(
                {
                    "video_id": it["contentDetails"]["videoId"],
                    "published": it["contentDetails"]["videoPublishedAt"],
                    "title": it["snippet"]["title"],
                }
            )
            if len(vids) >= n:
                break
        token = resp.get("nextPageToken")
        if not token:
            break
    return pd.DataFrame(vids)


@st.cache_data(show_spinner=False, ttl=120)
def fetch_video_stats(_yt, ids: list[str]) -> pd.DataFrame:
    if not ids:
        return pd.DataFrame(
            columns=["video_id", "title", "views", "likes", "comments", "description"]
        )
    rows = []
    for chunk in [ids[i : i + 50] for i in range(0, len(ids), 50)]:
        resp = (
            _yt.videos()
            .list(part="statistics,snippet", id=",".join(chunk))
            .execute()
        )
        for it in resp.get("items", []):
            s = it["statistics"]
            sn = it["snippet"]
            rows.append(
                {
                    "video_id": it["id"],
                    "title": sn.get("title", ""),
                    "description": sn.get("description", ""),
                    "views": int(s.get("viewCount", 0)),
                    "likes": int(s.get("likeCount", 0)) if "likeCount" in s else np.nan,
                    "comments": int(s.get("commentCount", 0))
                    if "commentCount" in s
                    else np.nan,
                }
            )
    return pd.DataFrame(rows).sort_values("views", ascending=False)


@st.cache_data(show_spinner=False, ttl=120)
def fetch_channel_meta(_yt, channel_id: str) -> dict:
    resp = _yt.channels().list(part="snippet,statistics", id=channel_id).execute()
    items = resp.get("items", [])
    if not items:
        return {}
    sn, stt = items[0]["snippet"], items[0]["statistics"]
    return {
        "name": sn.get("title", ""),
        "url": f"https://www.youtube.com/channel/{channel_id}",
        "subscribers": int(stt.get("subscriberCount", "0") or 0),
        "total_views": int(stt.get("viewCount", "0") or 0),
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------
def engagement_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["like_rate_%"] = (
        out["likes"] / out["views"].replace(0, np.nan) * 100
    ).round(2)
    out["comment_rate_%"] = (
        out["comments"] / out["views"].replace(0, np.nan) * 100
    ).round(2)
    return out


def view_velocity(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["published_dt"] = pd.to_datetime(
        out["published"], utc=True, errors="coerce"
    )
    now = datetime.now(timezone.utc)
    out["age_min"] = (now - out["published_dt"]).dt.total_seconds() / 60
    out["views_per_min"] = (
        out["views"] / out["age_min"].clip(lower=1)
    ).round(2)
    return out


def title_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    t = df["title"].fillna("")
    out = pd.DataFrame(index=df.index)
    out["title_len"] = t.str.len()
    out["title_ok_len"] = out["title_len"].between(45, 70, inclusive="both")

    def _dup_pen(s: str) -> int:
        words = re.findall(r"[a-z']{3,}", s.lower())
        if not words:
            return 0
        vc = pd.Series(words).value_counts()
        peak = int(vc.iloc[0]) if not vc.empty else 0
        return max(peak - 2, 0)

    out["dup_word_penalty"] = t.apply(_dup_pen)
    return out


def cadence_stats(df: pd.DataFrame):
    d = pd.to_datetime(df["published"], utc=True, errors="coerce").sort_values().dropna()
    if d.empty:
        return {
            "uploads_week": 0.0,
            "median_gap_days": np.nan,
            "best_day": "N/A",
            "best_hour_utc": "N/A",
            "consistency_100": 0,
        }
    gaps = d.diff().dt.total_seconds() / 86400
    uploads_week = round(7.0 / gaps.median(), 2) if gaps.notna().any() else 0.0
    day = d.dt.day_name().mode().iloc[0]
    hour = int(d.dt.hour.mode().iloc[0])
    freq = min(1.0, uploads_week / 3.0)
    var = gaps.std() if gaps.notna().sum() >= 2 else 5.0
    cons = int((0.7 * freq + 0.3 * (1 / (1 + var))) * 100)
    return {
        "uploads_week": uploads_week,
        "median_gap_days": round(gaps.median(), 2),
        "best_day": day,
        "best_hour_utc": hour,
        "consistency_100": cons,
    }


def keyword_density(titles: list[str]) -> pd.Series:
    words = []
    for t in titles:
        words += re.findall(r"[A-Za-z']{3,}", t.lower())
    stop = set(
        "the a an and or for with your this that what why how into from to of on in out are was were been being you my our their his her more most very".split()
    )
    words = [w for w in words if w not in stop]
    if not words:
        return pd.Series(dtype=int)
    return pd.Series(words).value_counts()


# SEO scoring
_POWER = re.compile(
    r"\b(best|secret|fast|simple|ultimate|new|proof|free|easy|guide|mistake|hack|win|earn|rich|money|truth|behind|strategy|blueprint)\b",
    re.I,
)


def _has_number(s: str) -> bool:
    return any(ch.isdigit() for ch in s or "")


def _has_link(s: str) -> bool:
    return bool(re.search(r"https?://", s or ""))


def _has_chapters(s: str) -> bool:
    return bool(re.search(r"\b\d{1,2}:\d{2}\b", s or ""))


def seo_score_row(
    title: str, desc: str, like_rate: float, comment_rate: float, dup_penalty: int
) -> tuple[int, dict]:
    title = title or ""
    desc = desc or ""
    pts = 0
    notes: dict[str, object] = {}

    # Title (40 pts)
    ok_len = 45 <= len(title) <= 70
    pw = bool(_POWER.search(title))
    num = _has_number(title)
    if ok_len:
        pts += 12
    if pw:
        pts += 10
    if num:
        pts += 8
    if dup_penalty == 0:
        pts += 10
    notes.update(
        {
            "title_len_ok": ok_len,
            "power_word": pw,
            "has_number": num,
            "dup_penalty": dup_penalty,
        }
    )

    # Description / structure (35 pts)
    long_desc = len(desc) >= 200
    chapters = _has_chapters(desc)
    links = _has_link(desc)
    if long_desc:
        pts += 15
    if chapters:
        pts += 10
    if links:
        pts += 10
    notes.update(
        {"desc_len_ok": long_desc, "chapters": chapters, "links": links}
    )

    # Engagement (25 pts)
    lr = max(0.0, float(like_rate or 0.0))
    cr = max(0.0, float(comment_rate or 0.0))
    pts += int(min(lr, 5.0) / 5.0 * 18)
    pts += int(min(cr, 1.0) / 1.0 * 7)
    notes.update({"like_rate_%": lr, "comment_rate_%": cr})

    return int(min(100, pts)), notes


# ---------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------
def _wrap(text: str, width_chars: int) -> list[str]:
    text = (text or "").replace("\n", " ")
    if not text:
        return [""]
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + (1 if line else 0) <= width_chars:
            line = (line + " " + w).strip()
        else:
            out.append(line)
            line = w
    if line:
        out.append(line)
    return out


def improvements_for_video(v: dict, median_views: int) -> list[str]:
    tips = []
    title = v.get("title", "") or ""
    desc = v.get("description", "") or ""
    views = int(v.get("views") or 0)
    comments = int(v.get("comments") or 0)

    if not any(ch.isdigit() for ch in title):
        tips.append("Add one number to the title to anchor the promise.")
    if len(title) < 45:
        tips.append("Lengthen title to ~55 chars with a clear outcome.")
    elif len(title) > 70:
        tips.append("Trim title to <=70 chars. Remove filler words.")
    if not _POWER.search(title):
        tips.append("Add one power word (e.g., ‘ultimate’, ‘simple’, ‘proven’).")

    if len(desc) < 200:
        tips.append("Extend description to >200 chars with 1–2 links near the top.")
    if not _has_chapters(desc):
        tips.append("Add chapters (timestamps) for navigation.")
    if not _has_link(desc):
        tips.append("Include one clear CTA link in the first 3 lines.")

    if comments == 0:
        tips.append("Pin a question and reply to the first 5 comments.")
    if views < max(1000, 0.8 * median_views):
        tips.append(
            "Test a stronger thumbnail: big face + 2–4 words + strong contrast."
        )

    seen, out = set(), []
    for t in tips:
        if t not in seen:
            out.append(t)
            seen.add(t)
        if len(out) == 6:
            break
    return out or ["Solid baseline. Split-test title and thumbnail for 24h."]


def global_summary(videos: list[dict]) -> list[str]:
    views = [int(v.get("views") or 0) for v in videos]
    med = int(pd.Series(views).median()) if views else 0
    return [
        f"Median views across audited videos: {med:,}.",
        "Keep a steady cadence. Aim for consistent upload gaps.",
        "Use a power word + number in titles. Avoid repeating the same word >2 times.",
        "Ensure descriptions include chapters and >200 chars.",
        "Refresh low performers with new title/thumbnail within 24h.",
    ]


def quick_wins(videos: list[dict]) -> list[str]:
    return [
        "Add chapters. Viewers jump to value fast.",
        "Front-load a number and outcome in the first 10 words of the title.",
        "Include 1–2 high-position links with a clear CTA.",
        "Pin a comment and reply to early comments within 1 hour.",
        "Batch-create 3 thumbnail variants and A/B test the strongest hook.",
    ]


def build_elite_pdf(
    channel: dict, videos: list[dict], kpis: dict, insights: list[str], keywords: list[str]
) -> bytes:
    W, H = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    def draw_line(x, y, txt, font="Helvetica", size=10):
        c.setFont(font, size)
        c.drawString(x, y, txt)

    def new_page():
        c.showPage()
        return H - 40

    COLS = ["TITLE", "DESCRIPTION", "COMMENTS", "VIEWS", "SEO /100"]
    CW = [220, 210, 70, 70, 60]
    X0, ROWH = 40, 12

    y = H - 40
    draw_line(
        X0,
        y,
        f"YOUTUBE AUDIT TOOL — {channel.get('name', '')}",
        "Helvetica-Bold",
        16,
    )
    y -= 22
    draw_line(X0, y, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    y -= 14
    draw_line(
        X0,
        y,
        f"Subs: {channel.get('subscribers', 0):,}   Total views: {channel.get('total_views', 0):,}",
    )
    y -= 18

    draw_line(X0, y, "KPI SUMMARY", "Helvetica-Bold", 12)
    y -= 14
    for k, v in kpis.items():
        if k == "SEO_SUMMARY_LINES":
            continue
        draw_line(X0 + 10, y, f"- {k}: {v}")
        y -= ROWH
    y -= 6

    c.setFont("Helvetica-Bold", 10)
    x = X0
    for i, h in enumerate(COLS):
        c.drawString(x + 2, y, h)
        x += CW[i]
    y -= ROWH
    c.setFont("Helvetica", 9)

    for v in videos:
        title_lines = _wrap(v.get("title", ""), 36)
        desc_lines = _wrap((v.get("description") or ""), 34)
        max_lines = max(len(title_lines), len(desc_lines))
        for i in range(max_lines):
            x = X0
            c.drawString(
                x + 2, y, title_lines[i] if i < len(title_lines) else ""
            )
            x += CW[0]
            c.drawString(
                x + 2, y, desc_lines[i] if i < len(desc_lines) else ""
            )
            x += CW[1]
            if i == 0:
                c.drawRightString(
                    x + CW[2] - 4, y, f"{int(v.get('comments') or 0):,}"
                )
                x += CW[2]
                c.drawRightString(
                    x + CW[3] - 4, y, f"{int(v.get('views') or 0):,}"
                )
                x += CW[3]
                c.drawRightString(
                    x + CW[4] - 4, y, f"{int(v.get('seo_score') or 0)}"
                )
            y -= ROWH
            if y < 80:
                y = new_page()
                c.setFont("Helvetica-Bold", 10)
                x = X0
                for i, h in enumerate(COLS):
                    c.drawString(x + 2, y, h)
                    x += CW[i]
                y -= ROWH
                c.setFont("Helvetica", 9)
        y -= 4
        if y < 80:
            y = new_page()

    draw_line(X0, y, "SEO SCORING SUMMARY", "Helvetica-Bold", 12)
    y -= 14
    draw_line(
        X0 + 10, y, f"Average SEO score: {kpis.get('Avg SEO', '0/100')}"
    )
    y -= ROWH
    for line_txt in kpis.get("SEO_SUMMARY_LINES", []):
        for chunk in _wrap(f"• {line_txt}", 95):
            draw_line(X0 + 10, y, chunk)
            y -= ROWH
            if y < 60:
                y = new_page()
    y -= 8

    draw_line(X0, y, "SPECIFIC IMPROVEMENTS", "Helvetica-Bold", 12)
    y -= 14
    med_views = (
        int(pd.Series([int(v.get("views") or 0) for v in videos]).median())
        if videos
        else 0
    )
    for idx, v in enumerate(videos, start=1):
        tips = improvements_for_video(v, med_views)
        draw_line(X0, y, f"VID {idx}:", "Helvetica-Bold", 10)
        y -= ROWH
        for t in tips[:4]:
            for chunk in _wrap(f"• {t}", 95):
                draw_line(X0 + 10, y, chunk)
                y -= ROWH
                if y < 60:
                    y = new_page()
        y -= 6
        if y < 60:
            y = new_page()

    draw_line(X0, y, "IMPROVEMENT SUMMARY:", "Helvetica-Bold", 12)
    y -= 14
    for s in global_summary(videos)[:5]:
        for chunk in _wrap(f"• {s}", 95):
            draw_line(X0 + 10, y, chunk)
            y -= ROWH
            if y < 60:
                y = new_page()
    y -= 6

    draw_line(X0, y, "VIDEO WINS:", "Helvetica-Bold", 12)
    y -= 14
    for w in quick_wins(videos)[:5]:
        for chunk in _wrap(f"• {w}", 95):
            draw_line(X0 + 10, y, chunk)
            y -= ROWH
            if y < 60:
                y = new_page()

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------
# Audience retention helpers
# ---------------------------------------------------------------------
def extract_video_id(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if "youtube.com" in s or "youtu.be" in s:
        from urllib.parse import urlparse, parse_qs

        p = urlparse(s)
        if p.netloc.endswith("youtu.be"):
            return p.path.strip("/").split("/")[0]
        if p.path.startswith("/watch"):
            qs = parse_qs(p.query)
            return (qs.get("v", [""])[0]).strip()
        last = p.path.strip("/").split("/")[-1]
        return last
    return s


def parse_yt_duration_iso8601(d: str) -> int:
    if not d or not d.startswith("PT"):
        return 0
    d = d[2:]
    num = ""
    total = 0
    for ch in d:
        if ch.isdigit():
            num += ch
        else:
            if not num:
                continue
            val = int(num)
            if ch == "H":
                total += val * 3600
            elif ch == "M":
                total += val * 60
            elif ch == "S":
                total += val
            num = ""
    return total


def top_drop_insights(df: pd.DataFrame, video_secs: int, k: int = 5) -> list[str]:
    if df.empty or video_secs <= 0:
        return []
    d = df.copy()
    d["drop"] = d["audienceWatchRatio"].diff().fillna(0.0)
    big = d.nsmallest(k, "drop")
    out = []
    for _, r in big.iterrows():
        t = int(round(float(r["elapsedVideoTimeRatio"]) * video_secs))
        pct = max(0.0, -float(r["drop"]) * 100.0)
        if t <= 10:
            tip = "open with a stronger hook, quick payoff in 0–10s"
        elif t <= 30:
            tip = "tighten intro, cut filler, show the outcome earlier"
        elif t <= 60:
            tip = "restate value, add motion/B-roll, remove a dead sentence"
        else:
            tip = "refresh pacing or add pattern-break (graphic, jump-cut, reveal)"
        out.append(f"~{pct:.1f}% viewers dropped near {t}s → {tip}.")
    return out


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------
st.title("YouTube Audit Pro")
require_license()

tabs = st.tabs(
    ["Audit", "Competition", "Audience Retention", "Thumbnail Ideas", "Video Ideas"]
)

# ================================================================
# Audit tab
# ================================================================
with tabs[0]:
    st.subheader("Audit")
    channel_input = st.text_input(
        "Channel URL / @handle / Channel ID",
        value="https://www.youtube.com/@ImanGadzhi",
    )
    recent_n = st.slider("Recent videos to audit", 3, 30, 5)

    if st.button("Run Audit"):
        yt = yt_key_service()
        ch_id = resolve_channel_id(yt, channel_input) or parse_channel_or_id(
            channel_input
        )
        if not ch_id:
            st.error("Could not resolve channel.")
            st.stop()

        uploads = fetch_recent_videos(yt, ch_id, recent_n)
        stats = fetch_video_stats(yt, uploads["video_id"].tolist())

        df = stats.merge(
            uploads[["video_id", "published", "title"]],
            on="video_id",
            how="left",
            suffixes=("", "_upl"),
        )
        if "title_upl" in df.columns:
            df["title"] = df["title"].fillna(df["title_upl"])
            df.drop(columns=["title_upl"], inplace=True)

        df = engagement_rates(view_velocity(df))
        td = title_diagnostics(df)
        df = pd.concat([df, td], axis=1)

        seo_scores, seo_notes = [], []
        for _, r in df.iterrows():
            score, notes = seo_score_row(
                title=str(r.get("title", "")),
                desc=str(r.get("description", "")),
                like_rate=float(r.get("like_rate_%") or 0.0),
                comment_rate=float(r.get("comment_rate_%") or 0.0),
                dup_penalty=int(r.get("dup_word_penalty") or 0),
            )
            seo_scores.append(score)
            seo_notes.append(notes)
        df["seo_score"] = seo_scores
        df["seo_notes"] = seo_notes
        seo_avg = int(np.nanmean(seo_scores)) if seo_scores else 0

        std = df["views_per_min"].std(ddof=0)
        if std == 0 or np.isnan(std):
            df["vpm_z"] = 0
        else:
            z = (df["views_per_min"] - df["views_per_min"].mean()) / std
            df["vpm_z"] = z.replace([np.inf, -np.inf], np.nan).fillna(0)

        df["health_100"] = (
            (df["vpm_z"].clip(-2, 3) + 2) / 5 * 60
            + df["like_rate_%"].fillna(0).clip(0, 5) * 4
            - df["dup_word_penalty"].clip(lower=0, upper=3) * 3
            + df["title_ok_len"].astype(int) * 5
        ).round(1).clip(0, 100)

        vids = len(df)
        avg_views = int(df["views"].mean()) if vids else 0
        med_likes = (
            int(df["likes"].median())
            if df["likes"].notna().sum() and vids
            else 0
        )

        vpm_raw = df["views_per_min"].mean(skipna=True) if vids else 0
        vpm = int(vpm_raw) if pd.notna(vpm_raw) else 0

        cad = cadence_stats(df)

        st.metric("Avg SEO score", f"{seo_avg}/100")
        st.dataframe(
            df[
                [
                    "title",
                    "views",
                    "likes",
                    "comments",
                    "published",
                    "views_per_min",
                    "like_rate_%",
                    "comment_rate_%",
                    "seo_score",
                    "health_100",
                ]
            ],
            use_container_width=True,
            height=320,
        )

        kd = keyword_density(df["title"].tolist())
        insights = [
            f"Cadence: {cad.get('uploads_week', 0)} uploads/week. Median gap {cad.get('median_gap_days', 0)} days.",
            f"Best posting time (UTC): {cad.get('best_day')} @ {cad.get('best_hour_utc')}:00. Consistency {cad.get('consistency_100', 0)}/100.",
            "Aim 45–70 chars, one number + one clear outcome. Avoid duplicate words.",
            "Keep description >200 chars with 1–2 key links near top. Add chapters.",
            "Refresh low-VPM videos with new title/thumbnail within 24h.",
        ]
        st.write("**Quick wins**")
        st.markdown("\n".join([f"- {w}" for w in insights]))

        ch_meta = fetch_channel_meta(yt, ch_id) or {
            "name": str(channel_input),
            "url": "",
            "subscribers": 0,
            "total_views": 0,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
        }
        st.session_state["channel_info"] = ch_meta

        vids_list = []
        for _, r in df.iterrows():
            vids_list.append(
                {
                    "title": str(r.get("title", "")),
                    "description": str(r.get("description", "")),
                    "comments": int(r.get("comments") or 0),
                    "likes": int(r.get("likes") or 0),
                    "views": int(r.get("views") or 0),
                    "seo_score": int(r.get("seo_score") or 0),
                    "upload_date": str(r.get("published", ""))[:10],
                }
            )
        st.session_state["videos"] = vids_list

        kpis = {
            "Videos": str(vids),
            "Avg views": f"{avg_views:,}",
            "Median likes": f"{med_likes:,}",
            "Avg views/min": f"{vpm:,}",
            "Consistency": f"{cad.get('consistency_100', 0)}/100",
            "Avg SEO": f"{seo_avg}/100",
        }

        def _miss(flag):
            return sum(1 for n in df["seo_notes"] if not n.get(flag, False))

        seo_summary_counts = {
            "Missing number in title": _miss("has_number"),
            "No power word": _miss("power_word"),
            "Title length off": _miss("title_len_ok"),
            "No chapters": _miss("chapters"),
            "No link/CTA": _miss("links"),
            "Short description": _miss("desc_len_ok"),
        }
        kpis["SEO_SUMMARY_LINES"] = [
            f"{k}: {v} videos" for k, v in seo_summary_counts.items()
        ]
        st.session_state["kpis"] = kpis
        st.session_state["insights"] = insights
        st.session_state["top_keywords"] = kd.index.tolist()

    ch = st.session_state.get("channel_info")
    vv = st.session_state.get("videos")
    if ch and vv:
        if st.button("Generate Elite PDF"):
            pdf_bytes = build_elite_pdf(
                ch,
                vv,
                st.session_state.get("kpis", {}),
                st.session_state.get("insights", []),
                st.session_state.get("top_keywords", []),
            )
            st.download_button(
                "Download Elite PDF",
                data=pdf_bytes,
                file_name=f"YouTube_Audit_{ch.get('name', 'channel')}.pdf",
                mime="application/pdf",
            )
    else:
        st.caption("Run Audit to enable Elite PDF.")

# ================================================================
# Competition tab
# ================================================================
with tabs[1]:
    st.subheader("Competition")
    base_input = st.text_input(
        "Your Channel URL/ID",
        value="https://www.youtube.com/@ImanGadzhi",
        key="comp_you",
    )
    compA_input = st.text_input(
        "Competitor A URL/ID",
        value="https://www.youtube.com/@AlexHormozi",
        key="comp_a",
    )
    compB_input = st.text_input(
        "Competitor B URL/ID", value="", key="comp_b"
    )
    comp_n = st.slider(
        "Recent videos per channel", 3, 40, 5, key="comp_n"
    )

    if st.button("Run Comparison"):
        yt = yt_key_service()
        rows = []
        for who, inp in [("You", base_input), ("A", compA_input), ("B", compB_input)]:
            if not inp.strip():
                continue
            cid = resolve_channel_id(yt, inp) or parse_channel_or_id(inp)
            if not cid:
                continue
            up = fetch_recent_videos(yt, cid, comp_n)
            stt = fetch_video_stats(yt, up["video_id"].tolist())
            merged = stt.merge(
                up[["video_id", "published", "title"]],
                on="video_id",
                how="left",
                suffixes=("", "_upl"),
            )
            if "title_upl" in merged.columns:
                merged["title"] = merged["title"].fillna(merged["title_upl"])
                merged.drop(columns=["title_upl"], inplace=True)
            merged = engagement_rates(view_velocity(merged))
            merged.insert(0, "who", who)
            rows.append(merged)
        if not rows:
            st.warning("No channels resolved.")
            st.stop()
        comp_df = pd.concat(rows, ignore_index=True)
        st.dataframe(
            comp_df[
                [
                    "who",
                    "title",
                    "views",
                    "likes",
                    "comments",
                    "published",
                    "views_per_min",
                    "like_rate_%",
                ]
            ],
            use_container_width=True,
            height=340,
        )

# ================================================================
# Audience Retention tab
# ================================================================
with tabs[2]:
    st.subheader("Audience Retention Analyzer")
    vid_in = st.text_input(
        "Video URL or ID",
        value="",
        help="Must be a video on the authenticated channel",
    )

    if st.button("Analyze Retention"):
        vid = extract_video_id(vid_in)
        if not vid:
            st.error("Enter a valid video URL or ID.")
            st.stop()

        try:
            yt_key = yt_key_service()
            yt_oauth, session = yt_oauth_clients()
        except Exception as e:
            st.error(f"Auth initialisation failed: {e}")
            st.stop()

        # Ownership check
        try:
            vmeta = (
                yt_key.videos()
                .list(part="snippet,contentDetails", id=vid)
                .execute()
            )
            items = vmeta.get("items", [])
            if not items:
                st.error("Video not found.")
                st.stop()
            owner_ch = items[0]["snippet"]["channelId"]

            mine = yt_oauth.channels().list(part="id", mine=True).execute()
            my_items = mine.get("items") or []
            my_ch = my_items[0].get("id") if my_items else None
            if not my_ch:
                st.error("Authenticated Google account has no YouTube channel.")
                st.stop()
            if owner_ch != my_ch:
                st.error(
                    "Google requires the video to be on the authenticated channel.\n\n"
                    "Log in with the YouTube channel that owns this video, then retry."
                )
                st.stop()
        except Exception as e:
            st.error(f"Ownership check failed: {e}")
            st.stop()

        # Analytics call via AuthorizedSession
        try:
            start_date = (
                datetime.utcnow().date() - timedelta(days=365 * 5)
            ).strftime("%Y-%m-%d")
            end_date = datetime.utcnow().date().strftime("%Y-%m-%d")
            params = {
                "ids": "channel==MINE",
                "startDate": start_date,
                "endDate": end_date,
                "metrics": "audienceWatchRatio,relativeRetentionPerformance",
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={vid}",
                "sort": "elapsedVideoTimeRatio",
            }
            resp = yta_reports(session, params)
        except Exception as e:
            st.error(f"Retention analysis failed: {e}")
            st.stop()

        cols = [h["name"] for h in resp.get("columnHeaders", [])]
        rows = resp.get("rows", [])
        df = pd.DataFrame(rows, columns=cols)

        if df.empty:
            st.warning(
                "No retention data available for this video (try a different video)."
            )
            st.stop()

        df["elapsed_%"] = (
            df["elapsedVideoTimeRatio"].astype(float) * 100
        ).round(1)
        df["watch_%"] = (
            df["audienceWatchRatio"].astype(float) * 100
        ).round(1)
        df.rename(
            columns={"relativeRetentionPerformance": "relative_perf"},
            inplace=True,
        )

        st.dataframe(
            df[["elapsed_%", "watch_%", "relative_perf"]],
            use_container_width=True,
            height=260,
        )

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(
            df["elapsedVideoTimeRatio"].astype(float),
            df["audienceWatchRatio"].astype(float),
            linewidth=2,
        )
        ax.set_title("Absolute retention")
        ax.set_xlabel("Elapsed video time ratio (0–1)")
        ax.set_ylabel("Audience watch ratio")
        ax.grid(alpha=0.2)
        st.pyplot(fig, use_container_width=True)

        duration_iso = items[0]["contentDetails"].get("duration", "")
        total_secs = parse_yt_duration_iso8601(duration_iso)
        insights = top_drop_insights(df, total_secs, k=5)

        st.markdown("### Actionable insights")
        if not insights:
            st.info(
                "No clear drop points detected. Keep pacing tight in the first 30–60 seconds."
            )
        else:
            for tip in insights:
                st.markdown(f"- {tip}")

# ================================================================
# Thumbnail Ideas tab
# ================================================================
with tabs[3]:
    st.subheader("Thumbnail Ideas")
    hint = st.text_input("Title hint", value="")
    if st.button("Generate 10 ideas"):
        key = (re.findall(r"[A-Za-z']{3,}", hint.lower()) or ["Your Bot"])[0].title()
        ideas = [
            "Make £100/Day | Right split | You pointing | Blurred stats | Electric blue",
            f"Fix {key} Fast | Left bar | You with wrench | Circuit board | Neon green",
            "Bot vs Human | Half split | You vs robot arm | Studio grey | Red/blue clash",
            "This Broke My Sales | Top/bottom | Shock face + ↓ arrow | Sales chart | Red accent",
            "Do This, Not That | Two panels | Tick & cross | Clean gradient | Lime vs red",
            "24h Challenge | Center portrait | Stopwatch | City night | Orange glow",
            "3 Hidden Tricks | Left text | Hand with 3 fingers | Soft blur | Sky blue",
            "I Copied a Millionaire | Right text | Notebook pose | Office bokeh | Gold accent",
            "From 0→1k Subs | Diagonal split | Growth arrow | Graph BG | Bright teal",
            "Truth About AI | Center big text | You + robot eye | Dark vignette | Cyan accent",
        ]
        st.write("\n".join([f"- {i}" for i in ideas]))

# ================================================================
# Video Ideas tab
# ================================================================
with tabs[4]:
    st.subheader("Video Ideas")
    chan_in = st.text_input(
        "Channel (URL, @handle, or ID)",
        value="https://www.youtube.com/@ImanGadzhi",
    )
    how_many = st.slider("How many?", 5, 25, 8)

    def _clean_term(t: str) -> str:
        t = re.sub(r"[^A-Za-z0-9 ']+", " ", t or "").strip().lower()
        stop = {
            "make",
            "made",
            "doing",
            "do",
            "this",
            "that",
            "thing",
            "stuff",
            "way",
            "ways",
            "day",
            "best",
        }
        words = [w for w in t.split() if w not in stop]
        s = " ".join(words).strip()
        return s.title() if s else "Strategy"

    def _cap60(s: str) -> str:
        return s if len(s) <= 60 else s[:57].rstrip() + "..."

    templates = [
        "How I Made £{num} With {seed}",
        "{seed}: 7 Simple Steps",
        "From 0 to 1,000 Subs Using {seed}",
        "Do This Before Starting {seed}",
        "Stop Doing This With {seed}",
        "The Truth About {seed}",
        "3 Mistakes Killing Your {seed}",
        "{seed} vs {alt}: What Works Now",
        "The Ultimate {seed} Guide",
        "{seed} For Beginners: Complete Setup",
        "Scale Faster With {seed}",
        "I Tried {seed} For 30 Days",
        "Avoid These {seed} Traps",
        "£0 To £1,000 With {seed}",
        "My Exact {seed} Workflow",
    ]
    alts = ["SEO", "Ads", "Automation", "Funnels", "Content", "Emails"]
    nums = ["100", "500", "1,000", "10,000"]

    if st.button("Generate"):
        yt = yt_key_service()
        cid = resolve_channel_id(yt, chan_in) or parse_channel_or_id(chan_in)
        if not cid:
            st.error("Could not resolve channel")
            st.stop()

        base = fetch_recent_videos(yt, cid, 40)
        titles = base["title"].tolist()
        dens = keyword_density(titles).head(12)
        seeds = (dens.index.tolist() or ["automation", "content", "offers", "ads"])[
            :12
        ]

        ideas = []
        used = set()
        i = 0
        while len(ideas) < how_many and i < 100:
            i += 1
            raw = _clean_term(seeds[(len(ideas) + i) % len(seeds)])
            alt = alts[(len(ideas) + i) % len(alts)]
            num = nums[(len(ideas) + i) % len(nums)]
            t = templates[(len(ideas) + i) % len(templates)]
            title = _cap60(t.format(seed=raw, alt=alt, num=num))
            title = re.sub(
                r"\b(Make|Made)\s+Made\b", "Made", title, flags=re.I
            )
            if title not in used:
                ideas.append(title)
                used.add(title)

        st.write("\n".join([f"- {i}" for i in ideas]))
