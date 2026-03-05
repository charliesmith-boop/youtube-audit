# YouTube Audit Pro — unified single-file app
# DEMO default + license unlock + secure reseller tree + custom white-label branding (no presets)
#
# Core behaviour preserved:
# - Demo mode is AUTOMATIC when there is NO valid active license key
# - VALID active license unlocks ALL tabs + features
# - Audit tab usable in demo but LIMITED to 3 total audits
# - Competition / Audience Retention / Thumbnail Ideas / Video Ideas show "Locked in Demo" in demo
# - Elite PDF export locked in demo
#
# Security model:
# - licenses.json stores licenses + role + parent/created_by tree
# - Owner password grants full control
# - Resellers authenticate via their license_key + admin_code (hashed)
# - Resellers can only see/manage their own subtree; cannot see/delete other resellers or their keys
#
# White-label branding model (premium):
# - No theme presets. Brand is CUSTOM: brand name, logo URL, accent/bg/panel/text/muted, radius
# - Branding can be set on a reseller (or owner) and automatically INHERITS down the tree
# - A client will display their reseller’s branding without you manually tagging every client
#
# Requires env vars:
#   YOUTUBE_API_KEY or YT_API_KEY
#   OWNER_PASSWORD (required for full owner admin)
#   OWNER_LICENSE_KEY (recommended)
#   GOOGLE_CLIENT_SECRET_FILE (defaults to client_secret.json)
#   GOOGLE_OAUTH_TOKEN_FILE   (defaults to token.json)
#   LICENSE_STORE_FILE (optional, defaults to licenses.json)
from __future__ import annotations
import os
import io
import json
import ssl
import re
import hmac
import base64
import secrets
import hashlib
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
# Bootstrap / config
# ---------------------------------------------------------------------
ssl._create_default_https_context = ssl._create_unverified_context
load_dotenv()
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
st.set_page_config(page_title="YouTube Audit Pro", layout="wide")
YOUTUBE_API_KEY = (os.getenv("YOUTUBE_API_KEY") or os.getenv("YT_API_KEY") or "").strip()
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
CLIENT_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json")
TOKEN_FILE = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "token.json")
# --- Streamlit Cloud OAuth bootstrap (no local browser) ---
# Store your client_secret.json and token.json in Streamlit Secrets as:
#   GOOGLE_CLIENT_SECRET_JSON = """{...}"""
#   GOOGLE_OAUTH_TOKEN_JSON   = """{...}"""
# This writes them to files at runtime so yt_oauth_clients() can load them.
def _write_oauth_files_from_secrets():
    try:
        cs = st.secrets.get("GOOGLE_CLIENT_SECRET_JSON", "")
        tk = st.secrets.get("GOOGLE_OAUTH_TOKEN_JSON", "")
        if cs and not os.path.exists(CLIENT_FILE):
            with open(CLIENT_FILE, "w", encoding="utf-8") as f:
                f.write(cs)
        if tk:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(tk)
    except Exception:
        # If secrets aren't configured, keep running (local dev will use files on disk)
        pass
_write_oauth_files_from_secrets()
LICENSE_FILE = os.getenv("LICENSE_STORE_FILE", "licenses.json")
OWNER_PASSWORD = (os.getenv("OWNER_PASSWORD") or "").strip()
OWNER_LICENSE_KEY = (os.getenv("OWNER_LICENSE_KEY") or "").strip()
OWNER_LICENSE_KEY = OWNER_LICENSE_KEY or "O-SUPPORTLY-OWNER"
# ---------------------------------------------------------------------
# Branding / UI theming (CUSTOM per tree, no presets)
# ---------------------------------------------------------------------
def _default_brand() -> dict:
    return {
        "brand_name": "YouTube Audit Pro",
        "logo_url": "",
        "accent": "#27a6ff",
        "bg": "#020617",
        "panel": "#0f172a",
        "text": "#ffffff",
        "muted": "#e5e7eb",
        "radius": 22,
    }
