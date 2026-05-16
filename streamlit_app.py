import re
from typing import Dict, List

import pandas as pd
import streamlit as st


def read_uploaded_file(uploaded_file) -> str:
    """Read uploaded PDF/TXT file and return extracted text."""
    if uploaded_file is None:
        raise ValueError("ファイルがアップロードされていません。")

    extension = uploaded_file.name.lower().split(".")[-1]

    if extension == "txt":
        try:
            return uploaded_file.read().decode("utf-8")
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            return uploaded_file.read().decode("cp932", errors="ignore")

    if extension == "pdf":
        # Mock behavior for initial version: no OCR/API, lightweight placeholder parsing.
        # We attempt to decode binary as text with ignore to keep this version dependency-free.
        raw = uploaded_file.read()
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            return "PDFのテキスト抽出はモック実装です。実データ解析にはTXTをご利用ください。"
        return text

    raise ValueError("対応していないファイル形式です。PDFまたはTXTをアップロードしてください。")


def _find_date(text: str) -> str:
    patterns = [
        r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{4}年\d{1,2}月\d{1,2}日\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return "未検出"


def _find_amount(text: str) -> str:
    patterns = [
        r"(?:¥|￥)\s?([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)",
        r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)\s?(?:円)",
        r"\b([0-9]{2,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return "未検出"


def _find_merchant(text: str) -> str:
    candidates = [
        r"取引先[:：]\s*(.+)",
        r"加盟店[:：]\s*(.+)",
        r"店舗名[:：]\s*(.+)",
    ]
    for pattern in candidates:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return lines[0][:40]
    return "未検出"


def extract_fields(text: str, selected_fields: List[str]) -> List[Dict]:
    """Mock extraction logic returning list of {field, value, confidence}."""
    results: List[Dict] = []

    for field in selected_fields:
        if field == "日付":
            value = _find_date(text)
            confidence = 0.90 if value != "未検出" else 0.35
        elif field == "金額":
            value = _find_amount(text)
            confidence = 0.88 if value != "未検出" else 0.30
        elif field == "取引先名":
            value = _find_merchant(text)
            confidence = 0.85 if value != "未検出" else 0.40
        else:
            value = "未対応フィールド"
            confidence = 0.0

        results.append({"field": field, "value": value, "confidence": round(confidence, 2)})

    return results


def summarize_results(results: List[Dict]) -> str:
    """Create a concise Japanese summary string from extracted results."""
    if not results:
        return "抽出結果がないため、要約できませんでした。"

    parts = []
    low_conf = []
    for item in results:
        parts.append(f"{item['field']}は『{item['value']}』(信頼度 {item['confidence']})")
        if item["confidence"] < 0.5:
            low_conf.append(item["field"])

    summary = "、".join(parts) + "。"
    if low_conf:
        summary += f" ただし {', '.join(low_conf)} は信頼度が低いため確認を推奨します。"
    return summary


def main() -> None:
    st.title("テキスト抽出＆要約アシスタント")

    with st.sidebar:
        uploaded_file = st.file_uploader("ファイルをアップロード", type=["pdf", "txt"])
        selected_fields = st.multiselect("抽出対象フィールド", ["日付", "金額", "取引先名"])
        run_button = st.button("解析実行")

    if uploaded_file:
        st.success(f"アップロード済み: {uploaded_file.name}")
    else:
        st.info("ファイル未アップロード")

    if run_button:
        if uploaded_file is None:
            st.warning("先にPDFまたはTXTファイルをアップロードしてください。")
            return
        if not selected_fields:
            st.warning("抽出対象フィールドを1つ以上選択してください。")
            return

        try:
            text = read_uploaded_file(uploaded_file)
            results = extract_fields(text, selected_fields)
            summary = summarize_results(results)

            st.subheader("抽出結果")
            df = pd.DataFrame(results, columns=["field", "value", "confidence"])
            st.dataframe(df, use_container_width=True)

            st.subheader("要約")
            st.write(summary)

        except ValueError as e:
            st.warning(str(e))
        except Exception as e:
            st.error(f"解析中に予期しないエラーが発生しました: {e}")


if __name__ == "__main__":
    main()
