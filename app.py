import io
import tempfile
import base64
from datetime import date

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from fpdf import FPDF

# ============ 基本設定 ============
st.set_page_config(page_title="Liquefaction Report Generator", layout="wide")

# ============ ユーティリティ ============
def compute_risk_level(fl: float) -> str:
    if pd.isna(fl):
        return "Unknown"
    if fl < 0.75:
        return "High"
    elif fl < 1.0:
        return "Moderate"
    else:
        return "Low"

def suggest_foundation(fl: float, ground_type: str) -> str:
    if pd.isna(fl):
        return "Review required"
    gt = (ground_type or "").lower()
    if fl < 0.75:
        return "Pile foundation + Ground improvement"
    elif fl < 1.0:
        return "Raft foundation + Partial improvement"
    else:
        return "Spread/Strip foundation" if "soft" not in gt else "Raft foundation + Monitoring"

def fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def save_bytes_to_tmp_png(data: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.write(data)
    tmp.flush()
    return tmp.name

# ============ 可視化（図表） ============
def plot_fl_bar(df: pd.DataFrame) -> bytes:
    ids = df["id"].astype(str).tolist()
    fls = df["FL"].astype(float).tolist()
    colors = ["#d62728" if x < 1.0 else "#2ca02c" for x in fls]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(ids, fls, color=colors)
    ax.axhline(1.0, color="gray", linestyle="--", linewidth=1)
    ax.set_title("FL value by site")
    ax.set_ylabel("FL")
    ax.set_xlabel("Site ID")
    ax.set_ylim(0, max(1.2, max(fls) + 0.1))
    return fig_to_png_bytes(fig)

def plot_locations_scatter(df: pd.DataFrame) -> bytes:
    # lat/lon が無い場合は簡易図にフォールバック
    if not set(["lat", "lon"]).issubset(df.columns):
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "No coordinates provided", ha="center", va="center", fontsize=14)
        ax.axis("off")
        return fig_to_png_bytes(fig)

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(df["lon"], df["lat"], c="#1f77b4")
    for _, r in df.iterrows():
        ax.annotate(str(r["id"]), (r["lon"], r["lat"]), xytext=(3, 3), textcoords="offset points", fontsize=8)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Site locations (scatter)")
    ax.grid(True, alpha=0.3)
    return fig_to_png_bytes(fig)

# ============ PDF Generation (fpdf2) ============
# Use built-in Helvetica font for reliable PDF generation without external font files
PDF_FONT = "Helvetica"


