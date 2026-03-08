
from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import re
from datetime import datetime

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from engine import (
    TypologyCatalog,
    build_summary,
    default_aluminium_offcuts,
    export_project_workbook,
    expand_window_rows,
    load_default_glass_offcuts,
    load_glass_specs,
    optimise_aluminium,
    optimise_glass,
)

BASE_DIR = Path(__file__).resolve().parent
PREVIEW_DIR = BASE_DIR / "typology_previews"
DEFAULT_LOGO = BASE_DIR / "ali_fab_logo.png"
PROJECTS_DIR = BASE_DIR / "projects_data"
PROJECTS_DIR.mkdir(exist_ok=True)
SQFT_PER_M2 = 10.7639
ALL_INPUT_FIELDS = [
    "OVERALL WIDTH",
    "OVERALL HEIGHT",
    "VENT WIDTH",
    "BOTTOM FIXED HEIGHT",
    "BOTTOM CLEARANCE REQUIRED",
    "MAIN VENT WIDTH",
]
FIELD_ORDER = {
    "OVERALL WIDTH": 1,
    "OVERALL HEIGHT": 2,
    "VENT WIDTH": 3,
    "MAIN VENT WIDTH": 4,
    "BOTTOM FIXED HEIGHT": 5,
    "BOTTOM CLEARANCE REQUIRED": 6,
}
FIELD_HELP = {
    "OVERALL WIDTH": "Full system width.",
    "OVERALL HEIGHT": "Full system height.",
    "VENT WIDTH": "Opening vent width for this typology.",
    "MAIN VENT WIDTH": "Main active leaf / vent width where required.",
    "BOTTOM FIXED HEIGHT": "Bottom fixed light height for the selected orientation.",
    "BOTTOM CLEARANCE REQUIRED": "Required clearance below the door leaf.",
}

st.set_page_config(page_title="Ali Fabrication System", page_icon="🪟", layout="wide")


@st.cache_resource
def get_catalog():
    return TypologyCatalog()


@st.cache_data
def get_glass_specs():
    return load_glass_specs()


@st.cache_data
def get_default_glass_offcuts():
    return load_default_glass_offcuts()


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s))


def mm_to_m(mm_value: float) -> float:
    return round(float(mm_value or 0) / 1000.0, 2)


def file_as_base64(path: Path) -> str:
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def uploaded_or_default_logo(uploaded_file) -> str:
    if uploaded_file:
        return base64.b64encode(uploaded_file.read()).decode("utf-8")
    return file_as_base64(DEFAULT_LOGO)


def preview_base64(variant_key: str) -> str:
    base = safe_name(variant_key)
    for ext in [".png", ".jpg", ".jpeg"]:
        p = PREVIEW_DIR / f"{base}{ext}"
        if p.exists():
            return base64.b64encode(p.read_bytes()).decode("utf-8")
    return ""


def system_code_from_label(label: str) -> str:
    label = str(label or "")
    code = label.split("·")[0].strip()
    if " - " in code:
        return code.split(" - ")[0].strip()
    return code


def variant_short_name(label: str) -> str:
    text = str(label or "")
    if "·" in text:
        return text.split("·", 1)[1].strip()
    return text


def slugify_project_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name).strip()).strip("_")
    return slug or "project"


def project_file(name: str) -> Path:
    return PROJECTS_DIR / f"{slugify_project_name(name)}.json"