def _clamp_hex(s: str, fallback: str) -> str:
    s = (s or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s
    return fallback
def _sanitize_brand(raw: dict | None) -> dict:
    base = _default_brand()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    out["brand_name"] = str(raw.get("brand_name") or base["brand_name"]).strip()[:60]
    out["logo_url"] = str(raw.get("logo_url") or "").strip()[:500]
    out["accent"] = _clamp_hex(str(raw.get("accent") or ""), base["accent"])
    out["bg"] = _clamp_hex(str(raw.get("bg") or ""), base["bg"])
    out["panel"] = _clamp_hex(str(raw.get("panel") or ""), base["panel"])
    out["text"] = _clamp_hex(str(raw.get("text") or ""), base["text"])
    out["muted"] = _clamp_hex(str(raw.get("muted") or ""), base["muted"])
    try:
        out["radius"] = int(raw.get("radius", base["radius"]))
        out["radius"] = max(8, min(32, out["radius"]))
    except Exception:
        out["radius"] = base["radius"]
    return out
def apply_base_ui(brand: dict) -> None:
    brand = _sanitize_brand(brand)
    accent = brand["accent"]
    bg = brand["bg"]
    panel = brand["panel"]
    text = brand["text"]
    muted = brand["muted"]
    radius = int(brand["radius"])
    CSS = f"""
    <style>
    :root {{
      --bg:{bg};
      --panel:{panel};
      --border-soft:rgba(148,163,184,0.45);
      --text:{text};
      --muted:{muted};
      --accent:{accent};
      --radius-lg:{radius}px;
      --cursor-x:0.5;
      --cursor-y:0.5;
    }}
    html, body, * {{
      color: var(--text) !important;
    }}
    /* App background reacts to mouse position */
    html, body, [data-testid="stAppViewContainer"] > .main {{
      background:
        radial-gradient(
          circle at calc(var(--cursor-x)*100%) calc(var(--cursor-y)*100%),
          color-mix(in srgb, var(--accent) 55%, transparent),
          transparent 55%
        ),
        radial-gradient(circle at 0% 100%, rgba(168,85,247,0.18), transparent 60%),
        radial-gradient(circle at 100% 0%, rgba(59,130,246,0.18), transparent 55%),
        var(--bg);
      transition: background 0.08s linear;
    }}
    /* Subtle moving grid overlay */
    [data-testid="stAppViewContainer"]::before {{
      content:"";
      position: fixed;
      inset:0;
      pointer-events:none;
      background-image:
        linear-gradient(rgba(15,23,42,0.65) 1px, transparent 1px),
        linear-gradient(90deg, rgba(15,23,42,0.65) 1px, transparent 1px);
      background-size: 32px 32px;
      mix-blend-mode: soft-light;
      opacity:0.35;
      z-index:-2;
      animation:grid-shift 40s linear infinite;
    }}
    @keyframes grid-shift {{
      0% {{ transform: translate3d(0,0,0); }}
      50% {{ transform: translate3d(-16px,8px,0); }}
      100% {{ transform: translate3d(0,0,0); }}
    }}
    /* Ambient glow layer */
    [data-testid="stAppViewContainer"]::after {{
      content:"";
      position: fixed;
      inset:-25%;
      pointer-events:none;
      background:
        radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--accent) 25%, transparent), transparent 60%),
        radial-gradient(circle at 100% 100%, rgba(236,72,153,0.10), transparent 60%);
      filter: blur(40px);
      opacity:0.9;
      z-index:-3;
    }}
    /* Sidebar */
    section[data-testid="stSidebar"] {{
      background: linear-gradient(180deg, var(--bg), var(--bg) 40%, #000 100%) !important;
      border-right: 1px solid rgba(148,163,184,0.25);
    }}
    section[data-testid="stSidebar"] * {{ color: var(--text) !important; }}
    /* Hero */
    .yt-hero {{
      position: relative;
      padding: 1.6rem 2rem 1.2rem 2rem;
      border-radius: 28px;
      background:
        radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--accent) 45%, transparent), transparent 55%),
        radial-gradient(circle at 100% 0%, rgba(248,113,113,0.12), transparent 60%),
        linear-gradient(135deg, rgba(3,7,30,0.96), rgba(3,7,40,0.98));
      border: 1px solid var(--border-soft);
      box-shadow: 0 32px 90px rgba(0,0,0,0.9);
      overflow: hidden;
      margin-bottom: 1.3rem;
    }}
    .yt-hero::before {{
      content:"";
      position:absolute;
      inset:-40%;
      background: conic-gradient(from 180deg,
        color-mix(in srgb, var(--accent) 30%, transparent),
        rgba(236,72,153,0.16),
        rgba(56,189,248,0.22),
        color-mix(in srgb, var(--accent) 30%, transparent));
      opacity:0.7;
      filter: blur(42px);
      animation: hero-spin 26s linear infinite;
      z-index:-1;
    }}
    @keyframes hero-spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    .yt-hero h1 {{ font-size: 1.7rem; letter-spacing: .03em; margin-bottom: .35rem; }}
    .yt-hero-sub {{ font-size: .95rem; color: var(--muted) !important; max-width: 640px; }}
    .yt-hero-pill {{
      display:inline-flex; align-items:center; gap:.4rem;
      padding:.25rem .75rem; border-radius:999px;
      border:1px solid rgba(148,163,184,0.6);
      background:rgba(15,23,42,0.85);
      font-size:.75rem; margin-bottom:.75rem;
    }}
    .yt-hero-pill span:last-child {{ color: var(--accent) !important; }}
    /* Section cards */
    .yt-section-card {{
      position: relative;
      padding: 1.1rem 1.2rem 1.2rem 1.2rem;
      margin-top: .5rem;
      border-radius: var(--radius-lg);
      background:
        radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 60%),
        linear-gradient(135deg, rgba(15,23,42,0.96), rgba(15,23,42,0.92));
      border:1px solid rgba(148,163,184,0.45);
      box-shadow: 0 22px 55px rgba(0,0,0,0.85);
      overflow:hidden;
    }}
    .yt-section-card::before {{
      content:"";
      position:absolute;
      inset:-1px;
      border-radius: inherit;
      border:1px solid transparent;
      background: conic-gradient(
        from 0deg,
        rgba(39,166,255,0.0),
        color-mix(in srgb, var(--accent) 75%, transparent),
        rgba(248,250,252,0.0),
        rgba(59,130,246,0.6),
        rgba(39,166,255,0.0)
      );
      mask: linear-gradient(#000 0 0) padding-box, linear-gradient(#000 0 0);
      mask-composite: exclude;
      opacity:0.55;
      animation:border-glow 18s linear infinite;
      pointer-events:none;
    }}
    @keyframes border-glow {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    /* Inputs */
    div[data-baseweb="input"] input,
    textarea,
    .stTextInput>div>div>input,
    .stTextArea textarea {{
      background: rgba(15,23,42,0.95) !important;
      border-radius: 12px !important;
      border: 1px solid rgba(148,163,184,0.55) !important;
      color: var(--text) !important;
    }}
    /* --- FIX: input containers + placeholders + password eye toggle --- */
    /* Ensure the whole input wrapper isn't white */
    div[data-baseweb="input"] > div {{
      background: rgba(15,23,42,0.95) !important;
      border-radius: 12px !important;
    }}
    /* Placeholder/readability */
    div[data-baseweb="input"] input::placeholder,
    textarea::placeholder,
    .stTextInput input::placeholder,
    .stTextArea textarea::placeholder {{
      color: rgba(229,231,235,0.70) !important;
      opacity: 1 !important;
    }}
    /* Password reveal eye button/icon visibility */
    div[data-baseweb="input"] button,
    .stTextInput button {{
      background: transparent !important;
      border: 0 !important;
      box-shadow: none !important;
      color: rgba(229,231,235,0.85) !important;
    }}
    div[data-baseweb="input"] button svg,
    .stTextInput button svg {{
      fill: rgba(229,231,235,0.85) !important;
      color: rgba(229,231,235,0.85) !important;
      opacity: 1 !important;
    }}
    div[data-baseweb="input"] button:hover svg,
    .stTextInput button:hover svg {{
      fill: var(--accent) !important;
      color: var(--accent) !important;
    }}
    /* Buttons (robust selectors across Streamlit versions) */
    .stButton>button,
    .stDownloadButton>button,
    div[data-testid^="stBaseButton-"] button {{
      background:
        radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--accent) 35%, transparent), transparent 55%),
        linear-gradient(135deg, #0f172a, #020617) !important;
      color: var(--text) !important;
      border-radius: 999px !important;
      border: 1px solid color-mix(in srgb, var(--accent) 85%, transparent) !important;
      padding: .45rem 1.2rem !important;
      font-weight: 600 !important;
      letter-spacing: .02em !important;
      box-shadow: 0 18px 32px rgba(15,23,42,0.85) !important;
      transition: all .18s ease-out !important;
    }}
    .stButton>button:hover,
    .stDownloadButton>button:hover,
    div[data-testid^="stBaseButton-"] button:hover {{
      transform: translateY(-1px) scale(1.01);
      box-shadow: 0 28px 60px rgba(15,23,42,0.9) !important;
      border-color: color-mix(in srgb, var(--accent) 100%, #38bdf8 40%) !important;
    }}
    .stButton>button:disabled,
    .stDownloadButton>button:disabled,
    div[data-testid^="stBaseButton-"] button:disabled {{
      opacity: 0.55 !important;
      filter: saturate(0.8);
      cursor: not-allowed !important;
    }}
/* Tabs */
    .stTabs {{ margin-bottom: .75rem; }}
    .stTabs [data-baseweb="tab-list"] {{
      display:flex; flex-wrap:wrap; justify-content:flex-start; gap:.45rem;
    }}
    .stTabs [data-baseweb="tab"] {{
      background: rgba(15,23,42,0.9);
      color: #e5e7eb !important;
      border-radius: 999px;
      padding: .35rem .9rem;
      border: 1px solid rgba(30,64,175,0.8);
      font-size: .85rem;
      font-weight: 500;
      box-shadow: 0 10px 28px rgba(15,23,42,0.75);
    }}
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
      background:
        radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--accent) 55%, transparent), transparent 60%),
        linear-gradient(135deg, #1d4ed8, var(--accent));
      color: #f9fafb !important;
      border-color: rgba(191,219,254,0.9);
    }}
    /* Dataframes */
    [data-testid="stDataFrame"] {{
      border-radius: 16px;
      overflow: hidden;
      border:1px solid rgba(30,64,175,0.65);
      box-shadow: 0 20px 50px rgba(15,23,42,0.9);
    }}
    [data-testid="stDataFrame"] table {{ color: var(--text) !important; }}
    /* --- FIX: selectbox / dropdown menus (BaseWeb) --- */
    div[data-baseweb="select"] > div {{
      background: rgba(15,23,42,0.95) !important;
      border-radius: 12px !important;
      border: 1px solid rgba(148,163,184,0.55) !important;
      color: var(--text) !important;
    }}
    div[role="listbox"] {{
      background: rgba(15,23,42,0.98) !important;
      border: 1px solid rgba(148,163,184,0.55) !important;
      border-radius: 12px !important;
      overflow: hidden !important;
    }}
    div[role="option"] {{
      background: transparent !important;
      color: var(--text) !important;
    }}
    div[role="option"]:hover,
    div[aria-selected="true"][role="option"] {{
      background: color-mix(in srgb, var(--accent) 25%, transparent) !important;
      color: var(--text) !important;
    }}
    /* --- FIX: selectbox dropdown popover menu (BaseWeb portal) --- */
div[data-baseweb="popover"] div[data-baseweb="menu"] {{
  background: rgba(15,23,42,0.98) !important;
  border: 1px solid rgba(148,163,184,0.55) !important;
  border-radius: 12px !important;
  box-shadow: 0 30px 70px rgba(0,0,0,0.9) !important;
  overflow: hidden !important;
}}
div[data-baseweb="popover"] div[role="option"] {{
  background: transparent !important;
  color: var(--text) !important;
}}
div[data-baseweb="popover"] div[role="option"]:hover {{
  background: color-mix(in srgb, var(--accent) 25%, transparent) !important;
  color: var(--text) !important;
}}
div[data-baseweb="popover"] div[role="option"][aria-selected="true"] {{
  background: color-mix(in srgb, var(--accent) 30%, transparent) !important;
  color: var(--text) !important;
}}
div[data-baseweb="popover"] * {{
  color: var(--text) !important;
}}
    /* Muted text utility */
    .muted {{ color: var(--muted) !important; }}
    
    /* ===== HARD FIX: Streamlit/BaseWeb select dropdown popover visibility ===== */
    /* Some Streamlit versions render the menu as a popover with a listbox (no data-baseweb="menu"). */
    /* Popover surface */
    div[data-baseweb="popover"] {{
      background: rgba(15,23,42,0.98) !important;
      border-radius: 12px !important;
    }}
    /* The listbox/menu container */
    div[data-baseweb="popover"] [role="listbox"],
    div[data-baseweb="popover"] div[data-baseweb="menu"],
    div[data-baseweb="popover"] ul {{
      background: rgba(15,23,42,0.98) !important;
      border: 1px solid rgba(148,163,184,0.55) !important;
      border-radius: 12px !important;
      box-shadow: 0 30px 70px rgba(0,0,0,0.90) !important;
      overflow: hidden !important;
    }}
    /* Option rows */
    div[data-baseweb="popover"] [role="option"],
    div[data-baseweb="popover"] li {{
      background: transparent !important;
      color: rgba(255,255,255,0.92) !important;
    }}
    /* Force option label text (covers nested spans/divs) */
    div[data-baseweb="popover"] [role="option"] * ,
    div[data-baseweb="popover"] li * {{
      color: rgba(255,255,255,0.92) !important;
    }}
    /* Hover/active */
    div[data-baseweb="popover"] [role="option"]:hover,
    div[data-baseweb="popover"] li:hover {{
      background: rgba(39,166,255,0.18) !important;
    }}
    /* Selected */
    div[data-baseweb="popover"] [role="option"][aria-selected="true"],
    div[data-baseweb="popover"] li[aria-selected="true"] {{
      background: rgba(39,166,255,0.26) !important;
    }}
</style>
    """
    st.markdown(CSS, unsafe_allow_html=True)
MOUSE_JS = """
<script>
document.addEventListener('mousemove', function(e) {
  const x = e.clientX / window.innerWidth;
  const y = e.clientY / window.innerHeight;
  document.documentElement.style.setProperty('--cursor-x', x.toString());
  document.documentElement.style.setProperty('--cursor-y', y.toString());
});
</script>
"""
# ---------------------------------------------------------------------
# Secure hashing helpers (PBKDF2)
# ---------------------------------------------------------------------
def _hash_secret(secret: str, salt_b64: str | None = None) -> str:
    secret_b = (secret or "").encode("utf-8")
    if not salt_b64:
        salt = secrets.token_bytes(16)
        salt_b64 = base64.b64encode(salt).decode("utf-8")
    else:
        salt = base64.b64decode(salt_b64.encode("utf-8"))
    dk = hashlib.pbkdf2_hmac("sha256", secret_b, salt, 200_000)
    dk_b64 = base64.b64encode(dk).decode("utf-8")
    return f"pbkdf2_sha256${salt_b64}${dk_b64}"
def _verify_secret(secret: str, stored: str) -> bool:
    try:
        algo, salt_b64, _ = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        test = _hash_secret(secret, salt_b64)
        return hmac.compare_digest(test, stored)
    except Exception:
        return False
def _gen_license_key(prefix: str = "L") -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"
def _gen_admin_code() -> str:
    return secrets.token_urlsafe(10).replace("-", "").replace("_", "")
# ---------------------------------------------------------------------
# License store + tree permissions
# ---------------------------------------------------------------------
def _gs_available() -> bool:
    try:
        _ = st.secrets.get("SHEET_ID")
        __ = st.secrets.get("SHEET_NAME")
        ___ = st.secrets.get("GOOGLE_SERVICE_ACCOUNT")
        return bool(_ and __ and ___)
    except Exception:
        return False


def _gs_open_worksheet():
    import gspread  # type: ignore
    from google.oauth2.service_account import Credentials  # type: ignore

    creds_dict = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(st.secrets["SHEET_ID"])
    ws = sh.worksheet(st.secrets["SHEET_NAME"])
    return ws


def _gs_required_headers() -> list[str]:
    # Must match your sheet (source of truth)
    return [
        "license_key",
        "role",
        "active",
        "white_label",
        "parent",
        "created_by",
        "admin_hash",
        "brand",
        "created_utc",
        "updated_utc",
    ]


def _gs_ensure_schema(ws) -> dict[str, int]:
    """
    Ensures required headers exist (adds missing headers to the right).
    Returns a header->col_index (1-based) map.
    NEVER clears/re-writes the sheet.
    """
    required = _gs_required_headers()

    try:
        header_row = ws.row_values(1)
    except Exception:
        header_row = []

    header_row = [str(h).strip() for h in (header_row or []) if str(h).strip()]

    if not header_row:
        ws.update("A1", [required])
        return {h: i + 1 for i, h in enumerate(required)}

    existing_set = set(header_row)
    missing = [h for h in required if h not in existing_set]
    if missing:
        ws.update("A1", [header_row + missing])
        header_row = header_row + missing

    return {h: (header_row.index(h) + 1) for h in required}


def _gs_bool_to_cell(v: object) -> str:
    return "TRUE" if bool(v) else "FALSE"


def _gs_parse_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    return s in {"true", "1", "yes", "y", "t"}


def _gs_find_row(ws, hk: dict[str, int], license_key: str) -> int | None:
    key = (license_key or "").strip()
    if not key:
        return None
    col = hk["license_key"]
    try:
        col_vals = ws.col_values(col)  # includes header at index 0
    except Exception:
        return None
    # rows are 1-based; row 1 is header
    for i, val in enumerate(col_vals[1:], start=2):
        if str(val).strip() == key:
            return i
    return None


def _gs_license_to_row(hk: dict[str, int], license_key: str, lic: dict, existing_created_utc: str | None) -> dict[str, str]:
    now = datetime.now(timezone.utc).isoformat()
    role = str((lic or {}).get("role") or "client").strip().lower()
    role = role if role in {"owner", "reseller", "client"} else "client"

    active = _gs_bool_to_cell((lic or {}).get("active", False))
    wl = _gs_bool_to_cell((lic or {}).get("white_label", False))

    parent = (lic or {}).get("parent") or ""
    created_by = (lic or {}).get("created_by") or ""
    admin_hash = (lic or {}).get("admin_hash") or ""

    brand = (lic or {}).get("brand")
    if isinstance(brand, (dict, list)):
        brand_cell = json.dumps(brand, ensure_ascii=False)
    else:
        brand_cell = str(brand or "")

    created_utc = str((lic or {}).get("created_utc") or existing_created_utc or now)
    updated_utc = now

    return {
        "license_key": str(license_key),
        "role": role,
        "active": active,
        "white_label": wl,
        "parent": str(parent or ""),
        "created_by": str(created_by or ""),
        "admin_hash": str(admin_hash or ""),
        "brand": brand_cell,
        "created_utc": created_utc,
        "updated_utc": updated_utc,
    }


def _gs_fetch_licenses() -> dict:
    if not _gs_available():
        return {}
    try:
        ws = _gs_open_worksheet()
        hk = _gs_ensure_schema(ws)
        records = ws.get_all_records(expected_headers=list(hk.keys()))
        out: dict[str, dict] = {}
        for r in records:
            k = str(r.get("license_key") or "").strip()
            if not k:
                continue
            lic: dict = {
                "role": str(r.get("role") or "client").strip().lower(),
                "active": _gs_parse_bool(r.get("active")),
                "white_label": _gs_parse_bool(r.get("white_label")),
                "parent": (str(r.get("parent") or "").strip() or None),
                "created_by": (str(r.get("created_by") or "").strip() or None),
                "admin_hash": (str(r.get("admin_hash") or "").strip() or None),
                "created_utc": (str(r.get("created_utc") or "").strip() or None),
                "updated_utc": (str(r.get("updated_utc") or "").strip() or None),
            }
            brand_raw = r.get("brand")
            if isinstance(brand_raw, str) and brand_raw.strip():
                try:
                    lic["brand"] = json.loads(brand_raw)
                except Exception:
                    lic["brand"] = brand_raw
            elif brand_raw:
                lic["brand"] = brand_raw
            out[k] = lic
        return out
    except Exception as e:
        try:
            print(f"[licenses] Google Sheets read failed: {e}")
        except Exception:
            pass
        return {}


def _gs_get_created_utc(ws, hk: dict[str, int], row: int) -> str | None:
    try:
        val = ws.cell(row, hk["created_utc"]).value
        return str(val).strip() if val else None
    except Exception:
        return None


def _gs_upsert_license(license_key: str, lic: dict) -> bool:
    """
    Upsert exactly ONE license row (append or update).
    NEVER clears/re-writes the sheet.
    """
    if not _gs_available():
        return False
    try:
        ws = _gs_open_worksheet()
        hk = _gs_ensure_schema(ws)
        row = _gs_find_row(ws, hk, license_key)
        existing_created_utc = _gs_get_created_utc(ws, hk, row) if row else None
        row_map = _gs_license_to_row(hk, license_key, lic, existing_created_utc)

        if row is None:
            # Append row in required header order
            values = [row_map[h] for h in _gs_required_headers()]
            ws.append_row(values, value_input_option="RAW")
            return True

        # Update only specific cells (one batch call)
        updates = []
        for h in _gs_required_headers():
            col = hk[h]
            a1 = f"{_gs_col_to_a1(col)}{row}"
            updates.append({"range": a1, "values": [[row_map[h]]]})
        ws.batch_update(updates)
        return True
    except Exception as e:
        try:
            print(f"[licenses] Google Sheets upsert failed: {e}")
        except Exception:
            pass
        return False


def _gs_delete_license(license_key: str) -> bool:
    """
    Delete exactly ONE license row (by license_key).
    NEVER clears/re-writes the sheet.
    """
    if not _gs_available():
        return False
    try:
        ws = _gs_open_worksheet()
        hk = _gs_ensure_schema(ws)
        row = _gs_find_row(ws, hk, license_key)
        if row is None:
            return True
        ws.delete_rows(row)
        return True
    except Exception as e:
        try:
            print(f"[licenses] Google Sheets delete failed: {e}")
        except Exception:
            pass
        return False


def _gs_col_to_a1(col: int) -> str:
    # 1 -> A, 2 -> B, ... 27 -> AA
    n = int(col)
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _lic_load() -> dict:
    """Load licenses from Google Sheets (single source of truth)."""
    if not _gs_available():
        st.error("License store unavailable (Google Sheets not configured).")
        return {}
    try:
        return _gs_fetch_licenses() or {}
    except Exception as e:
        st.error(f"License store read failed: {e}")
        return {}


def _lic_upsert_one(license_key: str, lic: dict) -> bool:
    """Upsert exactly ONE license row in Google Sheets. Never rewrites the sheet."""
    if not _gs_available():
        st.error("License write failed: Google Sheets not configured.")
        st.stop()
    ok = _gs_upsert_license(license_key, lic)
    if not ok:
        st.error("License write failed: could not save to Google Sheets. No changes were applied.")
        st.stop()
    return True


def _lic_delete_one(license_key: str) -> bool:
    """Delete exactly ONE license row in Google Sheets. Never rewrites the sheet."""
    if not _gs_available():
        st.error("License delete failed: Google Sheets not configured.")
        st.stop()
    ok = _gs_delete_license(license_key)
    if not ok:
        st.error("License delete failed: could not delete from Google Sheets. No changes were applied.")
        st.stop()
    return True


def _lic_is_active(lic: dict | None) -> bool:
    return bool(lic and isinstance(lic, dict) and lic.get("active", False))
def _lic_role(lic: dict | None) -> str:
    r = (lic or {}).get("role") or "client"
    r = str(r).strip().lower()
    return r if r in {"owner", "reseller", "client"} else "client"
def _ensure_owner_seed():
    if not OWNER_LICENSE_KEY:
        return
    s = _lic_load()
    if OWNER_LICENSE_KEY not in s:
        lic = {
            "role": "owner",
            "active": True,
            "white_label": True,
            "brand": _default_brand(),  # owner can brand too
            "parent": None,
            "created_by": None,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        }
        _lic_upsert_one(OWNER_LICENSE_KEY, lic)
def _subtree_keys(store: dict, root_key: str) -> set[str]:
    root_key = (root_key or "").strip()
    kids_map: dict[str, list[str]] = {}
    for k, v in store.items():
        if not isinstance(v, dict):
            continue
        parent = (v.get("parent") or "").strip() if v.get("parent") else None
        if parent:
            kids_map.setdefault(parent, []).append(k)
    out = set()
    stack = [root_key]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        for ch in kids_map.get(cur, []):
            stack.append(ch)
    return out
def _can_manage(actor_key: str, target_key: str, store: dict) -> bool:
    actor = store.get(actor_key)
    if _lic_role(actor) == "owner":
        return True
    if _lic_role(actor) == "reseller":
        subtree = _subtree_keys(store, actor_key)
        return target_key in subtree
    return False
def _can_delete(actor_key: str, target_key: str, store: dict) -> bool:
    # Never allow deleting the owner key (if configured)
    if OWNER_LICENSE_KEY and target_key == OWNER_LICENSE_KEY:
        return False
    actor = store.get(actor_key)
    target = store.get(target_key)
    if _lic_role(actor) == "owner":
        return True
    if _lic_role(actor) == "reseller":
        # Reseller can't delete themselves, can't delete other resellers, can't delete owner
        if target_key == actor_key:
            return False
        if _lic_role(target) != "client":
            return False
        return _can_manage(actor_key, target_key, store)
    return False
def _create_client_license(store: dict, reseller_key: str, active: bool = True) -> tuple[str, str]:
    new_key = _gen_license_key("L")
    while new_key in store:
        new_key = _gen_license_key("L")
    admin_code = _gen_admin_code()
    store[new_key] = {
        "role": "client",
        "active": bool(active),
        "white_label": False,  # branding inherits from parent by default
        "brand": None,
        "admin_hash": _hash_secret(admin_code),
        "parent": reseller_key,
        "created_by": reseller_key,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    return new_key, admin_code
def _create_reseller_license(store: dict, active: bool = True) -> tuple[str, str]:
    new_key = _gen_license_key("R")
    while new_key in store:
        new_key = _gen_license_key("R")
    admin_code = _gen_admin_code()
    store[new_key] = {
        "role": "reseller",
        "active": bool(active),
        "white_label": True,
        "brand": _default_brand(),
        "admin_hash": _hash_secret(admin_code),
        "parent": OWNER_LICENSE_KEY or None,
        "created_by": OWNER_LICENSE_KEY or None,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    return new_key, admin_code
def _reset_admin_code(store: dict, target_key: str) -> str:
    code = _gen_admin_code()
    lic = store.get(target_key, {})
    if not isinstance(lic, dict):
        lic = {}
    lic["admin_hash"] = _hash_secret(code)
    lic["updated_utc"] = datetime.now(timezone.utc).isoformat()
    store[target_key] = lic
    return code
def _effective_brand_for_license(license_key: str, store: dict, max_hops: int = 12) -> dict:
    """
    Returns the branding that should apply for this license:
    - If license has white_label True and has brand -> use it
    - Else walk up parent chain and use the first white_label brand found
    - Else default brand
    """
    base = _default_brand()
    k = (license_key or "").strip()
    seen = set()
    for _ in range(max_hops):
        if not k or k in seen:
            break
        seen.add(k)
        lic = store.get(k)
        if isinstance(lic, dict) and _lic_is_active(lic):
            if bool(lic.get("white_label", False)):
                b = lic.get("brand")
                if isinstance(b, dict):
                    return _sanitize_brand(b)
                # If white_label is on but brand missing, still return default brand
                return base
            parent = (lic.get("parent") or "").strip() if lic.get("parent") else ""
            k = parent
            continue
        break
    return base
def _mask_key(k: str) -> str:
    k = k or ""
    if len(k) <= 6:
        return "****"
    return f"{k[:2]}****{k[-4:]}"
# ---------------------------------------------------------------------
# Session defaults
# ---------------------------------------------------------------------
_ensure_owner_seed()
if "licensed" not in st.session_state:
    st.session_state["licensed"] = False
if "license_key" not in st.session_state:
    st.session_state["license_key"] = ""
if "demo_audits_used" not in st.session_state:
    st.session_state["demo_audits_used"] = 0
if "owner_mode" not in st.session_state:
    st.session_state["owner_mode"] = False
if "reseller_mode" not in st.session_state:
    st.session_state["reseller_mode"] = False
def _sync_license_state():
    """Auto-validate the currently stored license_key each run (no flaky state)."""
    key = (st.session_state.get("license_key") or "").strip()
    if not key:
        st.session_state["licensed"] = False
        return
    store = _lic_load()
    lic = store.get(key)
    st.session_state["licensed"] = bool(isinstance(lic, dict) and _lic_is_active(lic))
def demo_mode() -> bool:
    return not bool(st.session_state.get("licensed", False))
def show_locked(title: str, bullets: list[str], footer: str):
    st.markdown(f"## 🔒 {title} (Locked in Demo)")
    st.markdown("What you’ll unlock in the Agency Suite:")
    st.markdown("\n".join([f"- {b}" for b in bullets]))
    st.markdown(
        f"""
        <div style="
            margin-top:16px;
            padding:14px 18px;
            border-radius:12px;
            background: rgba(0,0,0,0.25);
            border: 1px solid rgba(255,255,255,0.08);
        ">
            <strong>{footer}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )
def render_guidance(title, what, steps, output=None):
    """
    Pure UI helper: explains what a feature does + how to use it.
    Does NOT touch logic. Safe to call anywhere.
    """
    with st.expander(f"🧭 {title}: what to do (60 seconds)", expanded=True):
        st.markdown("**What this does**")
        st.markdown("\n".join([f"- {w}" for w in what]))
        st.markdown("**How to use it**")
        st.markdown("\n".join([f"{i+1}. {s}" for i, s in enumerate(steps)]))
        if output:
            st.markdown("**What you get**")
            st.markdown("\n".join([f"- {o}" for o in output]))
# Keep license validity accurate
_sync_license_state()
# Compute brand for current viewer (inherit down the tree)
_store_now = _lic_load()
_active_key = (st.session_state.get("license_key") or "").strip()
active_brand = _effective_brand_for_license(_active_key, _store_now) if _active_key else _default_brand()
# Apply UI + mouse effect early so controls render correctly
apply_base_ui(active_brand)
st.markdown(MOUSE_JS, unsafe_allow_html=True)
# ---------------------------------------------------------------------
# Sidebar: Activation + Auth + Admin
# ---------------------------------------------------------------------
if not YOUTUBE_API_KEY:
    st.sidebar.warning("Set YOUTUBE_API_KEY (or YT_API_KEY) in your .env")
brand_name = active_brand.get("brand_name", "YouTube Audit Pro")
logo_url = (active_brand.get("logo_url") or "").strip()
if logo_url:
    st.sidebar.image(logo_url, use_container_width=True)
st.sidebar.title(brand_name)
# Activation
lic_in = st.sidebar.text_input("License key", value=st.session_state.get("license_key", ""))
colA, colB = st.sidebar.columns([1, 1])
with colA:
    if st.button("Activate"):
        k = (lic_in or "").strip()
        store = _lic_load()
        lic = store.get(k)
        if isinstance(lic, dict) and _lic_is_active(lic):
            st.session_state["license_key"] = k
            st.session_state["licensed"] = True
            st.sidebar.success("License valid. Access enabled.")
            st.rerun()
        else:
            st.session_state["license_key"] = ""
            st.session_state["licensed"] = False
            st.sidebar.warning("No valid active license key. Demo mode enabled.")
            st.rerun()
with colB:
    if st.button("Logout"):
        st.session_state["owner_mode"] = False
        st.session_state["reseller_mode"] = False
        st.session_state["license_key"] = ""
        st.session_state["licensed"] = False
        st.rerun()
st.sidebar.markdown("---")
# Owner admin
owner_pwd = st.sidebar.text_input("Owner password", type="password", value="")
if st.sidebar.button("Open Owner Admin"):
    ENV_OWNER = (os.getenv("OWNER_PASSWORD") or "").strip()
    FALLBACK_OWNER = "SupportlyAdmin00478"
    valid = False
    if ENV_OWNER and owner_pwd == ENV_OWNER:
        valid = True
    elif owner_pwd == FALLBACK_OWNER:
        valid = True
    if valid:
        st.session_state["owner_mode"] = True
        st.session_state["reseller_mode"] = False
        st.sidebar.success("Owner admin enabled.")
        st.rerun()
    else:
        st.session_state["owner_mode"] = False
        st.sidebar.error("Wrong owner password.")
# Reseller admin
admin_code = st.sidebar.text_input("Admin code", type="password", value="")
if st.sidebar.button("Open Reseller Admin"):
    key = (st.session_state.get("license_key") or "").strip()
    store = _lic_load()
    lic = store.get(key) if key else None
    if not key or not isinstance(lic, dict):
        st.session_state["reseller_mode"] = False
        st.sidebar.error("Activate your license key first.")
    else:
        role = _lic_role(lic)
        if role not in {"reseller", "owner"}:
            st.session_state["reseller_mode"] = False
            st.sidebar.error("This license is not a reseller/admin account.")
        else:
            stored = (lic.get("admin_hash") or "").strip()
            if stored and _verify_secret(admin_code, stored):
                st.session_state["reseller_mode"] = True
                st.session_state["owner_mode"] = (role == "owner")
                st.sidebar.success("Reseller admin enabled.")
                st.rerun()
            else:
                st.session_state["reseller_mode"] = False
                st.sidebar.error("Wrong admin code.")
# Demo banner
if demo_mode():
    st.info(
        "Demo Mode: limited preview. Upgrade to unlock full analytics, competition + retention, and Elite PDF export."
    )
# ---------------------------------------------------------------------
# Admin panels (owner + reseller)
# ---------------------------------------------------------------------
def _brand_editor_ui(prefix: str, current: dict) -> dict:
    cur = _sanitize_brand(current)
    st.sidebar.markdown("#### Branding (custom)")
    bn = st.sidebar.text_input("Brand name", value=cur["brand_name"], key=f"{prefix}_bn")
    lu = st.sidebar.text_input("Logo URL (optional)", value=cur["logo_url"], key=f"{prefix}_lu")
    c_accent = st.sidebar.color_picker("Accent", value=cur["accent"], key=f"{prefix}_accent")
    c_bg = st.sidebar.color_picker("Background", value=cur["bg"], key=f"{prefix}_bg")
    c_panel = st.sidebar.color_picker("Panel", value=cur["panel"], key=f"{prefix}_panel")
    c_text = st.sidebar.color_picker("Text", value=cur["text"], key=f"{prefix}_text")
    c_muted = st.sidebar.color_picker("Muted text", value=cur["muted"], key=f"{prefix}_muted")
    radius = st.sidebar.slider("Corner radius", 8, 32, int(cur["radius"]), key=f"{prefix}_radius")
    return _sanitize_brand(
        {
            "brand_name": (bn or "").strip(),
            "logo_url": (lu or "").strip(),
            "accent": c_accent,
            "bg": c_bg,
            "panel": c_panel,
            "text": c_text,
            "muted": c_muted,
            "radius": int(radius),
        }
    )
def _render_reseller_panel():
    st.sidebar.markdown("## Reseller Admin")
    store = _lic_load()
    me = (st.session_state.get("license_key") or "").strip()
    if not me or not isinstance(store.get(me), dict):
        st.sidebar.warning("Activate your reseller license first.")
        return
    my_role = _lic_role(store.get(me))
    if my_role not in {"reseller", "owner"}:
        st.sidebar.error("Not a reseller account.")
        return
    visible = _subtree_keys(store, me) if my_role == "reseller" else set(store.keys())
    st.sidebar.markdown("### Create client license")
    active_default = st.sidebar.toggle("Client active", value=True, key="new_client_active")
    if st.sidebar.button("Generate client license"):
        store = _lic_load()
        if not _can_manage(me, me, store):
            st.sidebar.error("Permission denied.")
            return
        new_key, admin_plain = _create_client_license(store, me, active=active_default)
        _lic_upsert_one(new_key, store[new_key])
        st.sidebar.success("Client license created.")
        st.sidebar.code(f"LICENSE: {new_key}\nADMIN CODE: {admin_plain}")
    st.sidebar.markdown("### Your tree")
    rows = []
    for k in sorted(visible):
        v = store.get(k)
        if not isinstance(v, dict):
            continue
        # Hide other resellers from a reseller (only show themselves + clients)
        if my_role == "reseller" and _lic_role(v) == "reseller" and k != me:
            continue
        rows.append(
            {
                "key": ("ME" if k == me else _mask_key(k)),
                "role": _lic_role(v),
                "active": bool(v.get("active", False)),
                "white_label": bool(v.get("white_label", False)),
                "parent": ("—" if not v.get("parent") else _mask_key(str(v.get("parent")))),
                "created": (v.get("created_utc") or "")[:19].replace("T", " "),
            }
        )
    if rows:
        st.sidebar.dataframe(pd.DataFrame(rows), use_container_width=True, height=230)
    else:
        st.sidebar.caption("No licenses found in your tree.")
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Edit (scoped)")
    editable = [k for k in sorted(visible) if isinstance(store.get(k), dict)]
    if my_role == "reseller":
        # reseller can edit: themselves + clients only
        editable = [k for k in editable if (_lic_role(store.get(k)) == "client" or k == me)]
    if not editable:
        st.sidebar.caption("Nothing editable.")
        return
    target = st.sidebar.selectbox(
        "Select license (your tree)",
        editable,
        format_func=lambda x: ("ME (reseller)" if x == me else _mask_key(x)),
        key="reseller_target",
    )
    store = _lic_load()
    if not _can_manage(me, target, store):
        st.sidebar.error("Permission denied.")
        return
    lic = store.get(target, {})
    if not isinstance(lic, dict):
        st.sidebar.error("Invalid license record.")
        return
    st.sidebar.markdown("#### Access controls")
    active = st.sidebar.toggle("Active", value=bool(lic.get("active", False)), key="edit_active")
    white_label = st.sidebar.toggle("White-label enabled", value=bool(lic.get("white_label", False)), key="edit_wl")
    # Branding editor:
    # - If editing a client: branding usually inherited; only edit if WL enabled for that client
    # - If editing self reseller: this is the tree root branding (recommended)
    cur_brand = lic.get("brand") if isinstance(lic.get("brand"), dict) else (_default_brand() if target == me else {})
    brand_payload = _brand_editor_ui("edit", cur_brand)
    apply_to_subtree = False
    if target == me and _lic_role(store.get(me)) in {"reseller", "owner"}:
        apply_to_subtree = st.sidebar.toggle("Apply branding to entire subtree", value=True, key="apply_tree")
    if st.sidebar.button("Save theme settings"):
        store = _lic_load()
        if not _can_manage(me, target, store):
            st.sidebar.error("Permission denied.")
            return
        lic2 = store.get(target, {})
        if not isinstance(lic2, dict):
            st.sidebar.error("Invalid license record.")
            return
        lic2["active"] = bool(active)
        lic2["white_label"] = bool(white_label)
        # Save branding only if WL enabled; otherwise keep stored brand but it won't apply
        lic2["brand"] = brand_payload if bool(white_label) else lic2.get("brand", None)
        lic2["updated_utc"] = datetime.now(timezone.utc).isoformat()
        store[target] = lic2
        if apply_to_subtree and target == me:
            sub = _subtree_keys(store, me)
            for k in sub:
                if k == me:
                    continue
                v = store.get(k)
                if not isinstance(v, dict):
                    continue
                # Don’t force clients to WL on; they will inherit automatically.
                # If a client already has WL on, update their brand too so they stay consistent.
                if bool(v.get("white_label", False)):
                    v["brand"] = brand_payload
                    v["updated_utc"] = datetime.now(timezone.utc).isoformat()
                    store[k] = v
                # Persist only the rows that changed (no full-sheet rewrite)
        _lic_upsert_one(target, store[target])
        if apply_to_subtree and target == me:
            sub = _subtree_keys(store, me)
            for k in sub:
                if k == me:
                    continue
                v = store.get(k)
                if not isinstance(v, dict):
                    continue
                if bool(v.get("white_label", False)):
                    _lic_upsert_one(k, v)
        st.sidebar.success("Saved.")
        st.rerun()
    if st.sidebar.button("Reset admin code (shows once)"):
        store = _lic_load()
        if not _can_manage(me, target, store):
            st.sidebar.error("Permission denied.")
            return
        # reseller can only reset admin codes for clients (and themselves)
        if _lic_role(store.get(me)) == "reseller" and _lic_role(store.get(target)) != "client" and target != me:
            st.sidebar.error("Not allowed.")
            return
        new_code = _reset_admin_code(store, target)
        _lic_upsert_one(target, store[target])
        st.sidebar.warning("Admin code reset. Copy it now:")
        st.sidebar.code(new_code)
    if st.sidebar.button("Delete license"):
        store = _lic_load()
        if not _can_delete(me, target, store):
            st.sidebar.error("Not allowed.")
            return
        _lic_delete_one(target)
        st.sidebar.success("Deleted.")
        st.rerun()
def _render_owner_panel():
    st.sidebar.markdown("## Owner Admin")
    store = _lic_load()
    st.sidebar.markdown("### Create reseller")
    reseller_active = st.sidebar.toggle("Reseller active", value=True, key="new_reseller_active")
    if st.sidebar.button("Generate reseller account"):
        store = _lic_load()
        new_key, admin_plain = _create_reseller_license(store, active=reseller_active)
        _lic_upsert_one(new_key, store[new_key])
        st.sidebar.success("Reseller created.")
        st.sidebar.code(f"RESELLER LICENSE: {new_key}\nADMIN CODE: {admin_plain}")
    st.sidebar.markdown("---")
    st.sidebar.markdown("### All licenses (owner)")
    rows = []
    for k, v in store.items():
        if not isinstance(v, dict):
            continue
        rows.append(
            {
                "key": k,
                "role": _lic_role(v),
                "active": bool(v.get("active", False)),
                "parent": v.get("parent"),
                "created_by": v.get("created_by"),
                "white_label": bool(v.get("white_label", False)),
                "created": (v.get("created_utc") or "")[:19].replace("T", " "),
            }
        )
    if rows:
        st.sidebar.dataframe(pd.DataFrame(rows), use_container_width=True, height=260)
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Owner edit / delete")
    all_keys = [k for k in sorted(store.keys()) if isinstance(store.get(k), dict)]
    if not all_keys:
        return
    target = st.sidebar.selectbox("Select license", all_keys, key="owner_target")
    lic = store.get(target, {})
    if not isinstance(lic, dict):
        st.sidebar.error("Invalid record.")
        return
    active = st.sidebar.toggle("Active", value=bool(lic.get("active", False)), key="owner_active")
    white_label = st.sidebar.toggle("White-label enabled", value=bool(lic.get("white_label", False)), key="owner_wl")
    role = st.sidebar.selectbox(
        "Role",
        ["client", "reseller", "owner"],
        index=["client", "reseller", "owner"].index(_lic_role(lic)),
        key="owner_role",
    )
    cur_brand = lic.get("brand") if isinstance(lic.get("brand"), dict) else _default_brand()
    brand_payload = _brand_editor_ui("owner", cur_brand)
    apply_to_subtree = False
    if _lic_role(lic) in {'reseller','owner'} and bool(white_label):
        apply_to_subtree = st.sidebar.toggle("Apply branding to this license's subtree", value=False, key="owner_apply_tree")
    if st.sidebar.button("Save license"):
        store = _lic_load()
        lic2 = store.get(target, {})
        if not isinstance(lic2, dict):
            st.sidebar.error("Invalid record.")
            return
        lic2["active"] = bool(active)
        lic2["white_label"] = bool(white_label)
        lic2["role"] = role
        lic2["brand"] = brand_payload if bool(white_label) else lic2.get("brand", None)
        lic2["updated_utc"] = datetime.now(timezone.utc).isoformat()
        store[target] = lic2
        # Persist only the row that changed (no full-sheet rewrite)
        _lic_upsert_one(target, store[target])
        if apply_to_subtree:
            sub = _subtree_keys(store, target)
            for k in sub:
                if k == target:
                    continue
                v = store.get(k)
                if not isinstance(v, dict):
                    continue
                if bool(v.get('white_label', False)):
                    v['brand'] = brand_payload
                    v['updated_utc'] = datetime.now(timezone.utc).isoformat()
                    store[k] = v
                    _lic_upsert_one(k, v)
        st.sidebar.success("Saved.")
        st.rerun()
    if st.sidebar.button("Reset admin code (shows once)"):
        store = _lic_load()
        new_code = _reset_admin_code(store, target)
        _lic_upsert_one(target, store[target])
        st.sidebar.warning("Admin code reset. Copy it now:")
        st.sidebar.code(new_code)
    if st.sidebar.button("Delete license (owner)"):
        if OWNER_LICENSE_KEY and target == OWNER_LICENSE_KEY:
            st.sidebar.error("Owner license key is protected.")
        else:
            store = _lic_load()
            _lic_delete_one(target)
            st.sidebar.success("Deleted.")
            st.rerun()
if st.session_state.get("owner_mode", False):
    _render_owner_panel()
elif st.session_state.get("reseller_mode", False):
    _render_reseller_panel()
# ---------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def yt_key_service():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY, cache_discovery=False)
@st.cache_resource(show_spinner=False)
def yt_oauth_clients():
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
def parse_channel_or_id(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("UC"):
        return s
    if "youtube.com" in s and "/channel/" in s:
        return s.split("/channel/")[1].split("/")[0]
    return s
@st.cache_data(show_spinner=False, ttl=120)
def resolve_channel_id(_yt, text: str) -> str | None:
    t = (text or "").strip()
    if t.startswith("UC"):
        return t
    if "youtube.com" in t:
        p = urlparse(t)
        if p.path.startswith("/channel/"):
            return p.path.split("/channel/")[1]
        if p.path.startswith("/@"):
            handle = p.path[2:]
            r = _yt.search().list(part="snippet", q=handle, type="channel", maxResults=1).execute()
            it = r.get("items", [])
            return it[0]["snippet"]["channelId"] if it else None
    r = _yt.search().list(part="snippet", q=t, type="channel", maxResults=1).execute()
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
        resp = _yt.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=pid,
            maxResults=50,
            pageToken=token
        ).execute()
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
        return pd.DataFrame(columns=["video_id", "title", "views", "likes", "comments", "description"])
    rows = []
    for chunk in [ids[i: i + 50] for i in range(0, len(ids), 50)]:
        resp = _yt.videos().list(part="statistics,snippet", id=",".join(chunk)).execute()
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
                    "comments": int(s.get("commentCount", 0)) if "commentCount" in s else np.nan,
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
def yta_reports(session: AuthorizedSession, params: dict) -> dict:
    url = "https://youtubeanalytics.googleapis.com/v2/reports"
    r = session.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()
# ---------------------------------------------------------------------
# Analysis helpers (UNCHANGED)
# ---------------------------------------------------------------------
def engagement_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["like_rate_%"] = (out["likes"] / out["views"].replace(0, np.nan) * 100).round(2)
    out["comment_rate_%"] = (out["comments"] / out["views"].replace(0, np.nan) * 100).round(2)
    return out
def view_velocity(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["published_dt"] = pd.to_datetime(out["published"], utc=True, errors="coerce")
    now = datetime.now(timezone.utc)
    out["age_min"] = (now - out["published_dt"]).dt.total_seconds() / 60
    out["views_per_min"] = (out["views"] / out["age_min"].clip(lower=1)).round(2)
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
        words += re.findall(r"[A-Za-z']{3,}", (t or "").lower())
    stop = set(
        "the a an and or for with your this that what why how into from to of on in out are was were been being you my our their his her more most very".split()
    )
    words = [w for w in words if w not in stop]
    if not words:
        return pd.Series(dtype=int)
    return pd.Series(words).value_counts()
_POWER = re.compile(r"\b(best|secret|fast|simple|ultimate|new|proof|free|easy|guide|mistake|hack|win|earn|rich|money|truth|behind|strategy|blueprint)\b", re.I)
def _has_number(s: str) -> bool:
    return any(ch.isdigit() for ch in (s or ""))
def _has_link(s: str) -> bool:
    return bool(re.search(r"https?://", (s or "")))
def _has_chapters(s: str) -> bool:
    return bool(re.search(r"\b\d{1,2}:\d{2}\b", (s or "")))
def seo_score_row(title: str, desc: str, like_rate: float, comment_rate: float, dup_penalty: int) -> tuple[int, dict]:
    title = title or ""
    desc = desc or ""
    pts = 0
    notes: dict[str, object] = {}
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
    notes.update({"title_len_ok": ok_len, "power_word": pw, "has_number": num, "dup_penalty": dup_penalty})
    long_desc = len(desc) >= 200
    chapters = _has_chapters(desc)
    links = _has_link(desc)
    if long_desc:
        pts += 15
    if chapters:
        pts += 10
    if links:
        pts += 10
    notes.update({"desc_len_ok": long_desc, "chapters": chapters, "links": links})
    lr = max(0.0, float(like_rate or 0.0))
    cr = max(0.0, float(comment_rate or 0.0))
    pts += int(min(lr, 5.0) / 5.0 * 18)
    pts += int(min(cr, 1.0) / 1.0 * 7)
    notes.update({"like_rate_%": lr, "comment_rate_%": cr})
    return int(min(100, pts)), notes
# ---------------------------------------------------------------------
# PDF helpers (UNCHANGED)
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
        tips.append("Test a stronger thumbnail: big face + 2–4 words + strong contrast.")
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
def build_elite_pdf(channel: dict, videos: list[dict], kpis: dict, insights: list[str], keywords: list[str]) -> bytes:
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
    draw_line(X0, y, f"YOUTUBE AUDIT TOOL — {channel.get('name', '')}", "Helvetica-Bold", 16)
    y -= 22
    draw_line(X0, y, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    y -= 14
    draw_line(X0, y, f"Subs: {channel.get('subscribers', 0):,}   Total views: {channel.get('total_views', 0):,}")
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
            c.drawString(x + 2, y, title_lines[i] if i < len(title_lines) else "")
            x += CW[0]
            c.drawString(x + 2, y, desc_lines[i] if i < len(desc_lines) else "")
            x += CW[1]
            if i == 0:
                c.drawRightString(x + CW[2] - 4, y, f"{int(v.get('comments') or 0):,}")
                x += CW[2]
                c.drawRightString(x + CW[3] - 4, y, f"{int(v.get('views') or 0):,}")
                x += CW[3]
                c.drawRightString(x + CW[4] - 4, y, f"{int(v.get('seo_score') or 0)}")
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
    draw_line(X0 + 10, y, f"Average SEO score: {kpis.get('Avg SEO', '0/100')}")
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
    med_views = int(pd.Series([int(v.get("views") or 0) for v in videos]).median()) if videos else 0
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
# Audience retention helpers (UNCHANGED)
# ---------------------------------------------------------------------
def extract_video_id(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if "youtube.com" in s or "youtu.be" in s:
        from urllib.parse import urlparse as _u, parse_qs
        p = _u(s)
        if p.netloc.endswith("youtu.be"):
            return p.path.strip("/").split("/")[0]
        if p.path.startswith("/watch"):
            qs = parse_qs(p.query)
            return (qs.get("v", [""])[0]).strip()
        last = p.path.strip("/").split("/")[-1]
        return last
    return s
def _get_transcript_segments(video_id: str):
    """
    Best-effort transcript fetch.
    - Uses youtube_transcript_api if available (no OAuth).
    - If unavailable / disabled, returns None.
    """
    vid = (video_id or "").strip()
    if not vid:
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except Exception:
        return None
    try:
        segs = YouTubeTranscriptApi.get_transcript(vid)
        if isinstance(segs, list) and segs:
            return segs
    except Exception:
        return None
    return None
def _transcript_snippet(segs, t_sec: int, window_sec: int = 8) -> str:
    if not segs or t_sec is None:
        return ""
    try:
        lo = max(0, int(t_sec) - int(window_sec))
        hi = int(t_sec) + int(window_sec)
        chunks = []
        for s in segs:
            stt = float(s.get("start", 0.0))
            if lo <= stt <= hi:
                txt = str(s.get("text", "")).replace("\n", " ").strip()
                if txt:
                    chunks.append(txt)
        out = []
        prev = ""
        for c in chunks:
            if c != prev:
                out.append(c)
            prev = c
        return " ".join(out).strip()
    except Exception:
        return ""
def _retention_fix_suggestion(t_sec, focus_window: str) -> str:
    # No LLM calls. Simple premium, actionable templates.
    if t_sec is None:
        return "Tighten the first 30 seconds: lead with outcome, show proof early, and cut any setup."
    try:
        t_sec = int(t_sec)
    except Exception:
        return "Tighten the first 30 seconds: lead with outcome, show proof early, and cut any setup."
    if t_sec <= 10:
        return "Start with the payoff in the first 3–5 seconds (result/proof), then explain the context after."
    if t_sec <= 30:
        return "Remove intro/setup. Add a pattern-break at ~8–12s and show proof/clip before the first explanation."
    if t_sec <= 60:
        return "Add a mid-hook: restate the promise, introduce a constraint ('but here’s the catch…'), or insert a proof clip."
    if focus_window == "Full video":
        return "Add pattern-breaks every 30–45s: quick proof, b-roll swap, on-screen text, or a mini-reset of the goal."
    return "Shorten the section before this moment and reintroduce tension/curiosity right before the drop."
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
def _retention_scorecard(df: pd.DataFrame, total_secs: int) -> dict:
    """Agency-friendly retention interpretation.
    Returns a dict with score (0-100), key metrics, and ranked issues (highest impact first).
    """
    if df.empty:
        return {"score": 0, "metrics": {}, "issues": [], "wins": []}
    d = df.copy()
    d["t_ratio"] = d["elapsedVideoTimeRatio"].astype(float).clip(0, 1)
    d["watch"] = d["audienceWatchRatio"].astype(float).clip(lower=0)
    d = d.sort_values("t_ratio")
    # Helper: watch ratio at a given second mark (nearest)
    def at_sec(sec: int) -> float:
        if total_secs <= 0:
            return float(d["watch"].iloc[0])
        r = max(0.0, min(1.0, sec / float(total_secs)))
        idx = (d["t_ratio"] - r).abs().idxmin()
        return float(d.loc[idx, "watch"])
    # Core checkpoints (simple + explainable)
    w0 = float(d["watch"].iloc[0])
    w10 = at_sec(10)
    w30 = at_sec(30)
    w60 = at_sec(60)
    w_mid = float(d[d["t_ratio"].between(0.45, 0.55, inclusive="both")]["watch"].mean()) if (d["t_ratio"].between(0.45,0.55).any()) else float(d["watch"].mean())
    # Drop rates
    drop_0_10 = max(0.0, w0 - w10)
    drop_0_30 = max(0.0, w0 - w30)
    drop_0_60 = max(0.0, w0 - w60)
    # Relative performance: >1 means better than YouTube's baseline for similar videos (roughly)
    rel = None
    if "relativeRetentionPerformance" in d.columns:
        rel = float(pd.to_numeric(d["relativeRetentionPerformance"], errors="coerce").dropna().mean() or 0.0)
    # Simple scoring (tunable). Goal: decision-first, not academic.
    score = 100
    # Early drop is the biggest revenue lever for agencies (hook + promise clarity)
    score -= min(55, drop_0_30 * 100 * 1.1)
    score -= min(20, drop_0_60 * 100 * 0.5)
    # Flatlining mid-video is second-order (pacing/structure)
    if w_mid < 0.18:
        score -= 10
    if rel is not None and rel < 0.9:
        score -= 8
    score = int(max(0, min(100, round(score))))
    metrics = {
        "Start→10s drop": f"{drop_0_10*100:.1f}%",
        "Start→30s drop": f"{drop_0_30*100:.1f}%",
        "Start→60s drop": f"{drop_0_60*100:.1f}%",
        "Mid-video watch ratio": f"{w_mid*100:.1f}%",
    }
    if rel is not None:
        metrics["Relative retention (avg)"] = f"{rel:.2f}"
    issues = []
    def add_issue(rank_hint: int, title: str, why: str, what_to_do: str, time_hint: str = "", impact: str = "High"):
        issues.append({
            "rank": rank_hint,
            "impact": impact,
            "issue": title,
            "where": time_hint,
            "why": why,
            "next_step": what_to_do,
        })
    # Ranked issues (simple language, actionable)
    if drop_0_30 >= 0.25:
        add_issue(
            1,
            "Hook isn’t landing fast enough",
            "A big chunk of viewers leave in the first 30 seconds, which kills reach and suggested traffic.",
            "Open with the outcome first (what they’ll get), cut any intro/filler, and show proof within 5–10s (result, screenshot, before/after).",
            "0–30s",
            "Very High",
        )
    elif drop_0_30 >= 0.15:
        add_issue(
            2,
            "Intro is a bit long / unclear",
            "Viewers are dropping early. Usually the promise isn’t clear or the video takes too long to start.",
            "Use a 3-part hook: (1) outcome, (2) why now, (3) what to expect. Aim to hit the first payoff by 15s.",
            "0–30s",
            "High",
        )
    # Detect biggest drop moments (existing helper)
    drops = top_drop_insights(d, total_secs, k=5)
    if drops:
        # Turn first 2 into structured issues
        for i, tip in enumerate(drops[:2], start=1):
            add_issue(
                3 + i,
                "Specific drop-off moment to fix",
                "A clear drop suggests a confusing sentence, slow section, or mismatch vs the title/thumbnail promise.",
                f"Rewatch that moment and remove anything that doesn’t move the story forward. Add a pattern-break (graphic, jump cut, new example) right before the drop.\n\nEvidence: {tip}",
                "",
                "Medium",
            )
    if w_mid < 0.16:
        add_issue(
            6,
            "Pacing is dragging mid-video",
            "When the middle of the video is too slow, viewers stop watching and the algorithm stops pushing it.",
            "Use tighter structure: short sections, frequent resets (“here’s the next step”), and add one new example every 30–45 seconds.",
            "Mid-video",
            "Medium",
        )
    wins = []
    if drop_0_30 < 0.12:
        wins.append("Strong opening: early drop is relatively low (good hook).")
    if w_mid >= 0.22:
        wins.append("Decent mid-video hold: pacing/structure is working.")
    if rel is not None and rel >= 1.0:
        wins.append("Relative retention looks above baseline for similar videos.")
    # If we generated nothing, give one clear default action
    if not issues:
        add_issue(
            1,
            "No major red flags detected",
            "The curve doesn’t show a single obvious failure point, so improvements are about small, repeatable gains.",
            "Run 3 tests next upload: shorten intro by 10s, add a proof clip in first 5s, and add 1 extra pattern-break at ~45s.",
            "",
            "Low",
        )
    # Sort by rank
    issues = sorted(issues, key=lambda x: x["rank"])
    return {"score": score, "metrics": metrics, "issues": issues, "wins": wins}
def _plot_retention(ax, df: pd.DataFrame, total_secs: int, focus_secs: int | None = None):
    d = df.copy()
    d["t_ratio"] = d["elapsedVideoTimeRatio"].astype(float).clip(0, 1)
    d["watch"] = d["audienceWatchRatio"].astype(float).clip(lower=0)
    d = d.sort_values("t_ratio")
    if focus_secs and total_secs > 0:
        r = max(0.0, min(1.0, focus_secs / float(total_secs)))
        d = d[d["t_ratio"] <= r]
    ax.plot(d["t_ratio"], d["watch"], linewidth=2)
    ax.set_title("Retention curve" + (f" (first {focus_secs}s)" if focus_secs else ""))
    ax.set_xlabel("Video progress (0–1)")
    ax.set_ylabel("Audience watch ratio")
    ax.grid(alpha=0.2)
# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------
st.markdown(
    f"""
    <div class="yt-hero">
      <div class="yt-hero-pill">
        <span>AI-driven audit suite</span>
        <span>Built for serious creators & agencies</span>
      </div>
      <h1>{brand_name}</h1>
      <p class="yt-hero-sub">
        Deep channel diagnostics, elite PDF reporting and growth tools in a single, streamlined command center.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)
tabs = st.tabs(["🔍 Growth Audit", "⚔ Competitors", "📈 Retention + Transcript", "🖼 Thumbnails", "💡 Video Ideas"])
# ================================================================
# Audit tab (UNCHANGED logic, demo cap kept)
# ================================================================
with tabs[0]:
    st.markdown('<div class="yt-section-card">', unsafe_allow_html=True)
    st.subheader("Growth Audit")
    render_guidance(
    "Growth Audit",
    what=[
        "Diagnoses what’s limiting views (titles, velocity, engagement, SEO)",
        "Flags underperforming videos and why they’re underperforming",
        "Gives specific fixes you can apply on the next upload",
    ],
    steps=[
        "Paste channel URL / @handle",
        "Choose how many recent videos to analyse",
        "Run audit and apply the ‘Specific improvements’ section first",
    ],
    output=[
        "KPIs + red flags",
        "Specific improvements per video",
        "Elite PDF export (licensed)",
    ],
    )
    if demo_mode():
        remaining = 3 - int(st.session_state.get("demo_audits_used", 0))
        st.caption(f"Demo limit: {max(0, remaining)}/3 audits remaining.")
        if remaining <= 0:
            st.error("Demo audit limit reached (3/3). Enter a license key to unlock unlimited audits + all tabs.")
            st.markdown("</div>", unsafe_allow_html=True)
            st.stop()
    channel_input = st.text_input("Channel URL / @handle / Channel ID", value="https://www.youtube.com/@ImanGadzhi")
    recent_n = st.slider("Recent videos to audit", 3, 30, 5)
    if st.button("Run Growth Audit"):
        if demo_mode():
            st.session_state["demo_audits_used"] = int(st.session_state.get("demo_audits_used", 0)) + 1
        yt = yt_key_service()
        ch_id = resolve_channel_id(yt, channel_input) or parse_channel_or_id(channel_input)
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
        med_likes = int(df["likes"].median()) if df["likes"].notna().sum() and vids else 0
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
        kpis["SEO_SUMMARY_LINES"] = [f"{k}: {v} videos" for k, v in seo_summary_counts.items()]
        st.session_state["kpis"] = kpis
        st.session_state["insights"] = insights
        st.session_state["top_keywords"] = kd.index.tolist()
    ch = st.session_state.get("channel_info")
    vv = st.session_state.get("videos")
    if ch and vv:
        if demo_mode():
            st.caption("Elite PDF is locked in demo. Enter a license key to unlock export.")
        else:
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
    st.markdown("</div>", unsafe_allow_html=True)
# ================================================================
# Competition tab (LOCKED in demo) — NO st.stop()
# ================================================================
with tabs[1]:
    st.markdown('<div class="yt-section-card">', unsafe_allow_html=True)
    if demo_mode():
        show_locked(
            "Competition",
            [
                "Competitor velocity benchmarking (views/min)",
                "Topic + keyword overlap",
                "Upload cadence comparison",
                "Winning title & format patterns",
                "Client-ready competitor reports",
            ],
            "Upgrade to unlock Competition.",
        )
    else:
        st.subheader("Competitor Comparison")
        render_guidance(
    "Competitor Comparison",
    what=[
        "Benchmarks you vs competitors using views/min and engagement",
        "Highlights winning title + format patterns in your niche",
        "Helps you pick topics YouTube is already rewarding",
    ],
    steps=[
        "Enter your channel + up to 2 competitors",
        "Pick recent videos per channel",
        "Run comparison and copy patterns from top performers",
    ],
    output=[
        "Side-by-side performance table",
        "Velocity and engagement benchmarks",
    ],
        )
        base_input = st.text_input("Your Channel URL/ID", value="https://www.youtube.com/@ImanGadzhi", key="comp_you")
        compA_input = st.text_input("Competitor A URL/ID", value="https://www.youtube.com/@AlexHormozi", key="comp_a")
        compB_input = st.text_input("Competitor B URL/ID", value="", key="comp_b")
        comp_n = st.slider("Recent videos per channel", 3, 40, 5, key="comp_n")
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
            else:
                comp_df = pd.concat(rows, ignore_index=True)
                st.dataframe(
                    comp_df[["who", "title", "views", "likes", "comments", "published", "views_per_min", "like_rate_%"]],
                    use_container_width=True,
                    height=340,
                )
    st.markdown("</div>", unsafe_allow_html=True)
# ================================================================
# Audience Retention tab (LOCKED in demo) — NO st.stop() at top-level
# ================================================================
with tabs[2]:
    st.markdown('<div class="yt-section-card">', unsafe_allow_html=True)
    if demo_mode():
        show_locked(
            "Audience Retention",
            [
                "Drop-off timestamps (seconds, not %)",
                "Hook strength analysis (first 30s)",
                "Pattern-break recommendations",
                "Client-ready summary + checklist",
                "Ownership-verified analytics",
            ],
            "Upgrade to unlock Audience Retention.",
        )
    else:
        st.subheader("Retention Analysis (with transcript)")
        render_guidance(
    "Retention Analysis",
    what=[
        "Finds the exact moments viewers leave (seconds, not vague %)",
        "Scores the hook and identifies the biggest retention bottleneck",
        "Shows transcript around drop points for precise rewrites (best-effort)",
    ],
    steps=[
        "Paste a video URL/ID owned by the signed-in channel",
        "Pick focus window (first 60s is best for hook work)",
        "Analyze and apply the top ‘Exactly what to do next’ actions",
    ],
    output=[
        "Top issues + wins",
        "Client-ready summary (copy/paste)",
        "Transcript snippets at drop points (if available)",
    ],
        )
        # Keep inputs stable + reduce accidental reruns
        with st.form("retention_form", clear_on_submit=False):
            vid_in = st.text_input(
                "Video URL or ID",
                value="",
                help="Must be a video on the authenticated channel",
            )
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                focus_window = st.selectbox("Focus window", ["First 60s", "First 120s", "Full video"], index=0)
            with c2:
                show_charts = st.checkbox("Show charts", value=True)
            with c3:
                show_raw = st.checkbox("Show raw data table", value=False)
            run = st.form_submit_button("Analyze Retention")
        if run:
            vid = extract_video_id(vid_in)
            if not vid:
                st.error("Enter a valid video URL or ID.")
            else:
                # Cache OAuth clients in-session to avoid re-auth overhead per rerun
                try:
                    if "yta_clients" not in st.session_state:
                        st.session_state["yta_clients"] = {}
                        st.session_state["yta_clients"]["yt_key"] = yt_key_service()
                        yt_oauth, session = yt_oauth_clients()
                        st.session_state["yta_clients"]["yt_oauth"] = yt_oauth
                        st.session_state["yta_clients"]["session"] = session
                    yt_key = st.session_state["yta_clients"]["yt_key"]
                    yt_oauth = st.session_state["yta_clients"]["yt_oauth"]
                    session = st.session_state["yta_clients"]["session"]
                except Exception as e:
                    st.error(f"Auth initialisation failed: {e}")
                    st.markdown("</div>", unsafe_allow_html=True)
                    st.stop()
                # Basic meta + ownership check (kept)
                try:
                    vmeta = yt_key.videos().list(part="snippet,contentDetails", id=vid).execute()
                    items = vmeta.get("items", [])
                    if not items:
                        st.error("Video not found.")
                        st.markdown("</div>", unsafe_allow_html=True)
                        st.stop()
                    owner_ch = items[0]["snippet"]["channelId"]
                    mine = yt_oauth.channels().list(part="id", mine=True).execute()
                    my_items = mine.get("items") or []
                    my_ch = my_items[0].get("id") if my_items else None
                    if not my_ch:
                        st.error("Authenticated Google account has no YouTube channel.")
                        st.markdown("</div>", unsafe_allow_html=True)
                        st.stop()
                    if owner_ch != my_ch:
                        st.error("Google requires the video to be on the authenticated channel.\n\nLog in with the YouTube channel that owns this video, then retry.")
                        st.markdown("</div>", unsafe_allow_html=True)
                        st.stop()
                except Exception as e:
                    st.error(f"Ownership check failed: {e}")
                    st.markdown("</div>", unsafe_allow_html=True)
                    st.stop()
                # Pull retention once; cache by video id for speed in the same session
                try:
                    if "retention_cache" not in st.session_state:
                        st.session_state["retention_cache"] = {}
                    cache_key = f"{vid}"
                    if cache_key in st.session_state["retention_cache"]:
                        resp = st.session_state["retention_cache"][cache_key]
                    else:
                        # Narrow the date range to reduce backend work (agencies care about NOW)
                        start_date = (datetime.utcnow().date() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
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
                        with st.spinner("Pulling retention data from YouTube Analytics..."):
                            resp = yta_reports(session, params)
                        st.session_state["retention_cache"][cache_key] = resp
                except Exception as e:
                    st.error(f"Retention analysis failed: {e}")
                    st.markdown("</div>", unsafe_allow_html=True)
                    st.stop()
                cols = [h["name"] for h in resp.get("columnHeaders", [])]
                rows = resp.get("rows", [])
                df = pd.DataFrame(rows, columns=cols)
                if df.empty:
                    st.warning("No retention data available for this video (try a different video).")
                    st.markdown("</div>", unsafe_allow_html=True)
                    st.stop()
                # Normalize + small downsample for faster plotting + clearer reading
                df["elapsedVideoTimeRatio"] = pd.to_numeric(df["elapsedVideoTimeRatio"], errors="coerce")
                df["audienceWatchRatio"] = pd.to_numeric(df["audienceWatchRatio"], errors="coerce")
                df = df.dropna(subset=["elapsedVideoTimeRatio", "audienceWatchRatio"]).sort_values("elapsedVideoTimeRatio")
                if len(df) > 220:
                    # Keep shape but reduce points
                    df = df.iloc[:: int(max(1, len(df) / 200))].copy()
                duration_iso = items[0]["contentDetails"].get("duration", "")
                total_secs = parse_yt_duration_iso8601(duration_iso)
                card = _retention_scorecard(df, total_secs)
                # ===== Decision-first output (what agencies can sell) =====
                st.markdown("### Retention verdict")
                cA, cB, cC = st.columns([1, 2, 2])
                with cA:
                    st.metric("Score (0–100)", card["score"])
                with cB:
                    for k, v in card["metrics"].items():
                        st.write(f"**{k}:** {v}")
                with cC:
                    if card["wins"]:
                        st.write("**What’s working**")
                        for w in card["wins"][:3]:
                            st.write(f"- {w}")
                st.markdown("### Ranked fixes (highest impact first)")
                # Render as readable on-page text blocks (no cut-off table)
                for i, item in enumerate(card["issues"][:8], start=1):
                    impact = item.get("impact", "")
                    issue = item.get("issue", "")
                    where = item.get("where", "")
                    why = item.get("why", "")
                    next_step = item.get("next_step", "")
                    st.markdown(
                        f"""
**{i}. {issue}**  
**Impact:** {impact}  
**Where:** {where}
**What’s going wrong**  
{why}
**Exactly what to do next**  
{next_step}
---
"""
                    )
                st.markdown("### Client-ready summary (copy/paste)")
                top_issue = card["issues"][0]["issue"] if card["issues"] else "Early hook needs tightening"
                top_action = card["issues"][0]["next_step"] if card["issues"] else "Tighten the first 30 seconds and show the payoff sooner."
                summary = f"""
**Goal**  
Increase watch time and suggested traffic.
**Biggest bottleneck**  
{top_issue}
**Why this matters**  
If people leave early, YouTube stops pushing the video to new viewers — which caps reach and views.
**What we’ll do first**  
{top_action}
**What to expect**  
Higher average view duration → more impressions → more views over the next uploads.
**What we’ll check next**  
We’ll re-run retention on the next videos to confirm the fix is working and adjust if needed.
"""
                st.markdown(summary)
    st.markdown("</div>", unsafe_allow_html=True)
# ================================================================
# Thumbnail Ideas tab (LOCKED in demo) — NO st.stop()
# ================================================================
with tabs[3]:
    st.markdown('<div class="yt-section-card">', unsafe_allow_html=True)
    if demo_mode():
        show_locked(
            "Thumbnail Ideas",
            [
                "High-converting thumbnail concepts",
                "On-screen text ideas (2–4 words)",
                "Layout templates + contrast rules",
                "Variant packs for A/B testing",
                "Niche-specific styling guidance",
            ],
            "Upgrade to unlock Thumbnail Ideas.",
        )
    else:
        st.subheader("Thumbnail Concepts")
        render_guidance(
    "Thumbnail Concepts",
    what=[
        "Generates high-CTR thumbnail directions you can brief a designer with",
        "Gives layout + contrast rules so thumbnails ‘pop’ on mobile",
    ],
    steps=[
        "Enter a title hint (or leave blank)",
        "Generate ideas and pick 2–3 variants",
        "A/B test by rotating thumbnails after 24–48 hours",
    ],
    output=[
        "10 ready-to-use thumbnail briefs",
    ],
        )
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
    st.markdown("</div>", unsafe_allow_html=True)
# ================================================================
# Video Ideas tab (LOCKED in demo) — NO st.stop()
# ================================================================
with tabs[4]:
    st.markdown('<div class="yt-section-card">', unsafe_allow_html=True)
    if demo_mode():
        show_locked(
            "Video Ideas",
            [
                "Keyword-mined ideas based on channel uploads",
                "Higher CTR title templates + angles",
                "Idea expansion + series planning",
                "Retention-first hooks (first 10s prompts)",
                "Client-ready idea exports",
            ],
            "Upgrade to unlock Video Ideas.",
        )
    else:
        st.subheader("Video Ideas (niche intelligence)")
        render_guidance(
    "Video Ideas",
    what=[
        "Finds angles that match what’s already winning in the niche",
        "Produces titles + hooks designed for CTR + retention",
    ],
    steps=[
        "Enter channel URL/ID",
        "Generate ideas and shortlist the top angles",
        "Use the hook prompts to script the first 10 seconds",
    ],
    output=[
        "Idea list + title templates",
        "Retention-first hook prompts",
    ],
        )
        # -------------------------------
        # Video Ideas: defensive schema
        # -------------------------------
        _VI_REQUIRED = {
            "video_id": "",
            "title": "",
            "published": pd.NaT,
            "seed": "",
            "views": 0,
            "channel_title": "",
        }
        def _vi_normalize_df(df: pd.DataFrame) -> pd.DataFrame:
            """Normalize schema for Video Ideas pipeline. Never throws."""
            if df is None or not isinstance(df, pd.DataFrame):
                df = pd.DataFrame()
            df = df.copy()
            # Common alias lifts
            alias_map = {
                "video_id": ["videoId", "id", "vid", "videoID"],
                "title": ["video_title", "videoTitle", "name"],
                "published": ["publishedAt", "published_at", "publish_time", "date"],
                "seed": ["seed_query", "seed_term", "seed_keyword"],
                "views": ["viewCount", "view_count"],
                "channel_title": ["channelTitle", "channel", "channel_name"],
            }
            for canonical, aliases in alias_map.items():
                if canonical in df.columns:
                    continue
                for a in aliases:
                    if a in df.columns:
                        df[canonical] = df[a]
                        break
            # Merge suffix repair (pandas adds _x/_y)
            if "title" not in df.columns:
                for a in ("title_x", "title_y"):
                    if a in df.columns:
                        df["title"] = df[a]
                        break
            if "published" not in df.columns:
                for a in ("published_x", "published_y", "publishedAt"):
                    if a in df.columns:
                        df["published"] = df[a]
                        break
            if "channel_title" not in df.columns:
                for a in ("channel_title_x", "channel_title_y", "channelTitle"):
                    if a in df.columns:
                        df["channel_title"] = df[a]
                        break
            # Ensure required columns exist with safe defaults
            for col, default in _VI_REQUIRED.items():
                if col not in df.columns:
                    df[col] = default
            # Type normalisation
            df["video_id"] = df["video_id"].fillna("").astype(str)
            df["title"] = df["title"].fillna("").astype(str)
            # published can be str or datetime
            df["published"] = pd.to_datetime(df["published"], utc=True, errors="coerce")
            df["seed"] = df["seed"].fillna("").astype(str)
            # views numeric
            df["views"] = pd.to_numeric(df["views"], errors="coerce").fillna(0).astype(int)
            df["channel_title"] = df["channel_title"].fillna("").astype(str)
            return df
        colA, colB, colC = st.columns([2.2, 1.0, 1.0])
        with colA:
            chan_in = st.text_input("Channel (URL, @handle, or ID)", value="https://www.youtube.com/@ImanGadzhi", key="vi_chan")
        with colB:
            region_label = st.selectbox("Region", ["UK", "US", "Global"], index=0, key="vi_region")
        with colC:
            days = st.selectbox("Freshness window", [7, 30, 90], index=1, key="vi_days")
        comp_in = st.text_input("Competitor (optional) — URL/ID", value="", key="vi_comp")
        how_many = st.slider("Ideas", 6, 30, 12, key="vi_n")
        include_kw = st.text_input("Include keywords/topics (comma separated, optional)", value="", key="vi_kw")
        output_mode = st.radio("Output", ["Titles only", "Briefs (expand for details)"], index=0, horizontal=True, key="vi_out")
        st.markdown("### How to use this")
        st.markdown(
            f"- Pulls ideas from what’s performing **right now** in your niche (last **{days} days**).\n"
            f"- Starts with the highest **Niche Match** ideas first (not random viral).\n"
            f"- Keep it simple: pick 3 titles, film this week, then repeat what works."
        )
        STOP = {
            "a","an","the","and","or","but","if","then","this","that","these","those","to","of","in","on","for","with","at","by","from",
            "is","are","was","were","be","been","being","it","its","as","into","over","under","than","too","very","you","your","we","our",
            "i","me","my","they","their","them","he","she","his","her","what","why","how","when","where","who","which","do","does","did",
            "make","made","making","get","got","getting","day","days","week","weeks","year","years","now","new","best","top","vs","v",
        }
        def _tok(s: str) -> list[str]:
            s = (s or "").lower()
            s = re.sub(r"[^a-z0-9\s']", " ", s)
            parts = [p.strip("'") for p in s.split() if p.strip("'")]
            return [p for p in parts if p not in STOP and len(p) > 2 and not p.isdigit()]
        def _ngrams(tokens: list[str], n: int) -> list[str]:
            return [" ".join(tokens[i:i+n]) for i in range(0, max(0, len(tokens)-n+1))]
        def _views_per_day(df: pd.DataFrame) -> pd.DataFrame:
            d = df.copy()
            d["published_dt"] = pd.to_datetime(d["published"], errors="coerce", utc=True)
            age_days = (pd.Timestamp.utcnow() - d["published_dt"]).dt.total_seconds() / 86400.0
            age_days = age_days.clip(lower=1.0)
            d["views_per_day"] = (pd.to_numeric(d.get("views", 0), errors="coerce").fillna(0) / age_days)
            return d
        def _parse_iso8601_duration(dur: str) -> int:
            # PT#H#M#S
            if not dur or not dur.startswith("PT"):
                return 0
            h = m = s = 0
            m1 = re.search(r"(\d+)H", dur)
            m2 = re.search(r"(\d+)M", dur)
            m3 = re.search(r"(\d+)S", dur)
            if m1: h = int(m1.group(1))
            if m2: m = int(m2.group(1))
            if m3: s = int(m3.group(1))
            return h*3600 + m*60 + s
        @st.cache_data(show_spinner=False, ttl=60*60)
        def _fetch_durations(_yt, video_ids: list[str]) -> dict:
            out = {}
            # chunk 50
            for i in range(0, len(video_ids), 50):
                chunk = video_ids[i:i+50]
                r = _yt.videos().list(part="contentDetails", id=",".join(chunk), maxResults=50).execute()
                for it in r.get("items", []):
                    vid = it.get("id")
                    dur = it.get("contentDetails", {}).get("duration", "")
                    out[vid] = _parse_iso8601_duration(dur)
            return out
        def _fingerprint_terms(title_series: list[str], weight_series: list[float]) -> tuple[dict, set, set]:
            # weighted n-grams (2-3)
            w = {}
            unigrams = set()
            bigrams = set()
            for t, wt in zip(title_series, weight_series):
                toks = _tok(t)
                for u in toks:
                    unigrams.add(u)
                    w[u] = w.get(u, 0.0) + wt
                for bg in _ngrams(toks, 2):
                    if len(bg) >= 8:
                        bigrams.add(bg)
                        w[bg] = w.get(bg, 0.0) + wt * 1.8
                for tg in _ngrams(toks, 3):
                    if len(tg) >= 12:
                        w[tg] = w.get(tg, 0.0) + wt * 2.2
            # top terms
            top = dict(sorted(w.items(), key=lambda x: x[1], reverse=True)[:120])
            return top, unigrams, bigrams
        def _pick_queries(term_weights: dict) -> list[str]:
            # Prefer multi-word phrases; avoid overly generic single terms
            phrases = [k for k in term_weights.keys() if " " in k and 8 <= len(k) <= 32]
            # keep top 10 phrases
            return phrases[:10] if phrases else list(term_weights.keys())[:8]
        def _is_spam_title(t: str) -> bool:
            tl = (t or "").lower()
            if "#" in tl:
                return True
            bad = ["shorts", "tiktok", "trending", "viral", "official video", "mv", "music video", "trailer", "full movie", "song", "lyrics"]
            if any(b in tl for b in bad):
                return True
            # too many non-ascii / emojis
            non_ascii = sum(1 for ch in (t or "") if ord(ch) > 127)
            if non_ascii > 6:
                return True
            return False
        def _niche_match_score(title: str, uni: set, bi: set, top_terms: dict) -> float:
            toks = _tok(title)
            if not toks:
                return 0.0
            uni_hits = sum(1 for x in toks if x in uni)
            bi_hits = 0
            bgs = _ngrams(toks, 2)
            for bg in bgs:
                if bg in bi:
                    bi_hits += 1
            # weighted sum with term weights
            wsum = 0.0
            for x in toks:
                wsum += top_terms.get(x, 0.0)
            for bg in bgs:
                wsum += top_terms.get(bg, 0.0)
            # normalize
            return (uni_hits * 1.0 + bi_hits * 2.2 + (wsum / (max(1.0, sum(top_terms.values())/50.0)))) / (len(toks) + 2.0)
        @st.cache_data(show_spinner=False, ttl=60*60)
        def _search_trends(_yt, query: str, days: int, region_code: str | None, max_results: int = 18) -> list[dict]:
            after = (pd.Timestamp.utcnow() - pd.Timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            kwargs = dict(part="snippet", q=query, type="video", order="viewCount", maxResults=max_results, publishedAfter=after)
            if region_code:
                kwargs["regionCode"] = region_code
            r = _yt.search().list(**kwargs).execute()
            out = []
            for it in r.get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                sn = it.get("snippet") or {}
                if not vid:
                    continue
                out.append({
                    "video_id": vid,
                    "title": sn.get("title", ""),
                    "published": sn.get("publishedAt", ""),
                    "channel_title": sn.get("channelTitle", ""),
                    "seed": query,
                })
            return out
        def _format_type(t: str) -> str:
            tl = (t or "").lower()
            if "how to" in tl or tl.startswith("how "):
                return "How-to"
            if "vs" in tl or " vs " in tl:
                return "Versus"
            if "mistake" in tl or "mistakes" in tl:
                return "Mistakes"
            if "truth" in tl or "nobody tells" in tl or "what they don't" in tl:
                return "Truth"
            if any(x in tl for x in ["framework", "system", "rules", "steps", "habits"]):
                return "Framework"
            if any(x in tl for x in ["i tried", "for 30 days", "i tested", "challenge"]):
                return "Experiment"
            return "Strategy"
        def _title_variants(seed_phrase: str, fmt: str) -> tuple[str, str, str]:
            seed = seed_phrase.title()
            # tighten seed if too long
            if len(seed) > 38:
                seed = seed[:35].rstrip() + "..."
            if fmt == "How-to":
                p1 = f"How to Win With {seed} (Step-by-Step)"
                p2 = f"{seed}: The Simple System That Works"
                p3 = f"Do This Before You Try {seed}"
            elif fmt == "Mistakes":
                p1 = f"3 Mistakes Killing Your {seed}"
                p2 = f"Stop Doing {seed} Like This"
                p3 = f"The {seed} Trap Nobody Talks About"
            elif fmt == "Truth":
                p1 = f"The Truth About {seed} in 2026"
                p2 = f"{seed} Isn’t Working (Here’s Why)"
                p3 = f"What Nobody Tells You About {seed}"
            elif fmt == "Framework":
                p1 = f"The {seed} Framework That Actually Works"
                p2 = f"{seed}: 7 Rules to Follow"
                p3 = f"My Exact {seed} Checklist"
            elif fmt == "Experiment":
                p1 = f"I Tried {seed} for 30 Days (Results)"
                p2 = f"What Happened When I Focused on {seed}"
                p3 = f"{seed} for 30 Days: My System"
            else:
                p1 = f"{seed}: What Works Right Now"
                p2 = f"Do This to Improve Your {seed}"
                p3 = f"The Fastest Way to Get Results With {seed}"
            return p1, p2, p3
        def _clean_title_display(s: str) -> str:
            s = (s or "").strip()
            s = re.sub(r"#\w+", "", s)
            s = re.sub(r"[^\w\s£$€:,'’\-–—\.\?\!\(\)]", "", s)
            s = re.sub(r"\s{2,}", " ", s).strip()
            return s if len(s) <= 80 else (s[:77].rstrip() + "...")
        if st.button("Generate briefs", key="vi_go"):
            yt = yt_key_service()
            cid = resolve_channel_id(yt, chan_in) or parse_channel_or_id(chan_in)
            if not cid:
                st.error("Could not resolve channel")
            else:
                # Region mapping
                region_code = {"UK": "GB", "US": "US", "Global": None}.get(region_label, None)
                # Base corpus
                base = fetch_recent_videos(yt, cid, 70)
                base_stats = fetch_video_stats(yt, base["video_id"].tolist())
                base = base.merge(base_stats, on="video_id", how="left")
                base = _views_per_day(base)
                # Optional competitor enrich
                if comp_in.strip():
                    ccid = resolve_channel_id(yt, comp_in) or parse_channel_or_id(comp_in)
                    if ccid:
                        comp = fetch_recent_videos(yt, ccid, 50)
                        comp_stats = fetch_video_stats(yt, comp["video_id"].tolist())
                        comp = comp.merge(comp_stats, on="video_id", how="left")
                        comp = _views_per_day(comp)
                        base = pd.concat([base, comp], ignore_index=True)
                # Ensure we have a usable title column after merges (avoid KeyError if pandas adds suffixes)
                if "title" not in base.columns:
                    for _c in ("title_x", "title_y", "video_title", "snippet_title", "name"):
                        if _c in base.columns:
                            base["title"] = base[_c]
                            break
                    else:
                        base["title"] = ""
                titles = base["title"].fillna("").astype(str).tolist()
                weights = base["views_per_day"].fillna(0).astype(float).tolist()
                top_terms, uni, bi = _fingerprint_terms(titles, weights)
                queries = _pick_queries(top_terms)
                # Pull trend candidates
                cand = []
                for q in queries[:8]:
                    cand.extend(_search_trends(yt, q, int(days), region_code, max_results=18))
                cand_df = pd.DataFrame(cand)
                # Defensive schema: if search returns nothing, stop early
                if cand_df.empty:
                    st.warning("No trend candidates found. Try a shorter freshness window or broaden region.")
                else:
                    # Ensure required columns exist (avoids KeyError cascades)
                    if "video_id" not in cand_df.columns:
                        for _c in ("videoId", "id", "vid"):
                            if _c in cand_df.columns:
                                cand_df["video_id"] = cand_df[_c]
                                break
                    if "video_id" not in cand_df.columns:
                        st.warning("Could not read video IDs from trend search results.")
                    else:
                        cand_df = cand_df.drop_duplicates(subset=["video_id"])
                        # Ensure title/published/seed fields exist even if upstream schema changes
                        if "title" not in cand_df.columns:
                            for _c in ("title_x","title_y","video_title","snippet_title","name"):
                                if _c in cand_df.columns:
                                    cand_df["title"] = cand_df[_c]
                                    break
                            else:
                                cand_df["title"] = ""
                        if "published" not in cand_df.columns:
                            if "publishedAt" in cand_df.columns:
                                cand_df["published"] = cand_df["publishedAt"]
                            else:
                                cand_df["published"] = ""
                        if "seed" not in cand_df.columns:
                            if "query" in cand_df.columns:
                                cand_df["seed"] = cand_df["query"]
                            else:
                                cand_df["seed"] = ""
                    stats = fetch_video_stats(yt, cand_df["video_id"].tolist())
                    cand_df = cand_df.merge(stats, on="video_id", how="left")
                    durs = _fetch_durations(yt, cand_df["video_id"].tolist())
                    cand_df["duration_s"] = cand_df["video_id"].map(lambda x: durs.get(x, 0))
                    cand_df = _views_per_day(cand_df)
                    # Hard filters
                    cand_df = _vi_normalize_df(cand_df)
                    cand_df = cand_df[ cand_df["duration_s"] >= 360 ]
                    cand_df = cand_df[ ~cand_df["title"].fillna("").apply(_is_spam_title) ]
                    if cand_df.empty:
                        st.warning("Trends were found, but nothing matched your niche after filters. Try 90 days or add a competitor.")
                    else:
                        # Niche match + demand score
                        cand_df["niche_match"] = cand_df["title"].apply(lambda t: _niche_match_score(str(t), uni, bi, top_terms))
                        # normalize demand
                        vmax = float(cand_df["views_per_day"].max() or 1.0)
                        cand_df["demand"] = cand_df["views_per_day"].astype(float) / vmax
                        cand_df["score"] = (cand_df["niche_match"] * 0.65 + cand_df["demand"] * 0.35) * 100.0
                        # Keep only high niche match
                        cand_df = cand_df.sort_values("score", ascending=False)
                        cand_df = cand_df[cand_df["niche_match"] >= max(0.12, float(cand_df["niche_match"].quantile(0.65)))]
                        cand_df = cand_df.head(max(30, how_many*3))
                        if cand_df.empty:
                            st.warning("Nothing strongly matched this channel’s niche. Try adding a competitor to tighten the niche profile.")
                        else:
                            # Build ideas (agency-grade): keyword control + diversity + guaranteed count
                            def _kw_list(raw: str) -> list[str]:
                                raw = (raw or "").strip()
                                if not raw:
                                    return []
                                parts = [p.strip() for p in raw.split(",")]
                                out = []
                                for p in parts:
                                    p2 = re.sub(r"[^A-Za-z0-9\s\-']", "", p).strip()
                                    if len(p2) >= 2:
                                        out.append(p2)
                                # de-dup preserve order
                                seen=set(); uniq=[]
                                for x in out:
                                    k=x.lower()
                                    if k in seen: continue
                                    seen.add(k); uniq.append(x)
                                return uniq
                            def _prefix3(t: str) -> str:
                                w = re.findall(r"[A-Za-z0-9']+", (t or "").lower())
                                return " ".join(w[:3])
                            def _family(t: str) -> str:
                                tl = (t or "").lower()
                                if tl.startswith("how ") or " how " in tl:
                                    return "how"
                                if tl.startswith("why "):
                                    return "why"
                                if "mistake" in tl:
                                    return "mistakes"
                                if "30 day" in tl or "30-day" in tl or "i tried" in tl or "i tested" in tl:
                                    return "experiment"
                                if "framework" in tl or "system" in tl or "rules" in tl or "checklist" in tl:
                                    return "framework"
                                if "truth" in tl or "nobody tells" in tl:
                                    return "truth"
                                return "other"
                            def _strict_evidence(df: pd.DataFrame) -> pd.DataFrame:
                                d = df.copy()
                                # pattern mining should only learn from high-confidence niche matches
                                d = d[d["niche_match"].fillna(0).astype(float) >= 0.75]
                                return d
                            def _detect_style(channel_input: str, df: pd.DataFrame) -> str:
                                """Lightweight style detector used ONLY inside Video Ideas.
                                Returns one of: 'mrbeast', 'generic'. Keeps logic simple and defensive.
                                """
                                t = (channel_input or "").lower()
                                if "mrbeast" in t or "@mrbeast" in t:
                                    return "mrbeast"
                                try:
                                    if df is not None and not df.empty and "channel_title" in df.columns:
                                        ct = df["channel_title"].fillna("").astype(str).str.lower()
                                        if len(ct) > 0:
                                            frac = float((ct.str.contains("mrbeast")).mean())
                                            if frac >= 0.35:
                                                return "mrbeast"
                                except Exception:
                                    pass
                                return "generic"
                            def _extract_frames(titles: list[str], style: str) -> list[str]:
                                """Mine lightweight title frames from evidence, returning format strings.
                                Frames must contain '{TOPIC}' and may contain '{MECH}'.
                                """
                                style = (style or "generic").strip().lower()
                                out: list[str] = []
                                if style == "mrbeast":
                                    beast = [
                                        "{{N}} People Compete To Win ${{M}}",
                                        "Last To Leave {{TOPIC}} Wins ${{M}}",
                                        "I Gave {{N}} People ${{M}} If They {{TOPIC}}",
                                        "I Survived {{N}} Days In {{TOPIC}}",
                                        "I Built {{TOPIC}} And Gave It Away",
                                        "I Spent ${{M}} To {{TOPIC}}",
                                        "I Trapped {{N}} People In {{TOPIC}}",
                                    ]
                                    mapped2 = []
                                    for fr in beast:
                                        fr = fr.replace("{{N}}", "10").replace("{{M}}", "100,000")
                                        mapped2.append(fr.replace("{{TOPIC}}", "{TOPIC}"))
                                    return mapped2
                                if not titles:
                                    return out
                                def _norm(s: str) -> str:
                                    s = (s or "").strip()
                                    s = re.sub(r"\s+", " ", s)
                                    return s
                                for raw in titles[:80]:
                                    t = _norm(raw)
                                    tl = t.lower()
                                    if not t:
                                        continue
                                    if "#short" in tl or "official video" in tl or "music video" in tl:
                                        continue
                                    if tl.startswith("how to "):
                                        out.append("How To {TOPIC} (Without Burnout)")
                                        continue
                                    if tl.startswith("why "):
                                        out.append("Why {TOPIC} Is Hard (And How To Fix It)")
                                        continue
                                    if tl.startswith("the truth") or "truth" in tl:
                                        out.append("The Truth About {TOPIC}")
                                        continue
                                    if "mistake" in tl or "mistakes" in tl:
                                        out.append("7 Mistakes Killing Your {TOPIC}")
                                        continue
                                    if tl.startswith("i tried") or " for 30 days" in tl:
                                        out.append("I Tried {TOPIC} For 30 Days (Here’s What Happened)")
                                        continue
                                    if "system" in tl or "framework" in tl or "playbook" in tl:
                                        out.append("The {MECH} System For {TOPIC}")
                                        continue
                                    if "explained" in tl:
                                        out.append("{TOPIC} Explained (No Fluff)")
                                        continue
                                    if "nobody tells" in tl:
                                        out.append("What Nobody Tells You About {TOPIC}")
                                        continue
                                cleaned=[]
                                seen=set()
                                for fr in out:
                                    if "{TOPIC}" not in fr and "{MECH}" not in fr:
                                        continue
                                    k = fr.lower()
                                    if k in seen:
                                        continue
                                    seen.add(k)
                                    cleaned.append(fr)
                                return cleaned
                            kw_tokens = _kw_list(include_kw)
                            style = _detect_style(chan_in, cand_df)
                            ev_strict = _strict_evidence(cand_df)
                            ev_for_frames = ev_strict if not ev_strict.empty else cand_df
                            winner_titles = ev_for_frames["title"].fillna("").astype(str).tolist()
                            frames = _extract_frames(winner_titles, style)
                            mechs = ["Identity", "Systems", "Discipline", "Psychology", "Leverage", "Habits", "Focus", "Execution"]
                            def _seed_pool(df: pd.DataFrame) -> list[str]:
                                # Prefer user keywords; then top niche terms; then cleaned search seeds.
                                seeds: list[str] = []
                                seeds.extend(kw_tokens)
                                # top_terms are already niche-weighted
                                tt = top_terms or []
                                # top_terms may be dict/Series/iterable depending on upstream scoring
                                if isinstance(tt, dict):
                                    tt = [k for k, _ in sorted(tt.items(), key=lambda kv: kv[1], reverse=True)]
                                elif isinstance(tt, pd.Series):
                                    tt = tt.tolist()
                                elif not isinstance(tt, list):
                                    try:
                                        tt = list(tt)
                                    except Exception:
                                        tt = []
                                for t in (tt or [])[:12]:
                                    if isinstance(t, str) and len(t.strip()) >= 3:
                                        seeds.append(t.strip())
                                if df is not None and not df.empty and "seed" in df.columns:
                                    for s in df["seed"].fillna("").astype(str).tolist():
                                        s2 = re.sub(r"[^A-Za-z0-9\s\-']", " ", s).strip()
                                        s2 = re.sub(r"\s{2,}", " ", s2)
                                        # avoid junky two-word fragments like 'reveals brutal'
                                        if len(s2) < 4:
                                            continue
                                        if any(x in s2.lower() for x in ["reveals", "brutal", "truth", "clips"]):
                                            continue
                                        seeds.append(s2)
                                # de-dup preserve order
                                seen=set(); out=[]
                                for s in seeds:
                                    k=s.lower()
                                    if k in seen: continue
                                    seen.add(k); out.append(s)
                                return out
                            seed_pool = _seed_pool(ev_for_frames)
                            if not seed_pool:
                                seed_pool = ["Wealth", "Success", "Money", "Discipline", "Mindset"]
                            def _render(frame: str, seed: str, mech: str, style: str, kw: str | None) -> str:
                                # style can later alter tone (mrbeast etc) but default keeps it clean
                                t = frame
                                # Prefer keyword if provided
                                topic = (kw or seed).strip()
                                t = t.replace("{SEED}", seed).replace("{MECH}", mech).replace("{TOPIC}", topic)
                                # Clean weird doubles
                                t = re.sub(r"\s{2,}", " ", t).strip()
                                return t
                            # Frame bank (non-spinner). Uses TOPIC + MECH and avoids repeating 'reveals/truth' constantly.
                            base_frames = [
                                "How {TOPIC} Really Works (The {MECH} Behind It)",
                                "Why {TOPIC} Keeps Failing (And The {MECH} Fix)",
                                "3 Mistakes Keeping You Stuck With {TOPIC}",
                                "I Tried {TOPIC} for 30 Days — Here’s What Changed",
                                "The {MECH} System For {TOPIC} (Steal This)",
                                "The Hidden Rule Behind {TOPIC}",
                                "{TOPIC}: The Simple System That Actually Sticks",
                                "What Nobody Tells You About {TOPIC}",
                                "The Fastest Way To Improve {TOPIC} (Without Burnout)",
                                "How To Build {TOPIC} When Motivation Is Zero",
                                "The {MECH} Checklist For {TOPIC} (Start Here)",
                                "{TOPIC} Explained In 10 Minutes (No Fluff)",
                            ]
                            # Merge mined frames with our bank, then de-dup.
                            all_frames = []
                            for fr in (frames or []):
                                if isinstance(fr, str) and fr.strip():
                                    all_frames.append(fr.strip())
                            for fr in base_frames:
                                all_frames.append(fr)
                            seen_fr=set(); uniq_frames=[]
                            for fr in all_frames:
                                k=fr.lower()
                                if k in seen_fr: continue
                                # drop bad generic mined frames
                                if "what works right now" in k: continue
                                if "fastest way to get results" in k: continue
                                seen_fr.add(k); uniq_frames.append(fr)
                            all_frames = uniq_frames
                            ideas = []
                            used = set()
                            prefix_counts = {}
                            family_counts = {}
                            per_seed_counts = {}
                            per_seed_cap = max(2, int(round(how_many * 0.30)))
                            def _accept(title: str) -> bool:
                                if not title:
                                    return False
                                if _is_spam_title(title):
                                    return False
                                k = title.lower().strip()
                                if k in used:
                                    return False
                                p = _prefix3(title)
                                if prefix_counts.get(p, 0) >= 2:
                                    return False
                                fam = _family(title)
                                if family_counts.get(fam, 0) >= 3:
                                    return False
                                return True
                            def _record(title: str, seed_txt: str):
                                used.add(title.lower().strip())
                                prefix_counts[_prefix3(title)] = prefix_counts.get(_prefix3(title), 0) + 1
                                fam = _family(title)
                                family_counts[fam] = family_counts.get(fam, 0) + 1
                                per_seed_counts[seed_txt] = per_seed_counts.get(seed_txt, 0) + 1
                                ideas.append(title)
                            # Generation pass 1: strict (must include keyword if provided)
                            kw_cycle = (kw_tokens or [None])
                            kw_i = 0
                            for seed_txt in seed_pool:
                                if len(ideas) >= how_many:
                                    break
                                per_seed_counts.setdefault(seed_txt, 0)
                                if per_seed_counts[seed_txt] >= per_seed_cap:
                                    continue
                                for fr in all_frames:
                                    if len(ideas) >= how_many:
                                        break
                                    mech = mechs[len(ideas) % len(mechs)]
                                    kw = kw_cycle[kw_i % len(kw_cycle)]
                                    kw_i += 1
                                    t = _clean_title_display(_render(fr, seed_txt, mech, style, kw))
                                    # if user provided keywords, enforce at least one appears in the title
                                    if kw_tokens:
                                        if not any(k.lower() in t.lower() for k in kw_tokens):
                                            continue
                                    if not _accept(t):
                                        continue
                                    _record(t, seed_txt)
                            # Generation pass 2: backfill (allow titles that don't contain kw, but still niche-safe)
                            if len(ideas) < how_many:
                                for seed_txt in seed_pool:
                                    if len(ideas) >= how_many:
                                        break
                                    if per_seed_counts.get(seed_txt, 0) >= per_seed_cap:
                                        continue
                                    for fr in all_frames:
                                        if len(ideas) >= how_many:
                                            break
                                        mech = mechs[len(ideas) % len(mechs)]
                                        t = _clean_title_display(_render(fr, seed_txt, mech, style, None))
                                        if not _accept(t):
                                            continue
                                        _record(t, seed_txt)
                            ideas = ideas[:how_many]
                            ideas_df = pd.DataFrame({"Primary title": ideas})
                            st.markdown("### Your titles")
                            if ideas_df is None or ideas_df.empty:
                                st.warning("Not enough high-quality evidence to generate ideas. Try 90 days or add a competitor.")
                            else:
                                if output_mode == "Titles only":
                                    # Cleaner than a table; still copy-friendly
                                    for i, t in enumerate(ideas_df["Primary title"].tolist()):
                                        st.markdown(f"**{i+1}.** {t}")
                                else:
                                    for i, row in ideas_df.iterrows():
                                        title_ = str(row.get("Primary title", "")).strip()
                                        with st.expander(f"{i+1}. {title_}", expanded=False):
                                            st.markdown(f"**Primary**: {title_}")
                                            st.markdown("**Hook (10s):** Outcome → tension → promise (one sentence each).")
                                            st.markdown("**Outline (5 beats):** Promise → Problem → Proof → Steps → Next action.")
                                            st.markdown("**Thumbnail direction:** 2–4 words, one promise, high contrast, no clutter.")
                            with st.expander("Evidence (what’s winning right now)", expanded=False):
                                ev = cand_df[["seed","title","channel_title","views","views_per_day","published","duration_s","niche_match","score"]].copy()
                                ev["published"] = ev["published"].astype(str).str.replace("T", " ").str.replace("Z", "")
                                ev = ev.sort_values(["score","views_per_day"], ascending=False).head(25)
                                st.dataframe(ev, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)