class Booklet(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font(PDF_FONT, size=9)
        self.set_text_color(120)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def add_cover(pdf: Booklet, project: str, author: str, report_date: str):
    pdf.add_page()
    pdf.set_font(PDF_FONT, "B", 24)
    pdf.ln(40)
    pdf.cell(0, 12, "Liquefaction Risk Evaluation Report", ln=1, align="C")
    pdf.set_font(PDF_FONT, size=16)
    pdf.ln(4)
    pdf.cell(0, 10, project, ln=1, align="C")
    pdf.ln(10)
    pdf.set_font(PDF_FONT, size=12)
    pdf.cell(0, 8, f"Prepared by: {author}", ln=1, align="C")
    pdf.cell(0, 8, f"Date: {report_date}", ln=1, align="C")


def add_toc(pdf: Booklet, df: pd.DataFrame):
    pdf.add_page()
    pdf.set_font(PDF_FONT, "B", 16)
    pdf.cell(0, 10, "Table of Contents", ln=1)
    pdf.ln(2)
    pdf.set_font(PDF_FONT, size=12)
    for i, r in enumerate(df.itertuples(), start=1):
        line = f"{i}. {r.id} - {r.ground_type} - FL={r.FL}"
        pdf.cell(0, 8, line, ln=1)


def add_overview_charts(pdf: Booklet, bar_png_path: str, loc_png_path: str):
    pdf.add_page()
    pdf.set_font(PDF_FONT, "B", 14)
    pdf.cell(0, 8, "Overview", ln=1)
    pdf.ln(2)
    y_start = pdf.get_y()
    pdf.image(bar_png_path, x=10, y=y_start, w=90)
    pdf.image(loc_png_path, x=110, y=y_start, w=90)
    pdf.ln(100)


def add_site_pages(pdf: Booklet, df: pd.DataFrame):
    for r in df.itertuples():
        pdf.add_page()
        pdf.set_font(PDF_FONT, "B", 14)
        pdf.cell(0, 8, f"Site ID: {r.id}", ln=1)
        pdf.set_font(PDF_FONT, size=12)
        if "lat" in df.columns and "lon" in df.columns:
            pdf.cell(0, 7, f"Coordinates: {r.lat}, {r.lon}", ln=1)
        pdf.cell(0, 7, f"Ground Type: {r.ground_type}", ln=1)
        pdf.cell(0, 7, f"Corrected FL Value: {r.FL}", ln=1)
        pdf.cell(0, 7, f"Risk Level: {r.risk_level}", ln=1)
        pdf.multi_cell(0, 7, f"Design Recommendation: {r.suggestion}", align="L")
        pdf.ln(2)
        pdf.set_font(PDF_FONT, "I", 11)
        note = getattr(r, "note", "")
        if pd.notna(note) and str(note).strip():
            pdf.multi_cell(0, 6, f"Remarks: {note}")


def build_pdf_booklet(df: pd.DataFrame, project: str, author: str, report_date: str) -> bytes:
    if df.empty:
        raise ValueError("Cannot generate report: No site data provided")

    bar_png = save_bytes_to_tmp_png(plot_fl_bar(df))
    loc_png = save_bytes_to_tmp_png(plot_locations_scatter(df))

    pdf = Booklet(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    add_cover(pdf, project, author, report_date)
    add_toc(pdf, df)
    add_overview_charts(pdf, bar_png, loc_png)
    add_site_pages(pdf, df)

    return bytes(pdf.output())

# ============ データ準備（サンプル or アップロード） ============
def get_sample_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"id": "A01", "lat": 35.6895, "lon": 139.6917, "FL": 0.81, "ground_type": "Soft ground"},
        {"id": "A02", "lat": 35.6900, "lon": 139.6920, "FL": 1.15, "ground_type": "Medium"},
        {"id": "A03", "lat": 35.6880, "lon": 139.6900, "FL": 0.95, "ground_type": "Soft ground"},
    ])

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    # 必須列が無ければ追加
    for col in ["id", "FL", "ground_type"]:
        if col not in df.columns:
            df[col] = None
    # 型整形
    if "FL" in df.columns:
        df["FL"] = pd.to_numeric(df["FL"], errors="coerce")
    # リスク・提案を自動算出（上書き可能）
    df["risk_level"] = df["FL"].apply(compute_risk_level)
    df["suggestion"] = df.apply(lambda r: suggest_foundation(r["FL"], r.get("ground_type", "")), axis=1)
    if "note" not in df.columns:
        df["note"] = ""
    return df

# ============ UI ============
st.title("📘 Liquefaction Report Generator")

with st.sidebar:
    st.markdown("### Project settings")
    project_name = st.text_input("Project name", "Funabashi City – 2025")
    author = st.text_input("Prepared by", "Yuki")
    report_date = st.text_input("Date", date.today().isoformat())
    st.markdown("---")
    st.markdown("### CSV format")
    st.caption("Required columns: id, FL, ground_type. Optional: lat, lon, note.")

uploaded = st.file_uploader("Upload CSV (UTF-8)", type=["csv"])
if uploaded:
    df = pd.read_csv(uploaded)
else:
    st.info("No CSV uploaded. Using sample data.")
    df = get_sample_df()

df = ensure_columns(df)

st.markdown("### ✏️ Edit data")
edited = st.data_editor(df, num_rows="dynamic")
# 型再整形（編集後）
edited["FL"] = pd.to_numeric(edited["FL"], errors="coerce")
edited["risk_level"] = edited["FL"].apply(compute_risk_level)
edited["suggestion"] = edited.apply(lambda r: suggest_foundation(r["FL"], r.get("ground_type", "")), axis=1)

st.markdown("### 📊 Preview charts")
col1, col2 = st.columns(2)
with col1:
    st.image(plot_fl_bar(edited), caption="FL value by site")
with col2:
    st.image(plot_locations_scatter(edited), caption="Site locations (scatter)")

st.markdown("---")
if st.button("📄 Generate PDF booklet"):
    try:
        pdf_bytes = build_pdf_booklet(edited, project_name, author, report_date)
        st.success("PDF generated.")
        st.download_button(
            "📥 Download report",
            data=pdf_bytes,
            file_name="Liquefaction_Report_Booklet.pdf",
            mime="application/pdf",
        )
    except Exception as e:
        st.error(f"PDF generation failed: {e}")