def list_saved_projects() -> list[str]:
    names = []
    for p in sorted(PROJECTS_DIR.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            names.append(payload.get("project_name", p.stem))
        except Exception:
            names.append(p.stem)
    return names


def load_project_data(name: str):
    p = project_file(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_project_data(name: str, payload: dict):
    p = project_file(name)
    payload = dict(payload)
    payload["project_name"] = name
    payload["saved_at"] = datetime.utcnow().isoformat() + "Z"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def delete_project_data(name: str):
    p = project_file(name)
    if p.exists():
        p.unlink()


def apply_project_payload(payload: dict, default_variant: str, default_glass_spec: str, variant_map: dict[str, str]):
    windows = payload.get("windows") or [blank_window(default_variant, default_glass_spec, variant_map, 1, "W1")]
    st.session_state.windows = windows
    st.session_state.next_window_id = int(payload.get("next_window_id", max([w.get("id", 0) for w in windows] + [0]) + 1))
    st.session_state.al_offcuts = payload.get("al_offcuts", default_aluminium_offcuts())
    st.session_state.glass_offcuts = payload.get("glass_offcuts", get_default_glass_offcuts()[:60])
    st.session_state.project_name_value = payload.get("project_name", "Ali Fabrication Project")
    st.session_state.client_name_value = payload.get("client_name", "")
    st.session_state.finish_value = payload.get("finish", "Powder Coated")
    st.session_state.stock_length_mm_value = float(payload.get("stock_length_mm", 6400.0))
    st.session_state.glass_sheet_width_mm_value = float(payload.get("glass_sheet_width_mm", 3660.0))
    st.session_state.glass_sheet_height_mm_value = float(payload.get("glass_sheet_height_mm", 2440.0))
    st.session_state.kerf_mm_value = float(payload.get("kerf_mm", 3.0))
    st.session_state.default_row_glass_value = payload.get("default_row_glass", default_glass_spec)


def project_payload_from_state(project_name: str, client_name: str, finish: str, stock_length_mm: float, glass_sheet_width_mm: float, glass_sheet_height_mm: float, kerf_mm: float, default_row_glass: str) -> dict:
    return {
        "project_name": project_name,
        "client_name": client_name,
        "finish": finish,
        "stock_length_mm": stock_length_mm,
        "glass_sheet_width_mm": glass_sheet_width_mm,
        "glass_sheet_height_mm": glass_sheet_height_mm,
        "kerf_mm": kerf_mm,
        "default_row_glass": default_row_glass,
        "windows": st.session_state.windows,
        "next_window_id": st.session_state.next_window_id,
        "al_offcuts": st.session_state.al_offcuts,
        "glass_offcuts": st.session_state.glass_offcuts,
    }



def get_variant_index(variant_key: str) -> int:
    try:
        key = str(variant_key)
        if "__" in key:
            return int(key.split("__")[-1])
    except Exception:
        pass
    return 1


def get_window_family(variant_label: str) -> str:
    txt = str(variant_label or "").upper()
    if "SLIDING FOLDING" in txt:
        return "folding"
    if "3 TRACK" in txt:
        return "sliding3"
    if "SLIDING" in txt:
        return "sliding2"
    if "SWING" in txt:
        return "swing"
    if "PROJECTING" in txt:
        return "projecting"
    return "fixed"


def normalize_profile_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").upper()).strip()

def lookup_profile_weight(profile_name: str, weights_map: dict) -> float:
    profile_norm = normalize_profile_key(profile_name)
    if not profile_norm:
        return 0.0
    # exact
    for k, v in weights_map.items():
        if normalize_profile_key(k) == profile_norm and v not in [None, ""]:
            return float(v)
    # longest prefix match, useful for names like 'GDIL 6001 OUTERFRAME' -> 'GDIL 6001'
    best_key = None
    for k, v in weights_map.items():
        if v in [None, ""]:
            continue
        kn = normalize_profile_key(k)
        if profile_norm.startswith(kn) and (best_key is None or len(kn) > len(best_key)):
            best_key = kn
            best_val = v
    if best_key is not None:
        return float(best_val)
    return 0.0


def get_profile_cost_map() -> dict:
    rows = st.session_state.get("profile_cost_map", []) or []
    out = {}
    for row in rows:
        profile = str(row.get("profile", "")).strip()
        if not profile:
            continue
        out[profile] = {
            "weight_per_m_kg": float(row.get("weight_per_m_kg", 0) or 0),
            "cost_per_kg": float(row.get("cost_per_kg", 0) or 0),
        }
    return out


def lookup_profile_weight_override(profile_name: str, weights_map: dict) -> float:
    override_map = get_profile_cost_map()
    if profile_name in override_map and float(override_map[profile_name].get("weight_per_m_kg", 0) or 0) > 0:
        return float(override_map[profile_name]["weight_per_m_kg"])
    return lookup_profile_weight(profile_name, weights_map)


def lookup_profile_cost_per_kg(profile_name: str) -> float:
    override_map = get_profile_cost_map()
    if profile_name in override_map and float(override_map[profile_name].get("cost_per_kg", 0) or 0) > 0:
        return float(override_map[profile_name]["cost_per_kg"])
    return float(st.session_state.get("aluminium_cost_per_kg_value", 0) or 0)


def build_profile_cost_map_rows(profile_rows, weights_map):
    profile_names = sorted({str(r.get("profile", "")).strip() for r in profile_rows if str(r.get("profile", "")).strip()})
    existing = get_profile_cost_map()
    rows = []
    for profile in profile_names:
        existing_row = existing.get(profile, {})
        default_weight = existing_row.get("weight_per_m_kg", 0) or lookup_profile_weight(profile, weights_map)
        default_cost = existing_row.get("cost_per_kg", 0) or float(st.session_state.get("aluminium_cost_per_kg_value", 0) or 0)
        rows.append({
            "profile": profile,
            "weight_per_m_kg": round(float(default_weight or 0), 4),
            "cost_per_kg": round(float(default_cost or 0), 2),
        })
    return rows

def build_window_svg(window: dict, variant_label: str, variant_key: str, compact: bool = False) -> str:
    family = get_window_family(variant_label)
    idx = get_variant_index(variant_key)
    overall_w = max(float(window.get("OVERALL WIDTH", 1200) or 1200), 1.0)
    overall_h = max(float(window.get("OVERALL HEIGHT", 1500) or 1500), 1.0)
    bottom_fixed = max(float(window.get("BOTTOM FIXED HEIGHT", 0) or 0), 0.0)

    vb_w = 320
    vb_h = 200 if compact else 250
    pad_x = 36 if compact else 52
    pad_y = 20 if compact else 34

    frame_w = vb_w - (pad_x * 2)
    frame_h = vb_h - (pad_y * 2) - (0 if compact else 18)

    ratio = overall_w / overall_h
    draw_w = frame_w
    draw_h = frame_h
    if ratio > 1:
        draw_h = min(frame_h, frame_w / ratio)
    else:
        draw_w = min(frame_w, frame_h * ratio)

    x0 = (vb_w - draw_w) / 2
    y0 = pad_y + ((frame_h - draw_h) / 2)
    x1 = x0 + draw_w
    y1 = y0 + draw_h

    svg = []
    svg.append(f'<svg viewBox="0 0 {vb_w} {vb_h}" width="100%" xmlns="http://www.w3.org/2000/svg">')
    svg.append('<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#0f4c81"/></marker></defs>')
    svg.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{draw_w:.1f}" height="{draw_h:.1f}" rx="2" fill="#ffffff" stroke="#0f172a" stroke-width="4"/>')
    svg.append(f'<rect x="{x0+8:.1f}" y="{y0+8:.1f}" width="{max(draw_w-16,1):.1f}" height="{max(draw_h-16,1):.1f}" fill="#f8fafc" stroke="#94a3b8" stroke-width="1.5"/>')

    def line(xa, ya, xb, yb, color="#334155", w=2):
        svg.append(f'<line x1="{xa:.1f}" y1="{ya:.1f}" x2="{xb:.1f}" y2="{yb:.1f}" stroke="{color}" stroke-width="{w}" />')

    def rect(x, y, w, h, fill="#eef6ff", stroke="#2563eb", sw=2):
        svg.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def arrow(xa, ya, xb, yb):
        svg.append(f'<line x1="{xa:.1f}" y1="{ya:.1f}" x2="{xb:.1f}" y2="{yb:.1f}" stroke="#0f4c81" stroke-width="2" marker-end="url(#arr)" />')

    if family == "projecting":
        transom_y = y1
        if bottom_fixed > 0:
            fixed_ratio = min(bottom_fixed / overall_h, 0.55)
            transom_y = y1 - (draw_h * fixed_ratio)
            line(x0, transom_y, x1, transom_y)
        if idx in {1, 2}:
            sash_w = draw_w * 0.46
            sx = x0 + 8 if idx == 1 else x1 - sash_w - 8
            rect(sx, y0 + 8, sash_w, max(transom_y - y0 - 16, 12), fill="#dbeafe")
            arrow(sx + sash_w / 2, y0 + 24, sx + sash_w / 2 + (-22 if idx == 1 else 22), y0 + 8)
        elif idx in {3, 4}:
            sash_h = max((transom_y - y0) * 0.46, 16)
            sy = y0 + 8 if idx == 3 else transom_y - sash_h - 8
            rect(x0 + 8, sy, draw_w - 16, sash_h, fill="#dbeafe")
            arrow(x0 + draw_w / 2, sy + sash_h / 2, x0 + draw_w / 2 + (18 if idx == 3 else -18), sy + sash_h / 2 - 18)
        else:
            sash_w = draw_w * 0.56
            sash_h = max((transom_y - y0) * 0.52, 18)
            sx = x0 + (draw_w - sash_w) / 2
            sy = y0 + 10
            rect(sx, sy, sash_w, sash_h, fill="#dbeafe")
            arrow(sx + sash_w / 2, sy + sash_h / 2, sx + sash_w / 2, sy - 16)
    elif family == "sliding2":
        mid = x0 + draw_w * 0.53
        line(mid, y0 + 6, mid, y1 - 6)
        rect(x0 + 8, y0 + 8, draw_w * 0.54, draw_h - 16, fill="#dbeafe")
        rect(x0 + draw_w * 0.34, y0 + 18, draw_w * 0.58, draw_h - 36, fill="#ecfeff", stroke="#0f766e")
        arrow(x0 + draw_w * 0.25, y0 + draw_h / 2, x0 + draw_w * 0.12, y0 + draw_h / 2)
        arrow(x0 + draw_w * 0.72, y0 + draw_h / 2, x0 + draw_w * 0.86, y0 + draw_h / 2)
    elif family == "sliding3":
        p1 = x0 + draw_w / 3
        p2 = x0 + (draw_w * 2 / 3)
        line(p1, y0 + 6, p1, y1 - 6)
        line(p2, y0 + 6, p2, y1 - 6)
        rect(x0 + 8, y0 + 10, draw_w / 3 - 12, draw_h - 20, fill="#dbeafe")
        rect(p1 + 4, y0 + 18, draw_w / 3 - 8, draw_h - 36, fill="#ecfeff", stroke="#0f766e")
        rect(p2 + 4, y0 + 10, draw_w / 3 - 12, draw_h - 20, fill="#dbeafe")
        arrow(x0 + draw_w * 0.15, y0 + draw_h / 2, x0 + draw_w * 0.06, y0 + draw_h / 2)
        arrow(x0 + draw_w * 0.50, y0 + draw_h / 2, x0 + draw_w * 0.60, y0 + draw_h / 2)
        arrow(x0 + draw_w * 0.83, y0 + draw_h / 2, x0 + draw_w * 0.93, y0 + draw_h / 2)
    elif family == "folding":
        panels = 4
        panel_w = draw_w / panels
        for i in range(1, panels):
            line(x0 + panel_w * i, y0 + 6, x0 + panel_w * i, y1 - 6)
        for i in range(panels):
            fill = "#dbeafe" if i % 2 == 0 else "#ecfeff"
            rect(x0 + 4 + panel_w * i, y0 + 8, panel_w - 8, draw_h - 16, fill=fill)
        arrow(x0 + draw_w * 0.12, y0 + draw_h / 2, x0 + draw_w * 0.88, y0 + draw_h / 2 - 20)
    elif family == "swing":
        if idx == 3:
            mid = x0 + draw_w / 2
            line(mid, y0 + 8, mid, y1 - 8)
            arrow(x0 + draw_w * 0.25, y0 + draw_h * 0.78, x0 + draw_w * 0.10, y0 + draw_h * 0.25)
            arrow(x0 + draw_w * 0.75, y0 + draw_h * 0.78, x0 + draw_w * 0.90, y0 + draw_h * 0.25)
        else:
            hinge_left = idx == 1
            rect(x0 + 8, y0 + 8, draw_w - 16, draw_h - 16, fill="#dbeafe")
            if hinge_left:
                arrow(x0 + draw_w * 0.35, y0 + draw_h * 0.75, x0 + draw_w * 0.08, y0 + draw_h * 0.22)
            else:
                arrow(x0 + draw_w * 0.65, y0 + draw_h * 0.75, x0 + draw_w * 0.92, y0 + draw_h * 0.22)

    if compact:
        svg.append(f'<text x="{vb_w/2:.1f}" y="{vb_h-10:.1f}" text-anchor="middle" font-size="10" fill="#475569">{int(overall_w)} × {int(overall_h)}</text>')
    else:
        line(x0, y0 - 16, x1, y0 - 16, "#64748b", 1.5)
        line(x0, y0 - 11, x0, y0 - 22, "#64748b", 1.5)
        line(x1, y0 - 11, x1, y0 - 22, "#64748b", 1.5)
        svg.append(f'<text x="{(x0+x1)/2:.1f}" y="{y0-20:.1f}" text-anchor="middle" font-size="11" fill="#334155">{int(overall_w)} mm</text>')
        line(x0 - 18, y0, x0 - 18, y1, "#64748b", 1.5)
        line(x0 - 12, y0, x0 - 24, y0, "#64748b", 1.5)
        line(x0 - 12, y1, x0 - 24, y1, "#64748b", 1.5)
        cy = (y0 + y1) / 2
        svg.append(f'<text x="{x0-22:.1f}" y="{cy:.1f}" text-anchor="middle" font-size="11" fill="#334155" transform="rotate(-90 {x0-22:.1f} {cy:.1f})">{int(overall_h)} mm</text>')

    svg.append('</svg>')
    return "".join(svg)



def inject_brand_css(primary: str, accent: str):
    css = """
        <style>
        .stApp {{
            background:
                radial-gradient(circle at top right, rgba(22,163,74,0.07), transparent 28%),
                linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
        }}
        .brand-hero {{
            background: linear-gradient(135deg, {primary} 0%, #0b2539 50%, {accent} 100%);
            color: white;
            border-radius: 28px;
            padding: 26px 30px;
            box-shadow: 0 18px 44px rgba(15, 23, 42, 0.16);
            margin-bottom: 1rem;
            position: relative;
            overflow: hidden;
        }}
        .brand-hero:before {{
            content: "";
            position: absolute;
            right: -60px;
            top: -60px;
            width: 220px;
            height: 220px;
            background: rgba(255,255,255,0.08);
            border-radius: 999px;
        }}
        .hero-wrap {{
            display: flex;
            align-items: center;
            gap: 20px;
            position: relative;
            z-index: 2;
        }}
        .hero-logo {{
            width: 94px;
            height: 94px;
            object-fit: contain;
            border-radius: 18px;
            background: rgba(255,255,255,0.08);
            padding: 10px;
        }}
        .hero-title {{
            margin: 0;
            font-size: 2rem;
            line-height: 1.05;
            font-weight: 800;
        }}
        .hero-sub {{
            margin-top: 0.45rem;
            opacity: 0.95;
            font-size: 1rem;
        }}
        .metric-card {{
            background: white;
            border-radius: 18px;
            padding: 16px 18px;
            border: 1px solid rgba(148,163,184,0.15);
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
        }}
        .metric-label {{
            color: #475569;
            font-size: 0.92rem;
            margin-bottom: 0.35rem;
        }}
        .metric-value {{
            color: #0f172a;
            font-size: 1.7rem;
            font-weight: 700;
        }}
        .soft-card {{
            background: rgba(255,255,255,0.9);
            border: 1px solid rgba(148,163,184,0.18);
            border-radius: 22px;
            padding: 18px 18px 10px 18px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
            backdrop-filter: blur(6px);
            margin-bottom: 1rem;
        }}
        .section-title {{
            font-size: 1.14rem;
            font-weight: 800;
            color: #0f172a;
            margin-bottom: 0.65rem;
        }}
        .preview-shell {{
            background: linear-gradient(180deg, rgba(248,250,252,0.95), rgba(255,255,255,0.98));
            border: 1px solid rgba(148,163,184,0.18);
            border-radius: 18px;
            padding: 14px;
            text-align: center;
        }}
        .preview-code {{
            display: inline-block;
            background: rgba(15,76,129,0.1);
            color: {primary};
            border: 1px solid rgba(15,76,129,0.12);
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 0.82rem;
            font-weight: 700;
            margin-bottom: 10px;
        }}
        .preview-variant {{
            color: #334155;
            font-size: 0.88rem;
            margin-bottom: 8px;
            min-height: 38px;
        }}
        .preview-img {{
            width: 100%;
            max-height: 230px;
            object-fit: contain;
            background: white;
            border-radius: 16px;
            padding: 10px;
        }}
        .mini-tag {{
            display:inline-block;
            background:#eff6ff;
            color:#1d4ed8;
            padding:4px 8px;
            border-radius:999px;
            margin: 0 6px 6px 0;
            font-size:0.78rem;
            font-weight:600;
        }}
        div[data-testid="stExpander"] {{
            border: 1px solid rgba(148,163,184,0.18);
            border-radius: 18px;
            background: rgba(255,255,255,0.88);
        }}
        .stButton>button {{
            border-radius: 12px;
            border: none;
            background: {primary};
            color: white;
            font-weight: 700;
        }}
        .stDownloadButton>button {{
            border-radius: 12px;
            border: none;
            background: {accent};
            color: white;
            font-weight: 700;
        }}
        button[data-baseweb="tab"] {{
            color: #0f4c81 !important;
            font-weight: 700 !important;
        }}
        button[data-baseweb="tab"][aria-selected="true"] {{
            color: #dc2626 !important;
        }}
        .board-card {{
            background: rgba(255,255,255,0.94);
            border: 1px solid rgba(148,163,184,0.18);
            border-radius: 22px;
            padding: 16px;
            box-shadow: 0 10px 26px rgba(15,23,42,0.06);
            min-height: 310px;
        }}
        .board-top {{
            display:flex;
            justify-content:space-between;
            align-items:flex-start;
            gap:10px;
            margin-bottom:10px;
        }}
        .board-label {{
            font-weight:800;
            color:#0f172a;
            font-size:1rem;
        }}
        .board-code {{
            color:#475569;
            font-size:0.8rem;
            margin-top:2px;
        }}
        .board-status {{
            background:#eff6ff;
            color:#1d4ed8;
            border:1px solid rgba(29,78,216,0.10);
            padding:4px 10px;
            border-radius:999px;
            font-size:0.75rem;
            font-weight:700;
        }}
        .drawing-shell {{
            background:linear-gradient(180deg,#ffffff,#f8fafc);
            border:1px solid rgba(148,163,184,0.16);
            border-radius:18px;
            padding:8px;
            margin:10px 0 12px 0;
        }}
        .preview-subhead {{
            margin:10px 0 6px 0;
            font-size:0.78rem;
            color:#64748b;
            font-weight:700;
            text-transform:uppercase;
            letter-spacing:0.04em;
        }}
        .board-meta {{
            display:grid;
            grid-template-columns:1fr 1fr;
            gap:8px;
            margin-top:10px;
        }}
        .board-kpi {{
            background:#f8fafc;
            border-radius:14px;
            padding:10px;
            border:1px solid rgba(148,163,184,0.12);
        }}
        .board-kpi-label {{
            font-size:0.72rem;
            color:#64748b;
            text-transform:uppercase;
            letter-spacing:0.04em;
        }}
        .board-kpi-value {{
            font-size:0.95rem;
            color:#0f172a;
            font-weight:800;
            margin-top:4px;
        }}
        @media (max-width: 900px) {{
            .brand-hero {{
                padding: 18px 16px;
                border-radius: 20px;
            }}
            .hero-wrap {{
                gap: 12px;
                align-items: flex-start;
            }}
            .hero-logo {{
                width: 62px;
                height: 62px;
                padding: 6px;
            }}
            .hero-title {{
                font-size: 1.35rem;
            }}
            .hero-sub {{
                font-size: 0.92rem;
            }}
            .soft-card {{
                padding: 14px 12px 8px 12px;
                border-radius: 16px;
            }}
            .preview-img {{
                max-height: 160px;
            }}
            .metric-value {{
                font-size: 1.3rem;
            }}
            .board-card {{
                min-height: 280px;
                padding: 12px;
                border-radius: 18px;
            }}
            .board-meta {{
                grid-template-columns:1fr;
            }}
            button[data-baseweb="tab"] {{
                color: #0f4c81 !important;
                font-size: 0.92rem !important;
            }}
        }}
        </style>
    """.format(primary=primary, accent=accent)
    st.markdown(css, unsafe_allow_html=True)


def render_metric(label: str, value: str):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_schedule_bom_workbook(window_schedule_df: pd.DataFrame, bom_profiles_df: pd.DataFrame, bom_glass_df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        window_schedule_df.to_excel(writer, sheet_name="Window Schedule", index=False)
        bom_profiles_df.to_excel(writer, sheet_name="BOM Profiles", index=False)
        bom_glass_df.to_excel(writer, sheet_name="BOM Glass", index=False)
    return output.getvalue()



def render_aluminium_bar_layouts(aluminium: dict):
    bars = aluminium.get("bars", []) or []
    if not bars:
        st.info("No new aluminium bars are required.")
        return

    st.markdown('<div class="section-title">Visual aluminium cutting layouts</div>', unsafe_allow_html=True)

    def fmt_degree(value):
        txt = str(value or "").strip().replace("°", "")
        if txt in {"", "0", "0.0"}:
            txt = "90"
        return f"{txt}°"

    for bar in bars:
        stock = float(bar.get("stock_length_mm", 0) or 0)
        used = float(bar.get("used_mm", 0) or 0)
        waste = float(bar.get("waste_mm", 0) or 0)
        cuts = bar.get("cuts", []) or []
        if stock <= 0:
            continue

        st.markdown(
            f"""
            <div class="soft-card">
                <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
                    <div style="font-weight:800;color:#0f172a;">Bar {bar.get('bar_no','')}</div>
                    <div style="color:#475569;font-size:0.9rem;">
                        Profile: <b>{bar.get('profile','')}</b>
                        &nbsp;|&nbsp; Stock: <b>{int(stock)} mm</b>
                        &nbsp;|&nbsp; Used: <b>{int(used)} mm</b>
                        &nbsp;|&nbsp; Waste: <b>{int(waste)} mm</b>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if not cuts:
            st.warning("No cut pieces were attached to this bar.")
            continue

        segs = []
        markers = []
        rows = []
        running = 0.0

        for idx, cut in enumerate(cuts, start=1):
            length = float(cut.get("length_mm", 0) or 0)
            angle = fmt_degree(cut.get("cut_degree", "90"))
            win = str(cut.get("window_label", "") or "")
            profile_name = str(cut.get("profile", bar.get("profile", "")) or "")
            width_pct = max((length / stock) * 100.0, 2.2)

            segs.append(
                f'<div style="width:{width_pct:.4f}%;min-width:82px;height:96px;background:#dbeafe;'
                'border-right:1px solid white;display:flex;align-items:center;justify-content:center;'
                'text-align:center;font-size:10px;font-weight:700;color:#1e3a8a;padding:4px;overflow:hidden;line-height:1.08;">'
                f'{win}<br>{profile_name[:20]}<br>{int(length)} mm<br>{angle}</div>'
            )

            rows.append({
                "Cut No": idx,
                "Window": win,
                "Profile": profile_name,
                "Length (mm)": int(length),
                "Angle": angle,
                "Source bar length (mm)": int(stock),
            })

            running += length
            if idx < len(cuts):
                left_pct = max(min((running / stock) * 100.0, 99.7), 0.3)
                markers.append(
                    f'<div style="position:absolute;left:calc({left_pct:.4f}% - 1px);top:28px;bottom:0;width:2px;background:#0f172a;"></div>'
                    f'<div style="position:absolute;left:calc({left_pct:.4f}% - 24px);top:0;background:#0f172a;color:white;'
                    f'border-radius:999px;padding:3px 8px;font-size:11px;font-weight:700;">{angle}</div>'
                )

        if waste > 0:
            waste_pct = max((waste / stock) * 100.0, 2.2)
            segs.append(
                f'<div style="width:{waste_pct:.4f}%;min-width:58px;height:96px;background:#fee2e2;'
                'display:flex;align-items:center;justify-content:center;text-align:center;'
                'font-size:11px;font-weight:700;color:#991b1b;padding:4px;line-height:1.1;">'
                f'Waste<br>{int(waste)} mm</div>'
            )

        st.markdown(
            f"""
            <div style="position:relative;padding-top:28px;margin:-8px 0 12px 0;">
                <div style="display:flex;width:100%;border-radius:14px;overflow:hidden;border:1px solid rgba(148,163,184,0.25);background:white;">
                    {''.join(segs)}
                </div>
                {''.join(markers)}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_glass_sheet_layouts(glass: dict, sheet_w: float, sheet_h: float):
    jobs = glass.get("optimiser_jobs", []) or []
    unplaced = glass.get("unplaced_jobs", []) or []
    if unplaced:
        st.error(f"{len(unplaced)} glass piece(s) could not fit on the selected sheet size. Increase the sheet size or review those piece dimensions in Results.")
    if not jobs:
        if not unplaced:
            st.info("All glass pieces were covered by offcuts.")
        return

    st.markdown('<div class="section-title">Visual glass sheet layouts</div>', unsafe_allow_html=True)

    by_sheet = {}
    for item in jobs:
        by_sheet.setdefault(item.get("sheet_no", 1), []).append(item)

    for sheet_no, items in sorted(by_sheet.items(), key=lambda x: x[0]):
        scale = min(700.0 / max(sheet_w, 1), 420.0 / max(sheet_h, 1))
        canvas_w = max(int(sheet_w * scale), 240)
        canvas_h = max(int(sheet_h * scale), 180)

        pieces_html = []
        for i, item in enumerate(items):
            x = float(item.get("x_mm", 0) or 0)
            y = float(item.get("y_mm", 0) or 0)
            w = float(item.get("placed_width_mm", item.get("width_mm", 0)) or 0)
            h = float(item.get("placed_height_mm", item.get("height_mm", 0)) or 0)
            left = int(x * scale)
            top = int(y * scale)
            width = max(int(w * scale), 28)
            height = max(int(h * scale), 20)
            label = item.get("piece_id", item.get("window_label", f"P{i+1}"))
            subtitle = f"{int(w)} x {int(h)}"
            pieces_html.append(
                f'<div style="position:absolute;left:{left}px;top:{top}px;width:{width}px;height:{height}px;background:rgba(15,118,110,0.18);border:2px solid #0f766e;border-radius:8px;box-sizing:border-box;display:flex;align-items:center;justify-content:center;text-align:center;font-size:11px;font-weight:700;color:#134e4a;padding:2px;overflow:hidden;line-height:1.1;">{label}<br>{subtitle}</div>'
            )

        st.markdown(
            f"""
            <div class="soft-card">
                <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
                    <div style="font-weight:800;color:#0f172a;">Sheet {sheet_no}</div>
                    <div style="color:#475569;font-size:0.9rem;">Sheet size: <b>{int(sheet_w)} x {int(sheet_h)} mm</b> &nbsp;|&nbsp; Pieces: <b>{len(items)}</b></div>
                </div>
                <div style="position:relative;width:{canvas_w}px;height:{canvas_h}px;max-width:100%;border:2px solid #0f172a;border-radius:16px;background:linear-gradient(180deg,#ffffff,#f8fafc);overflow:hidden;">
                    {''.join(pieces_html)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def build_layout_pdf(project_name: str, client_name: str, finish: str, stock_length_mm: float, glass_sheet_width_mm: float, glass_sheet_height_mm: float, summary: dict, aluminium: dict, glass: dict) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    page_w, page_h = landscape(A4)

    def new_page(title: str, subtitle: str = ""):
        c.setFont("Helvetica-Bold", 20)
        c.drawString(30, page_h - 35, title)
        if subtitle:
            c.setFont("Helvetica", 10)
            c.setFillColor(colors.HexColor("#475569"))
            c.drawString(30, page_h - 52, subtitle)
            c.setFillColor(colors.black)

    def footer():
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawRightString(page_w - 24, 16, "Ali Fabrication Layout Pack")
        c.setFillColor(colors.black)

    new_page("Cutting Layout Summary", f"Project: {project_name}   Client: {client_name or '-'}   Finish: {finish}")
    c.setFont("Helvetica-Bold", 12)
    y = page_h - 90
    rows = [
        ("Window lines", str(summary.get("window_lines", 0))),
        ("Profile cuts", str(summary.get("profile_cuts", 0))),
        ("New aluminium bars", str(summary.get("aluminium_new_bars", 0))),
        ("Glass offcut hits", str(summary.get("glass_offcut_hits", 0))),
        ("New glass sheets", str(summary.get("glass_new_sheets", 0))),
        ("Total profile length", f"{round(float(summary.get('total_profile_length_mm', 0))/1000.0, 2)} m"),
        ("Total glass area", f"{round(float(summary.get('total_glass_area_m2', 0)), 2)} m²"),
        ("Aluminium stock length", f"{int(stock_length_mm)} mm"),
        ("Glass sheet size", f"{int(glass_sheet_width_mm)} x {int(glass_sheet_height_mm)} mm"),
    ]
    for label, value in rows:
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawString(40, y, label)
        c.setFillColor(colors.HexColor("#1d4ed8"))
        c.drawRightString(310, y, value)
        c.setFillColor(colors.black)
        y -= 20

    unplaced = glass.get("unplaced_jobs", []) or []
    c.setFillColor(colors.HexColor("#991b1b"))
    c.setFont("Helvetica-Bold", 11)
    if unplaced:
        c.drawString(360, page_h - 90, "Unplaced glass pieces")
        c.setFont("Helvetica", 9)
        yy = page_h - 110
        for item in unplaced[:15]:
            c.drawString(360, yy, f"{item.get('piece_id', item.get('window_label', 'Piece'))}: {int(item.get('width_mm',0))} x {int(item.get('height_mm',0))} mm")
            yy -= 14
    footer()
    c.showPage()

    bars = aluminium.get("bars", []) or []
    for bar in bars:
        new_page(f"Aluminium Bar {bar.get('bar_no', '')}", f"Profile: {bar.get('profile', '')}   Stock: {int(bar.get('stock_length_mm', 0))} mm")
        x0 = 40
        y0 = page_h / 2
        total_w = page_w - 80
        bar_h = 60
        c.setStrokeColor(colors.HexColor("#0f172a"))
        c.rect(x0, y0, total_w, bar_h, stroke=1, fill=0)
        stock = float(bar.get("stock_length_mm", 0) or 1)
        cursor = x0
        for cut in bar.get("cuts", []) or []:
            seg_w = max((float(cut.get("length_mm", 0))/stock) * total_w, 18)
            c.setFillColor(colors.HexColor("#dbeafe"))
            c.rect(cursor, y0, seg_w, bar_h, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#1e3a8a"))
            c.setFont("Helvetica-Bold", 8)
            txt = f"{cut.get('profile', '')} {int(cut.get('length_mm', 0))} mm"
            c.drawCentredString(cursor + seg_w/2, y0 + bar_h/2, txt[:28])
            cursor += seg_w
        waste = float(bar.get("waste_mm", 0) or 0)
        if waste > 0 and cursor < x0 + total_w:
            c.setFillColor(colors.HexColor("#fee2e2"))
            c.rect(cursor, y0, x0 + total_w - cursor, bar_h, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#991b1b"))
            c.drawCentredString(cursor + (x0 + total_w - cursor)/2, y0 + bar_h/2, f"Waste {int(waste)} mm")
        footer()
        c.showPage()

    jobs = glass.get("optimiser_jobs", []) or []
    by_sheet = {}
    for item in jobs:
        by_sheet.setdefault(item.get("sheet_no", 1), []).append(item)

    for sheet_no, items in sorted(by_sheet.items(), key=lambda x: x[0]):
        new_page(f"Glass Sheet {sheet_no}", f"Sheet size: {int(glass_sheet_width_mm)} x {int(glass_sheet_height_mm)} mm")
        x0, y0 = 40, 80
        draw_w, draw_h = page_w - 80, page_h - 150
        scale = min(draw_w / max(glass_sheet_width_mm, 1), draw_h / max(glass_sheet_height_mm, 1))
        sheet_w = glass_sheet_width_mm * scale
        sheet_h = glass_sheet_height_mm * scale
        c.setStrokeColor(colors.black)
        c.rect(x0, y0, sheet_w, sheet_h, stroke=1, fill=0)
        palette = ["#ccfbf1", "#bfdbfe", "#fde68a", "#fecaca", "#ddd6fe", "#fed7aa"]
        for i, item in enumerate(items):
            x = x0 + float(item.get("x_mm", 0) or 0) * scale
            y = y0 + float(item.get("y_mm", 0) or 0) * scale
            w = float(item.get("placed_width_mm", item.get("width_mm", 0)) or 0) * scale
            h = float(item.get("placed_height_mm", item.get("height_mm", 0)) or 0) * scale
            c.setFillColor(colors.HexColor(palette[i % len(palette)]))
            c.rect(x, y, w, h, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#134e4a"))
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(x + w/2, y + h/2, f"{item.get('piece_id', item.get('window_label', 'P'))}")
        footer()
        c.showPage()

    c.save()
    return buffer.getvalue()


def blank_window(default_variant: str, default_glass_spec: str, variant_map: dict[str, str], next_id: int, label: str):
    base = {
        "id": next_id,
        "label": label,
        "variant_key": default_variant,
        "variant_label": variant_map[default_variant],
        "window_qty": 1,
        "glass_spec": default_glass_spec,
    }
    for field in ALL_INPUT_FIELDS:
        base[field] = 0.0
    base["OVERALL WIDTH"] = 1200.0
    base["OVERALL HEIGHT"] = 1500.0
    return base


def set_default_windows(default_variant: str, default_glass_spec: str, variant_map: dict[str, str]):
    if "windows" not in st.session_state:
        st.session_state.windows = [blank_window(default_variant, default_glass_spec, variant_map, 1, "W1")]
    if "next_window_id" not in st.session_state:
        st.session_state.next_window_id = 2


def ensure_supporting_state():
    if "al_offcuts" not in st.session_state:
        st.session_state.al_offcuts = default_aluminium_offcuts()
    if "glass_offcuts" not in st.session_state:
        st.session_state.glass_offcuts = get_default_glass_offcuts()[:60]


def add_window(default_variant: str, default_glass_spec: str, variant_map: dict[str, str]):
    next_id = st.session_state.next_window_id
    st.session_state.windows.append(blank_window(default_variant, default_glass_spec, variant_map, next_id, f"W{len(st.session_state.windows)+1}"))
    st.session_state.next_window_id += 1


def duplicate_window(window_id: int):
    windows = st.session_state.windows
    for i, w in enumerate(windows):
        if w["id"] == window_id:
            next_id = st.session_state.next_window_id
            clone = dict(w)
            clone["id"] = next_id
            clone["label"] = f"W{len(windows)+1}"
            windows.insert(i + 1, clone)
            st.session_state.next_window_id += 1
            break


def remove_window(window_id: int):
    st.session_state.windows = [w for w in st.session_state.windows if w["id"] != window_id]


def update_window_field(index: int, field: str, value):
    st.session_state.windows[index][field] = value


catalog = get_catalog()
variant_options = catalog.list_variant_options()
variant_lookup = catalog.variant_lookup()
variant_keys = [k for k, _ in variant_options]
variant_map = dict(variant_options)
default_variant = variant_options[0][0]
glass_specs = get_glass_specs()
default_glass_spec = "6.38MM GREY TINTED LAMINATED GLASS" if "6.38MM GREY TINTED LAMINATED GLASS" in glass_specs else glass_specs[0]

set_default_windows(default_variant, default_glass_spec, variant_map)
ensure_supporting_state()

if "active_project_name" not in st.session_state:
    saved = list_saved_projects()
    if saved:
        latest_name = saved[-1]
        payload = load_project_data(latest_name)
        if payload:
            apply_project_payload(payload, default_variant, default_glass_spec, variant_map)
            st.session_state.active_project_name = latest_name
        else:
            st.session_state.active_project_name = "Ali Fabrication Project"
            st.session_state.project_name_value = "Ali Fabrication Project"
    else:
        st.session_state.active_project_name = "Ali Fabrication Project"
        st.session_state.project_name_value = "Ali Fabrication Project"
        st.session_state.client_name_value = ""
        st.session_state.finish_value = "Powder Coated"
        st.session_state.stock_length_mm_value = 6400.0
        st.session_state.glass_sheet_width_mm_value = 3660.0
        st.session_state.glass_sheet_height_mm_value = 2440.0
        st.session_state.kerf_mm_value = 3.0
        st.session_state.default_row_glass_value = default_glass_spec

with st.sidebar:
    st.header("Projects")
    saved_projects = list_saved_projects()
    selectable_projects = saved_projects if saved_projects else [st.session_state.active_project_name]
    current_idx = selectable_projects.index(st.session_state.active_project_name) if st.session_state.active_project_name in selectable_projects else 0
    selected_project = st.selectbox("Open saved project", selectable_projects, index=current_idx)
    if selected_project != st.session_state.active_project_name:
        payload = load_project_data(selected_project)
        if payload:
            apply_project_payload(payload, default_variant, default_glass_spec, variant_map)
            st.session_state.active_project_name = selected_project
            st.rerun()

    new_project_name = st.text_input("Create new project", value="", placeholder="e.g. ABC Apartments")
    p1, p2 = st.columns(2)
    with p1:
        if st.button("New project", use_container_width=True):
            name = (new_project_name or "New Project").strip()
            st.session_state.active_project_name = name
            st.session_state.windows = [blank_window(default_variant, default_glass_spec, variant_map, 1, "W1")]
            st.session_state.next_window_id = 2
            st.session_state.al_offcuts = default_aluminium_offcuts()
            st.session_state.glass_offcuts = get_default_glass_offcuts()[:60]
            st.session_state.project_name_value = name
            st.session_state.client_name_value = ""
            st.session_state.finish_value = "Powder Coated"
            st.session_state.stock_length_mm_value = 6400.0
            st.session_state.glass_sheet_width_mm_value = 3660.0
            st.session_state.glass_sheet_height_mm_value = 2440.0
            st.session_state.kerf_mm_value = 3.0
            st.session_state.default_row_glass_value = default_glass_spec
            save_project_data(name, project_payload_from_state(name, "", "Powder Coated", 6400.0, 3660.0, 2440.0, 3.0, default_glass_spec))
            st.rerun()
    with p2:
        if st.button("Delete project", use_container_width=True):
            delete_project_data(st.session_state.active_project_name)
            remaining = list_saved_projects()
            if remaining:
                payload = load_project_data(remaining[-1])
                if payload:
                    apply_project_payload(payload, default_variant, default_glass_spec, variant_map)
                    st.session_state.active_project_name = remaining[-1]
            else:
                st.session_state.active_project_name = "Ali Fabrication Project"
                st.session_state.windows = [blank_window(default_variant, default_glass_spec, variant_map, 1, "W1")]
                st.session_state.next_window_id = 2
                st.session_state.al_offcuts = default_aluminium_offcuts()
                st.session_state.glass_offcuts = get_default_glass_offcuts()[:60]
                st.session_state.project_name_value = "Ali Fabrication Project"
                st.session_state.client_name_value = ""
                st.session_state.finish_value = "Powder Coated"
                st.session_state.stock_length_mm_value = 6400.0
                st.session_state.glass_sheet_width_mm_value = 3660.0
                st.session_state.glass_sheet_height_mm_value = 2440.0
                st.session_state.kerf_mm_value = 3.0
                st.session_state.default_row_glass_value = default_glass_spec
            st.rerun()

    st.caption(f"Active project: {st.session_state.active_project_name}")
    st.caption("Changes autosave as you work and should remain after refresh.")

    st.header("Project Details")
    project_name = st.text_input("Project name", key="project_name_value")
    client_name = st.text_input("Client name", key="client_name_value")
    finish = st.text_input("Aluminium finish", key="finish_value")
    primary = "#0F4C81"
    accent = "#F28C36"
    logo_file = None

    st.header("Material Controls")
    stock_length_mm = st.number_input("Aluminium stock length (mm)", min_value=1000.0, step=100.0, key="stock_length_mm_value")
    glass_sheet_width_mm = st.number_input("Glass sheet width (mm)", min_value=500.0, step=10.0, key="glass_sheet_width_mm_value")
    glass_sheet_height_mm = st.number_input("Glass sheet height (mm)", min_value=500.0, step=10.0, key="glass_sheet_height_mm_value")
    kerf_mm = st.number_input("Saw / cut kerf (mm)", min_value=0.0, step=0.5, key="kerf_mm_value")
    aluminium_cost_per_kg = st.number_input("Aluminium cost per kg", min_value=0.0, step=0.1, value=float(st.session_state.get("aluminium_cost_per_kg_value",0.0)), key="aluminium_cost_per_kg_value")
    glass_cost_per_sqft = st.number_input("Glass cost per sq ft", min_value=0.0, step=0.1, value=float(st.session_state.get("glass_cost_per_sqft_value",0.0)), key="glass_cost_per_sqft_value")
    default_glass_index = glass_specs.index(st.session_state.default_row_glass_value) if st.session_state.default_row_glass_value in glass_specs else 0
    default_row_glass = st.selectbox("Default glass specification", glass_specs, index=default_glass_index, key="default_row_glass_value")

    st.header("Weight & Cost Mapping")
    st.caption("Per-profile overrides. Use this to fix systems where workbook weights do not map correctly.")

inject_brand_css(primary, accent)

logo_b64 = uploaded_or_default_logo(logo_file)
logo_html = f'<img class="hero-logo" src="data:image/png;base64,{logo_b64}" />' if logo_b64 else ""

st.markdown(
    f"""
    <div class="brand-hero">
        <div class="hero-wrap">
            {logo_html}
            <div>
                <h1 class="hero-title">{project_name}</h1>
                <div class="hero-sub">Master craftsmanship planning for aluminium profiles, glass optimisation, offcuts, and export-ready jobcards.</div>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="soft-card" style="padding:14px 18px 12px 18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
            <div>
                <div style="font-size:1.05rem;font-weight:800;color:#0f172a;">Client: {client_name or '-'}</div>
                <div style="font-size:0.92rem;color:#475569;margin-top:4px;">Finish: {finish or '-'} &nbsp;|&nbsp; Autosaved project: <b>{st.session_state.active_project_name}</b></div>
            </div>
            <div style="background:#eff6ff;color:#1d4ed8;border:1px solid rgba(29,78,216,0.12);padding:6px 12px;border-radius:999px;font-size:0.8rem;font-weight:700;">
                PROJECT ACTIVE
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


with st.sidebar:
    if st.session_state.get("profile_cost_map"):
        edited_profile_map = st.data_editor(
            pd.DataFrame(st.session_state.get("profile_cost_map", [])),
            use_container_width=True,
            num_rows="fixed",
            key="profile_cost_map_editor",
            column_config={
                "profile": st.column_config.TextColumn("Profile", disabled=True),
                "weight_per_m_kg": st.column_config.NumberColumn("Weight / m (kg)", min_value=0.0, step=0.001),
                "cost_per_kg": st.column_config.NumberColumn("Cost / kg", min_value=0.0, step=0.01),
            },
        )
        st.session_state.profile_cost_map = edited_profile_map.fillna(0).to_dict("records")
    else:
        st.caption("Profile weight and cost overrides will appear here once profiles are calculated.")



tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs(["Project Board", "Window Entry", "Offcuts", "Results", "Visual Layouts", "Schedule & BOM"])


with tab0:
    st.markdown('<div class="section-title">Interactive Project Window Board</div>', unsafe_allow_html=True)
    st.caption("Review the whole project visually, open any window for editing, duplicate repeated windows, and spot the correct system at a glance.")

    b1, b2, b3, b4 = st.columns(4)
    total_qty = int(sum(int(w.get("window_qty", 1) or 1) for w in st.session_state.windows))
    families = len(set(get_window_family(variant_map.get(w.get("variant_key", default_variant), "")) for w in st.session_state.windows))
    with b1:
        render_metric("Window types", str(len(st.session_state.windows)))
    with b2:
        render_metric("Total units", str(total_qty))
    with b3:
        render_metric("System families", str(families))
    with b4:
        render_metric("Active project", st.session_state.active_project_name)

    selected_id = st.session_state.get("board_selected_window_id", st.session_state.windows[0]["id"] if st.session_state.windows else None)
    cols = st.columns(3)
    for i, window in enumerate(st.session_state.windows):
        col = cols[i % 3]
        variant_key = window.get("variant_key", default_variant)
        variant_label = variant_map.get(variant_key, variant_key)
        system_code = system_code_from_label(variant_label)
        card_svg = build_window_svg(window, variant_label, variant_key, compact=True)
        status = "Focused" if window.get("id") == selected_id else "Ready"
        with col:
            preview_b64 = preview_base64(variant_key)
            ref_img_html = f'<img class="preview-img" src="data:image/png;base64,{preview_b64}" style="max-height:180px;" />' if preview_b64 else '<div style="padding:28px 0;color:#64748b;text-align:center;">Reference image not available</div>'
            st.markdown(
                f"""
                <div class="board-card">
                    <div class="board-top">
                        <div>
                            <div class="board-label">{window.get('label','Window')}</div>
                            <div class="board-code">{system_code}</div>
                        </div>
                        <div class="board-status">{status}</div>
                    </div>
                    <div style="font-size:0.82rem;color:#475569;min-height:34px;">{variant_short_name(variant_label)}</div>
                    <div class="drawing-shell">{ref_img_html}</div>
                    <div style="font-size:0.74rem;color:#64748b;font-weight:700;margin-top:-4px;margin-bottom:6px;">Workbook reference</div>
                    <div class="board-meta">
                        <div class="board-kpi">
                            <div class="board-kpi-label">Quantity</div>
                            <div class="board-kpi-value">{int(window.get('window_qty',1) or 1)}</div>
                        </div>
                        <div class="board-kpi">
                            <div class="board-kpi-label">Glass</div>
                            <div class="board-kpi-value">{str(window.get('glass_spec','-'))[:18]}</div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("Open", key=f"board_open_{window['id']}", use_container_width=True):
                    st.session_state.board_selected_window_id = window["id"]
                    st.rerun()
            with c2:
                if st.button("Duplicate", key=f"board_dup_{window['id']}", use_container_width=True):
                    duplicate_window(window["id"])
                    st.session_state.board_selected_window_id = window["id"]
                    st.rerun()
            with c3:
                if len(st.session_state.windows) > 1 and st.button("Delete", key=f"board_del_{window['id']}", use_container_width=True):
                    remove_window(window["id"])
                    st.rerun()

    focused_window = next((w for w in st.session_state.windows if w.get("id") == selected_id), st.session_state.windows[0] if st.session_state.windows else None)
    if focused_window:
        f_variant_key = focused_window.get("variant_key", default_variant)
        f_variant_label = variant_map.get(f_variant_key, f_variant_key)
        required_fields = sorted(variant_lookup[f_variant_key].get("input_labels", []), key=lambda x: FIELD_ORDER.get(x, 999))
        summary_items = []
        for fld in required_fields:
            summary_items.append(f"<span class='mini-tag'>{fld}: {int(float(focused_window.get(fld,0) or 0))} mm</span>")
        st.markdown(
            f"""
            <div class="soft-card">
                <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;">
                    <div>
                        <div style="font-size:1.06rem;font-weight:800;color:#0f172a;">Focused window: {focused_window.get('label','Window')}</div>
                        <div style="font-size:0.9rem;color:#475569;">{system_code_from_label(f_variant_label)} · {variant_short_name(f_variant_label)}</div>
                    </div>
                    <div class="board-status">Open this same window in the Window Entry tab</div>
                </div>
                <div style="margin-top:10px;">{''.join(summary_items)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

with tab1:
    st.markdown('<div class="section-title">Window Calculator</div>', unsafe_allow_html=True)
    st.caption("Each selected system and orientation now shows only the input fields required for that exact variant.")
    focused_window = next((w for w in st.session_state.windows if w.get('id') == st.session_state.get('board_selected_window_id')), None)
    if focused_window:
        st.info(f"Focused from Project Board: {focused_window.get('label','Window')} — this card opens first below.")

    for idx, window in enumerate(st.session_state.windows):
        current_variant = window.get("variant_key", default_variant)
        current_variant_meta = variant_lookup[current_variant]
        current_required = sorted(current_variant_meta.get("input_labels", []), key=lambda x: FIELD_ORDER.get(x, 999))
        title = f"{window.get('label', f'W{idx+1}')} — {variant_map.get(current_variant, 'Typology')}"
        with st.expander(title, expanded=(window.get('id') == st.session_state.get('board_selected_window_id', st.session_state.windows[0]['id'] if st.session_state.windows else None))):
            form_col, preview_col = st.columns([2.4, 1])

            with form_col:
                c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
                with c1:
                    label = st.text_input("Label", value=window.get("label", f"W{idx+1}"), key=f"label_{window['id']}")
                with c2:
                    variant_key = st.selectbox(
                        "Typology / Orientation",
                        options=variant_keys,
                        index=variant_keys.index(current_variant),
                        format_func=lambda x: variant_map.get(x, x),
                        key=f"variant_{window['id']}",
                    )
                with c3:
                    qty = st.number_input("Qty", min_value=1, value=int(window.get("window_qty", 1)), step=1, key=f"qty_{window['id']}")
                with c4:
                    row_glass = st.selectbox(
                        "Glass Spec",
                        options=glass_specs,
                        index=glass_specs.index(window.get("glass_spec", default_row_glass)) if window.get("glass_spec", default_row_glass) in glass_specs else 0,
                        key=f"glass_{window['id']}",
                    )

                selected_variant_meta = variant_lookup[variant_key]
                required_fields = sorted(selected_variant_meta.get("input_labels", []), key=lambda x: FIELD_ORDER.get(x, 999))

                st.markdown("**Required inputs for this exact system:**", unsafe_allow_html=False)
                st.markdown("".join([f'<span class="mini-tag">{f}</span>' for f in required_fields]), unsafe_allow_html=True)

                if required_fields:
                    cols = st.columns(3)
                    for pos, field in enumerate(required_fields):
                        with cols[pos % 3]:
                            value = st.number_input(
                                f"{field} (mm)",
                                min_value=0.0,
                                value=float(window.get(field, 0.0)),
                                step=10.0,
                                key=f"{safe_name(field)}_{window['id']}",
                                help=FIELD_HELP.get(field, ""),
                            )
                            update_window_field(idx, field, value)

                for field in ALL_INPUT_FIELDS:
                    if field not in required_fields:
                        # keep hidden fields but do not force any value
                        update_window_field(idx, field, float(window.get(field, 0.0)))

                update_window_field(idx, "label", label)
                update_window_field(idx, "variant_key", variant_key)
                update_window_field(idx, "variant_label", variant_map.get(variant_key, variant_key))
                update_window_field(idx, "window_qty", qty)
                update_window_field(idx, "glass_spec", row_glass)

                action1, action2, action_spacer = st.columns([1, 1, 3])
                with action1:
                    if st.button("Duplicate window", key=f"dup_{window['id']}", use_container_width=True):
                        duplicate_window(window["id"])
                        st.rerun()
                with action2:
                    if len(st.session_state.windows) > 1 and st.button("Remove this window", key=f"remove_{window['id']}", use_container_width=True):
                        remove_window(window["id"])
                        st.rerun()

            with preview_col:
                selected_variant_label = variant_map.get(variant_key, variant_key)
                selected_code = system_code_from_label(selected_variant_label)
                preview_b64 = preview_base64(variant_key)
                variant_short = variant_short_name(selected_variant_label)
                drawing_svg = build_window_svg(st.session_state.windows[idx], selected_variant_label, variant_key, compact=False)
                img_html = f'<img class="preview-img" src="data:image/png;base64,{preview_b64}" style="max-height:280px;" />' if preview_b64 else '<div style="padding:30px 0;color:#64748b;">Reference image not available</div>'
                st.markdown(
                    f"""
                    <div class="preview-shell">
                        <div class="preview-code">System Code: {selected_code}</div>
                        <div class="preview-variant">{variant_short}</div>
                        <div class="preview-subhead">Workbook reference image</div>
                        {img_html}
                        <div class="preview-subhead">Generated drawing</div>
                        <div class="drawing-shell">{drawing_svg}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    add_col, spacer_col = st.columns([1.2, 4])
    with add_col:
        if st.button("Add window", use_container_width=True):
            add_window(default_variant, default_row_glass, variant_map)
            st.rerun()
    with spacer_col:
        st.caption("Add the next window from the bottom of the page so you do not need to scroll back up.")

with tab2:
    st.markdown('<div class="section-title">Stock & Offcuts</div>', unsafe_allow_html=True)
    a1, a2 = st.columns(2)
    with a1:
        top_a1, top_a2 = st.columns([2,1])
        with top_a1:
            st.caption("Aluminium offcuts")
            st.caption("Starts empty by default. Add only the aluminium profile offcuts you actually have.")
        with top_a2:
            if st.button("Clear aluminium", key="clear_al_offcuts", use_container_width=True):
                st.session_state.al_offcuts = []
                st.rerun()
        al_df = pd.DataFrame(st.session_state.al_offcuts or [{"profile": "", "length_mm": 0.0, "qty": 1}])
        al_edited = st.data_editor(
            al_df,
            use_container_width=True,
            num_rows="dynamic",
            key="al_offcuts_editor_variant",
            column_config={
                "profile": st.column_config.TextColumn("Profile"),
                "length_mm": st.column_config.NumberColumn("Length (mm)", min_value=0.0),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1),
            },
        )
        st.session_state.al_offcuts = al_edited.fillna("").to_dict("records")
    with a2:
        top_g1, top_g2, top_g3 = st.columns([2,1,1])
        with top_g1:
            st.caption("Glass offcuts")
            st.caption("Preloaded from the workbook by default. You can clear them or reload the workbook defaults.")
        with top_g2:
            if st.button("Clear glass", key="clear_glass_offcuts", use_container_width=True):
                st.session_state.glass_offcuts = []
                st.rerun()
        with top_g3:
            if st.button("Reload defaults", key="reload_glass_offcuts", use_container_width=True):
                st.session_state.glass_offcuts = get_default_glass_offcuts()[:60]
                st.rerun()
        glass_df = pd.DataFrame(st.session_state.glass_offcuts or [{"spec": default_row_glass, "width_mm": 0.0, "height_mm": 0.0, "qty": 1}])
        glass_edited = st.data_editor(
            glass_df,
            use_container_width=True,
            num_rows="dynamic",
            key="glass_offcuts_editor_variant",
            column_config={
                "spec": st.column_config.SelectboxColumn("Specification", options=glass_specs),
                "width_mm": st.column_config.NumberColumn("Width (mm)", min_value=0.0),
                "height_mm": st.column_config.NumberColumn("Height (mm)", min_value=0.0),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1),
            },
        )
        st.session_state.glass_offcuts = glass_edited.fillna("").to_dict("records")

profile_rows, glass_rows, warnings = expand_window_rows(st.session_state.windows, catalog, default_row_glass)
aluminium = optimise_aluminium(profile_rows, stock_length_mm, kerf_mm, st.session_state.al_offcuts)
glass = optimise_glass(glass_rows, glass_sheet_width_mm, glass_sheet_height_mm, kerf_mm, st.session_state.glass_offcuts)
summary = build_summary(st.session_state.windows, profile_rows, glass_rows, aluminium, glass, catalog.weights)

# Build live profile weight/cost map from actual calculated profiles
if "profile_cost_map" not in st.session_state:
    st.session_state.profile_cost_map = []
current_map_rows = build_profile_cost_map_rows(profile_rows, catalog.weights)
existing_map = {str(r.get("profile","")): r for r in (st.session_state.get("profile_cost_map", []) or [])}
for row in current_map_rows:
    if row["profile"] in existing_map:
        if float(existing_map[row["profile"]].get("weight_per_m_kg", 0) or 0) > 0:
            row["weight_per_m_kg"] = float(existing_map[row["profile"]]["weight_per_m_kg"])
        if float(existing_map[row["profile"]].get("cost_per_kg", 0) or 0) > 0:
            row["cost_per_kg"] = float(existing_map[row["profile"]]["cost_per_kg"])
st.session_state.profile_cost_map = current_map_rows

# Safer aluminium weight calculation using overrides first, then workbook mapping
profile_piece_df_for_weight = pd.DataFrame(profile_rows)
missing_weight_profiles = []
if not profile_piece_df_for_weight.empty and "profile" in profile_piece_df_for_weight.columns:
    def _row_weight_kg(row):
        profile = row.get("profile", "")
        weight_per_m = lookup_profile_weight_override(profile, catalog.weights)
        if not weight_per_m:
            if profile not in missing_weight_profiles:
                missing_weight_profiles.append(profile)
            return 0.0
        qty = float(row.get("qty", 1) or 1)
        length_mm = float(row.get("length_mm", 0) or 0)
        return (length_mm / 1000.0) * qty * float(weight_per_m)

    def _row_cost(row):
        profile = row.get("profile", "")
        weight_per_m = lookup_profile_weight_override(profile, catalog.weights)
        qty = float(row.get("qty", 1) or 1)
        length_mm = float(row.get("length_mm", 0) or 0)
        weight_kg = (length_mm / 1000.0) * qty * float(weight_per_m or 0)
        return weight_kg * float(lookup_profile_cost_per_kg(profile) or 0)

    calculated_aluminium_weight_kg = round(float(profile_piece_df_for_weight.apply(_row_weight_kg, axis=1).sum()), 2)
    aluminium_cost_total = round(float(profile_piece_df_for_weight.apply(_row_cost, axis=1).sum()), 2)
else:
    calculated_aluminium_weight_kg = 0.0
    aluminium_cost_total = 0.0

summary["estimated_weight_kg"] = calculated_aluminium_weight_kg
glass_cost_total = round(float(summary.get("total_glass_area_m2",0) or 0) * SQFT_PER_M2 * float(st.session_state.get("glass_cost_per_sqft_value",0) or 0),2)


active_name = (project_name or st.session_state.active_project_name or "Ali Fabrication Project").strip()
st.session_state.active_project_name = active_name
save_project_data(
    active_name,
    project_payload_from_state(
        active_name,
        client_name,
        finish,
        stock_length_mm,
        glass_sheet_width_mm,
        glass_sheet_height_mm,
        kerf_mm,
        default_row_glass,
    ),
)

bar_df = pd.DataFrame(aluminium["bars"])
if not bar_df.empty:
    order_breakdown = (
        bar_df.groupby("profile", dropna=False)
        .agg(
            bars_to_order=("bar_no", "count"),
            ordered_length_mm=("stock_length_mm", "sum"),
            used_length_mm=("used_mm", "sum"),
            waste_mm=("waste_mm", "sum"),
        )
        .reset_index()
    )
    order_breakdown["ordered_length_m"] = order_breakdown["ordered_length_mm"].apply(mm_to_m)
    order_breakdown["used_length_m"] = order_breakdown["used_length_mm"].apply(mm_to_m)
    order_breakdown["waste_m"] = order_breakdown["waste_mm"].apply(mm_to_m)
else:
    order_breakdown = pd.DataFrame(columns=["profile", "bars_to_order", "ordered_length_mm", "ordered_length_m", "used_length_mm", "used_length_m", "waste_mm", "waste_m"])

profile_piece_df = pd.DataFrame(profile_rows)
if not profile_piece_df.empty:
    profile_totals = (
        profile_piece_df.groupby("profile", dropna=False)
        .agg(total_cut_qty=("qty", "sum"), total_cut_length_mm=("length_mm", lambda s: float((s * profile_piece_df.loc[s.index, "qty"]).sum())))
        .reset_index()
    )
    profile_totals["total_cut_length_m"] = profile_totals["total_cut_length_mm"].apply(mm_to_m)
    profile_totals["weight_per_m_kg"] = profile_totals["profile"].apply(lambda p: lookup_profile_weight_override(p, catalog.weights))
    profile_totals["cost_per_kg"] = profile_totals["profile"].apply(lambda p: lookup_profile_cost_per_kg(p))
    profile_totals["estimated_weight_kg"] = profile_totals.apply(lambda r: round(float(r["total_cut_length_m"]) * float(r["weight_per_m_kg"] or 0), 2), axis=1)
    profile_totals["estimated_cost"] = profile_totals.apply(lambda r: round(float(r["estimated_weight_kg"] or 0) * float(r["cost_per_kg"] or 0), 2), axis=1)
else:
    profile_totals = pd.DataFrame(columns=["profile", "total_cut_qty", "total_cut_length_mm", "total_cut_length_m", "weight_per_m_kg", "estimated_weight_kg", "estimated_cost"])



with tab3:

    st.markdown('<div class="section-title">Project Summary</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_metric("Windows", str(summary["window_lines"]))
    with c2:
        render_metric("Profile cuts", str(summary["profile_cuts"]))
    with c3:
        render_metric("New aluminium bars", str(summary["aluminium_new_bars"]))
    with c4:
        render_metric("New glass sheets", str(summary["glass_new_sheets"]))

    # Derived profile ordering summary
    bars_df = pd.DataFrame(aluminium.get("bars", []) or [])
    if not bars_df.empty:
        profile_order_summary = (
            bars_df.groupby("profile", dropna=False)
            .agg(
                lengths_to_order=("bar_no", "count"),
                ordered_length_mm=("stock_length_mm", "sum"),
                used_length_mm=("used_mm", "sum"),
                waste_mm=("waste_mm", "sum"),
            )
            .reset_index()
        )
        profile_order_summary["ordered_length_m"] = profile_order_summary["ordered_length_mm"].apply(mm_to_m)
        profile_order_summary["used_length_m"] = profile_order_summary["used_length_mm"].apply(mm_to_m)
        profile_order_summary["waste_m"] = profile_order_summary["waste_mm"].apply(mm_to_m)
    else:
        profile_order_summary = pd.DataFrame(columns=["profile","lengths_to_order","ordered_length_mm","ordered_length_m","used_length_mm","used_length_m","waste_mm","waste_m"])

    aluminium_offcut_df = pd.DataFrame(aluminium.get("offcut_jobs", []) or [])
    glass_offcut_df = pd.DataFrame(glass.get("offcut_jobs", []) or [])

    if not aluminium_offcut_df.empty:
        aluminium_offcut_view = aluminium_offcut_df.copy()
        if "source_length_mm" in aluminium_offcut_view.columns:
            aluminium_offcut_view["source_length_m"] = aluminium_offcut_view["source_length_mm"].apply(mm_to_m)
        if "remaining_after_mm" in aluminium_offcut_view.columns:
            aluminium_offcut_view["remaining_after_m"] = aluminium_offcut_view["remaining_after_mm"].apply(mm_to_m)
    else:
        aluminium_offcut_view = pd.DataFrame()

    if not glass_offcut_df.empty:
        glass_offcut_view = glass_offcut_df.copy()
    else:
        glass_offcut_view = pd.DataFrame()

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Profiles</div>', unsafe_allow_html=True)

    p1, p2, p3 = st.columns(3)
    with p1:
        st.metric("Total profile length", f"{mm_to_m(summary['total_profile_length_mm'])} m")
    with p2:
        st.metric("Estimated aluminium weight", f"{round(float(summary['estimated_weight_kg'] or 0), 2)} kg")
    with p3:
        st.metric("Estimated aluminium purchase cost", f"{aluminium_cost_total:,.2f}")

    if missing_weight_profiles:
        st.warning("Some profiles do not have a matching weight mapping yet. Use the Weight & Cost Mapping editor in the sidebar to add or override them: " + ", ".join(missing_weight_profiles))

    st.markdown("**Profile cuts**")
    profile_df = pd.DataFrame(profile_rows)
    if not profile_df.empty:
        show_cols = [c for c in ["window_label", "profile", "length_mm", "qty", "cut_degree"] if c in profile_df.columns]
        st.dataframe(profile_df[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No profile cuts generated yet.")

    st.markdown("**Profile summary with weights and cost**")
    if not profile_totals.empty:
        show_cols = [c for c in ["profile", "total_cut_qty", "total_cut_length_mm", "total_cut_length_m", "weight_per_m_kg", "cost_per_kg", "estimated_weight_kg", "estimated_cost"] if c in profile_totals.columns]
        st.dataframe(
            profile_totals[show_cols],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No weighted profile summary available.")

    st.markdown("**Profile lengths to order**")
    st.caption("This is the summary of how many new stock lengths of each profile need to be purchased after aluminium offcut allocation.")
    if not profile_order_summary.empty:
        st.dataframe(
            profile_order_summary[["profile", "lengths_to_order", "ordered_length_mm", "ordered_length_m", "used_length_mm", "used_length_m", "waste_mm", "waste_m"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("All profile cuts were covered by aluminium offcuts. No new stock lengths are required.")

    st.markdown("**Profiles retrieved from offcuts**")
    st.caption("Shows which aluminium pieces were cut from offcuts and exactly which offcut length was used.")
    if not aluminium_offcut_view.empty:
        show_cols = [c for c in ["window_label", "profile", "length_mm", "source_offcut_id", "source_length_mm", "source_length_m", "remaining_after_mm", "remaining_after_m"] if c in aluminium_offcut_view.columns]
        st.dataframe(aluminium_offcut_view[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No aluminium pieces were retrieved from offcuts.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Glass</div>', unsafe_allow_html=True)

    g1, g2, g3 = st.columns(3)
    with g1:
        st.metric("Total glass area", f"{round(summary['total_glass_area_m2'], 2)} m²")
    with g2:
        st.metric("Glass offcut hits", str(summary["glass_offcut_hits"]))
    with g3:
        st.metric("Estimated glass purchase cost", f"{glass_cost_total:,.2f}")

    st.markdown("**Glass pieces**")
    glass_df = pd.DataFrame(glass_rows)
    if not glass_df.empty:
        show_cols = [c for c in ["window_label", "spec", "width_mm", "height_mm", "qty"] if c in glass_df.columns]
        st.dataframe(glass_df[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No glass pieces generated yet.")

    st.markdown("**Glass summary by specification**")
    if 'glass_summary' in locals() and not glass_summary.empty:
        st.dataframe(glass_summary, use_container_width=True, hide_index=True)
    else:
        st.info("No glass summary available.")

    st.markdown("**Glass retrieved from offcuts**")
    st.caption("Shows which glass pieces were cut from offcuts and which offcut size was used.")
    if not glass_offcut_view.empty:
        show_cols = [c for c in ["piece_id", "window_label", "spec", "width_mm", "height_mm", "source_offcut_id", "source_width_mm", "source_height_mm", "remaining_width_mm", "remaining_height_mm"] if c in glass_offcut_view.columns]
        st.dataframe(glass_offcut_view[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No glass pieces were retrieved from offcuts.")

    st.markdown("**Glass optimiser placements**")
    glass_opt_df = pd.DataFrame(glass.get("optimiser_jobs", []) or [])
    if not glass_opt_df.empty:
        view_cols = [c for c in ["piece_id", "window_label", "spec", "sheet_no", "x_mm", "y_mm", "placed_width_mm", "placed_height_mm", "rotated"] if c in glass_opt_df.columns]
        st.dataframe(glass_opt_df[view_cols], use_container_width=True, hide_index=True)
    else:
        st.info("All glass pieces were covered by glass offcuts.")

    unplaced_df = pd.DataFrame(glass.get("unplaced_jobs", []) or [])
    if not unplaced_df.empty:
        st.warning("Some glass pieces do not fit on the selected sheet size.")
        show_cols = [c for c in ["piece_id", "window_label", "spec", "width_mm", "height_mm", "reason"] if c in unplaced_df.columns]
        st.dataframe(unplaced_df[show_cols], use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Material Purchasing Summary</div>', unsafe_allow_html=True)
    s1, s2, s3 = st.columns(3)
    with s1:
        st.metric("Aluminium purchase cost", f"{aluminium_cost_total:,.2f}")
    with s2:
        st.metric("Glass purchase cost", f"{glass_cost_total:,.2f}")
    with s3:
        st.metric("Total material cost", f"{aluminium_cost_total + glass_cost_total:,.2f}")

    st.write(f"**Client:** {client_name or '-'}")
    st.write(f"**Finish:** {finish}")
    st.write(f"**Aluminium cost per kg:** {float(st.session_state.get('aluminium_cost_per_kg_value', 0.0) or 0):,.2f}")
    st.write(f"**Glass cost per sq ft:** {float(st.session_state.get('glass_cost_per_sqft_value', 0.0) or 0):,.2f}")
    st.markdown('</div>', unsafe_allow_html=True)


with tab4:
    st.markdown('<div class="section-title">Visual Layouts</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        render_aluminium_bar_layouts(aluminium)
        aluminium_offcut_df = pd.DataFrame(aluminium.get("offcut_jobs", []) or [])
        if not aluminium_offcut_df.empty:
            st.markdown("**Aluminium offcut cut diagrams**")
            st.caption("Each card shows the original offcut length, the piece cut from it, and the remainder.")
            for _, row in aluminium_offcut_df.iterrows():
                source_len = float(row.get("source_length_mm", 0) or 0)
                piece_len = float(row.get("length_mm", 0) or 0)
                rem_len = float(row.get("remaining_after_mm", max(source_len - piece_len, 0)) or 0)
                denom = max(source_len, 1.0)
                used_pct = max((piece_len / denom) * 100.0, 4.0)
                rem_pct = max((rem_len / denom) * 100.0, 4.0) if rem_len > 0 else 0
                st.markdown(
                    f"""
                    <div class="soft-card">
                        <div style="font-weight:800;color:#0f172a;margin-bottom:8px;">
                            {row.get('window_label','')} · {row.get('profile','')}
                        </div>
                        <div style="color:#475569;font-size:0.9rem;margin-bottom:8px;">
                            Source offcut: <b>{row.get('source_offcut_id','-')}</b> · Original length: <b>{int(source_len)} mm</b>
                        </div>
                        <div style="display:flex;width:100%;border-radius:14px;overflow:hidden;border:1px solid rgba(148,163,184,0.25);background:white;">
                            <div style="width:{used_pct:.3f}%;min-width:72px;height:74px;background:#dbeafe;display:flex;align-items:center;justify-content:center;text-align:center;font-size:11px;font-weight:700;color:#1e3a8a;padding:4px;line-height:1.1;">
                                Cut piece<br>{int(piece_len)} mm
                            </div>
                            {"<div style='width:"+f"{rem_pct:.3f}"+"%;min-width:56px;height:74px;background:#dcfce7;display:flex;align-items:center;justify-content:center;text-align:center;font-size:11px;font-weight:700;color:#166534;padding:4px;line-height:1.1;'>Remaining<br>"+str(int(rem_len))+" mm</div>" if rem_len > 0 else ""}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("No aluminium offcut pieces to visualize.")

    with c2:
        render_glass_sheet_layouts(glass, glass_sheet_width_mm, glass_sheet_height_mm)
        glass_offcut_df = pd.DataFrame(glass.get("offcut_jobs", []) or [])
        if not glass_offcut_df.empty:
            st.markdown("**Glass offcut cut diagrams**")
            st.caption("Each card shows the source glass offcut, the cut piece, and the remaining offcut area.")
            for _, row in glass_offcut_df.iterrows():
                src_w = float(row.get("source_width_mm", row.get("offcut_width_mm", 0)) or 0)
                src_h = float(row.get("source_height_mm", row.get("offcut_height_mm", 0)) or 0)
                cut_w = float(row.get("width_mm", row.get("placed_width_mm", 0)) or 0)
                cut_h = float(row.get("height_mm", row.get("placed_height_mm", 0)) or 0)
                rem_w = float(row.get("remaining_width_mm", max(src_w - cut_w, 0)) or 0)
                rem_h = float(row.get("remaining_height_mm", src_h) or 0)
                scale = min(300.0 / max(src_w, 1), 180.0 / max(src_h, 1))
                canvas_w = max(int(src_w * scale), 140)
                canvas_h = max(int(src_h * scale), 100)
                cut_box_w = max(int(cut_w * scale), 30)
                cut_box_h = max(int(cut_h * scale), 24)
                st.markdown(
                    f"""
                    <div class="soft-card">
                        <div style="font-weight:800;color:#0f172a;margin-bottom:8px;">
                            {row.get('window_label','')} · {row.get('piece_id','')}
                        </div>
                        <div style="color:#475569;font-size:0.9rem;margin-bottom:8px;">
                            Source offcut: <b>{row.get('source_offcut_id','-')}</b> · Original size: <b>{int(src_w)} × {int(src_h)} mm</b>
                        </div>
                        <div style="position:relative;width:{canvas_w}px;height:{canvas_h}px;border:2px solid #0f172a;border-radius:14px;background:linear-gradient(180deg,#ffffff,#f8fafc);overflow:hidden;">
                            <div style="position:absolute;left:0;top:0;width:{cut_box_w}px;height:{cut_box_h}px;background:rgba(15,118,110,0.18);border:2px solid #0f766e;border-radius:8px;box-sizing:border-box;display:flex;align-items:center;justify-content:center;text-align:center;font-size:10px;font-weight:700;color:#134e4a;line-height:1.05;">
                                Cut<br>{int(cut_w)} × {int(cut_h)}
                            </div>
                        </div>
                        <div style="margin-top:8px;color:#166534;font-size:0.88rem;font-weight:700;">
                            Remaining offcut: {int(rem_w)} × {int(rem_h)} mm
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("No glass offcut pieces to visualize.")





with tab5:
    st.markdown('<div class="section-title">Window Schedule & BOM Export</div>', unsafe_allow_html=True)
    st.caption("Use this tab for procurement, planning, and schedule review.")

    schedule_rows = []
    for w in st.session_state.windows:
        vkey = w.get("variant_key", default_variant)
        vlabel = variant_map.get(vkey, vkey)
        schedule_rows.append({
            "window_label": w.get("label", ""),
            "system_code": system_code_from_label(vlabel),
            "type": variant_short_name(vlabel),
            "width_mm": float(w.get("OVERALL WIDTH", 0) or 0),
            "height_mm": float(w.get("OVERALL HEIGHT", 0) or 0),
            "qty": int(w.get("window_qty", 1) or 1),
            "glass_spec": w.get("glass_spec", ""),
        })
    window_schedule_df = pd.DataFrame(schedule_rows)

    # Profiles BOM = purchase summary of new stock lengths required
    if 'order_breakdown' in locals() and isinstance(order_breakdown, pd.DataFrame) and not order_breakdown.empty:
        bom_profiles_df = order_breakdown.copy()
        if "bars_to_order" in bom_profiles_df.columns:
            bom_profiles_df["lengths_to_order"] = bom_profiles_df["bars_to_order"]
        if "ordered_length_m" not in bom_profiles_df.columns and "ordered_length_mm" in bom_profiles_df.columns:
            bom_profiles_df["ordered_length_m"] = bom_profiles_df["ordered_length_mm"].apply(mm_to_m)
        bom_profiles_df["weight_per_m_kg"] = bom_profiles_df["profile"].apply(lambda p: lookup_profile_weight_override(p, catalog.weights))
        bom_profiles_df["cost_per_kg"] = bom_profiles_df["profile"].apply(lambda p: lookup_profile_cost_per_kg(p))
        bom_profiles_df["estimated_weight_kg"] = bom_profiles_df.apply(
            lambda r: round(float(r.get("ordered_length_m", 0) or 0) * float(r.get("weight_per_m_kg", 0) or 0), 2),
            axis=1,
        )
        bom_profiles_df["estimated_cost"] = bom_profiles_df.apply(
            lambda r: round(float(r.get("estimated_weight_kg", 0) or 0) * float(r.get("cost_per_kg", 0) or 0), 2),
            axis=1,
        )
    else:
        bom_profiles_df = pd.DataFrame(columns=["profile", "lengths_to_order", "ordered_length_mm", "ordered_length_m", "used_length_mm", "used_length_m", "waste_mm", "waste_m", "weight_per_m_kg", "cost_per_kg", "estimated_weight_kg", "estimated_cost"])

    # Glass BOM = only pieces that need to come from new sheets after offcuts are removed
    glass_sheet_piece_df = pd.DataFrame(glass.get("optimiser_jobs", []) or [])
    if not glass_sheet_piece_df.empty:
        if "spec" not in glass_sheet_piece_df.columns:
            glass_sheet_piece_df["spec"] = ""
        glass_sheet_piece_df["cut_width_mm"] = glass_sheet_piece_df.get("placed_width_mm", glass_sheet_piece_df.get("width_mm", 0))
        glass_sheet_piece_df["cut_height_mm"] = glass_sheet_piece_df.get("placed_height_mm", glass_sheet_piece_df.get("height_mm", 0))
        glass_sheet_piece_df["area_m2_calc"] = (
            glass_sheet_piece_df["cut_width_mm"].fillna(0).astype(float)
            * glass_sheet_piece_df["cut_height_mm"].fillna(0).astype(float)
            / 1000000.0
        )
        bom_glass_df = glass_sheet_piece_df.copy()
        if "qty" not in bom_glass_df.columns:
            bom_glass_df["qty"] = 1
        bom_glass_summary_df = (
            bom_glass_df.groupby("spec", dropna=False)
            .agg(
                total_pieces=("qty", "sum"),
                total_area_m2=("area_m2_calc", "sum"),
            )
            .reset_index()
        )
        bom_glass_summary_df["estimated_cost"] = bom_glass_summary_df["total_area_m2"].apply(
            lambda x: round(float(x) * SQFT_PER_M2 * float(st.session_state.get("glass_cost_per_sqft_value", 0.0) or 0), 2)
        )
    else:
        bom_glass_df = pd.DataFrame(columns=["piece_id", "window_label", "spec", "sheet_no", "cut_width_mm", "cut_height_mm", "area_m2_calc"])
        bom_glass_summary_df = pd.DataFrame(columns=["spec", "total_pieces", "total_area_m2", "estimated_cost"])

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Window Schedule</div>', unsafe_allow_html=True)
    if not window_schedule_df.empty:
        st.dataframe(window_schedule_df, use_container_width=True, hide_index=True)
    else:
        st.info("No windows available for the schedule.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Bill of Materials — Profiles to Purchase</div>', unsafe_allow_html=True)
    st.caption("Shows the summary of new stock lengths required after aluminium offcuts have already been deducted.")
    if not bom_profiles_df.empty:
        show_cols = [c for c in ["profile", "lengths_to_order", "bars_to_order", "ordered_length_mm", "ordered_length_m", "weight_per_m_kg", "cost_per_kg", "estimated_weight_kg", "estimated_cost", "used_length_mm", "used_length_m", "waste_mm", "waste_m"] if c in bom_profiles_df.columns]
        st.dataframe(bom_profiles_df[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No profile purchase BOM available. All profiles may have been covered by offcuts.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Bill of Materials — Glass to Cut from New Sheets</div>', unsafe_allow_html=True)
    st.caption("Shows only the glass pieces that still need to be cut from new sheets after glass offcuts have been deducted.")
    if not bom_glass_df.empty:
        detail_cols = [c for c in ["piece_id", "window_label", "spec", "sheet_no", "cut_width_mm", "cut_height_mm", "area_m2_calc"] if c in bom_glass_df.columns]
        st.dataframe(bom_glass_df[detail_cols], use_container_width=True, hide_index=True)
        st.markdown("**Glass purchase summary**")
        st.dataframe(bom_glass_summary_df, use_container_width=True, hide_index=True)
    else:
        st.info("No glass needs to be cut from new sheets. All glass pieces were covered by offcuts.")
    st.markdown('</div>', unsafe_allow_html=True)

    total_profiles_cost = 0.0
    if "estimated_cost" in bom_profiles_df.columns and not bom_profiles_df.empty:
        total_profiles_cost = float(bom_profiles_df["estimated_cost"].fillna(0).sum())
    total_glass_cost = 0.0
    if "estimated_cost" in bom_glass_summary_df.columns and not bom_glass_summary_df.empty:
        total_glass_cost = float(bom_glass_summary_df["estimated_cost"].fillna(0).sum())

    st.markdown('<div class="soft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Schedule & BOM Export</div>', unsafe_allow_html=True)
    e1, e2, e3 = st.columns(3)
    with e1:
        st.metric("Profiles BOM cost", f"{total_profiles_cost:,.2f}")
    with e2:
        st.metric("Glass BOM cost", f"{total_glass_cost:,.2f}")
    with e3:
        st.metric("Combined BOM cost", f"{(total_profiles_cost + total_glass_cost):,.2f}")

    workbook_bytes = build_schedule_bom_workbook(window_schedule_df, bom_profiles_df, bom_glass_summary_df)
    st.download_button(
        "Download Schedule + BOM Excel",
        data=workbook_bytes,
        file_name=f"{project_name.strip().replace(' ', '_') or 'project'}_schedule_bom.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)

