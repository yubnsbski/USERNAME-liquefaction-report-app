from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import streamlit as st

Category = str

MERCHANT_SCORE = 80
ITEM_SCORE = 50
SMALL_MARGIN = 20

MERCHANT_RULES: Dict[str, Category] = {
    "セブンイレブン": "食費",
    "ファミリーマート": "食費",
    "ローソン": "食費",
    "マツモトキヨシ": "日用品",
    "ウエルシア": "日用品",
    "ENEOS": "交通",
    "JR東日本": "交通",
}

AMBIGUOUS_MERCHANTS: List[str] = [
    "Amazon",
    "楽天",
    "イオン",
    "ドン・キホーテ",
    "ドンキホーテ",
    "メルカリ",
]

DEFAULT_OUTPUT_DESTINATIONS: Dict[Category, str] = {
    "食費": "家計簿/食費",
    "日用品": "家計簿/日用品",
    "交通": "家計簿/交通",
    "医療": "家計簿/医療",
    "教育": "家計簿/教育",
    "要確認": "家計簿/要確認",
}

ITEM_KEYWORD_RULES: Dict[str, Category] = {
    "おにぎり": "食費",
    "弁当": "食費",
    "牛乳": "食費",
    "洗剤": "日用品",
    "シャンプー": "日用品",
    "薬": "医療",
    "ガソリン": "交通",
    "本": "教育",
}


@dataclass(frozen=True)
class CategoryScore:
    category: Category
    score: int


@dataclass(frozen=True)
class ClassificationResult:
    merchant_normalized: str
    category: Optional[Category]
    confidence: float
    needs_review: bool
    reason: str
    reasons: List[str]
    scores: List[CategoryScore]


def normalize_merchant(raw: str) -> str:
    text = raw.strip().replace(" ", "").replace("\t", "").replace("\n", "")
    return (
        text.replace("ｾﾌﾞﾝ-ｲﾚﾌﾞﾝ", "セブンイレブン")
        .replace("セブン-イレブン", "セブンイレブン")
        .replace("セブンーイレブン", "セブンイレブン")
        .replace("ファミマ", "ファミリーマート")
        .replace("ﾏﾂｷﾖ", "マツモトキヨシ")
        .replace("ドン・キホーテ", "ドンキホーテ")
    )


def _to_sorted_scores(score_map: Dict[Category, int]) -> List[CategoryScore]:
    scores = [CategoryScore(category=k, score=v) for k, v in score_map.items() if v > 0]
    return sorted(scores, key=lambda entry: entry.score, reverse=True)


def classify_receipt(
    merchant_raw: str,
    items: Optional[List[str]] = None,
) -> ClassificationResult:
    merchant_normalized = normalize_merchant(merchant_raw)
    item_list = items or []
    reasons: List[str] = []
    score_map: Dict[Category, int] = {}

    for merchant in AMBIGUOUS_MERCHANTS:
        if merchant in merchant_normalized and len(item_list) == 0:
            return ClassificationResult(
                merchant_normalized=merchant_normalized,
                category=None,
                confidence=0.3,
                needs_review=True,
                reason=f"ambiguous merchant: {merchant}",
                reasons=[f"ambiguous_merchant_no_items: {merchant}"],
                scores=[],
            )

    for merchant, category in MERCHANT_RULES.items():
        if merchant in merchant_normalized:
            score_map[category] = score_map.get(category, 0) + MERCHANT_SCORE
            reasons.append(f"merchant_rule: {merchant}")

    for item in item_list:
        for keyword, category in ITEM_KEYWORD_RULES.items():
            if keyword in item:
                score_map[category] = score_map.get(category, 0) + ITEM_SCORE
                reasons.append(f"item_keyword: {keyword}")

    scores = _to_sorted_scores(score_map)
    if not scores:
        return ClassificationResult(
            merchant_normalized=merchant_normalized,
            category=None,
            confidence=0.1,
            needs_review=True,
            reason="no rule matched",
            reasons=["no_rule_matched"],
            scores=[],
        )

    best = scores[0]
    second = scores[1] if len(scores) > 1 else None
    total_score = sum(entry.score for entry in scores)
    confidence = min(0.99, round(best.score / total_score, 2))

    is_ambiguous_top = any(m in merchant_normalized for m in AMBIGUOUS_MERCHANTS)
    has_small_margin = (best.score - second.score) < SMALL_MARGIN if second else False
    needs_review = is_ambiguous_top or has_small_margin

    if needs_review:
        reason = "ambiguous merchant requires review" if is_ambiguous_top else "score margin too small"
        category: Optional[Category] = None
    else:
        reason = f"score_winner: {best.category}"
        category = best.category

    return ClassificationResult(
        merchant_normalized=merchant_normalized,
        category=category,
        confidence=confidence,
        needs_review=needs_review,
        reason=reason,
        reasons=reasons,
        scores=scores,
    )


def parse_items(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_output_destination_editor() -> Tuple[Dict[str, str], Dict[str, str]]:
    st.sidebar.subheader("カテゴリ別の出力先設定")
    st.sidebar.caption("カテゴリごとの保存先と出力ラベルを編集できます。")

    all_categories = sorted(set(MERCHANT_RULES.values()) | set(ITEM_KEYWORD_RULES.values()) | {"要確認"})
    configured_destinations: Dict[str, str] = {}

    for category in all_categories:
        default_value = DEFAULT_OUTPUT_DESTINATIONS.get(category, f"家計簿/{category}")
        configured_destinations[category] = st.sidebar.text_input(
            category,
            value=default_value,
            key=f"destination_{category}",
        )

    st.sidebar.markdown("---")
    st.sidebar.caption("出力フィールド名")

    configured_prefixes = {
        "date": st.sidebar.text_input("日付ラベル", value="date", key="prefix_date"),
        "amount": st.sidebar.text_input("金額ラベル", value="amount", key="prefix_amount"),
        "merchant": st.sidebar.text_input("取引先ラベル", value="merchant", key="prefix_merchant"),
    }

    return configured_destinations, configured_prefixes


def main() -> None:
    st.set_page_config(page_title="家計簿レシート分類", layout="wide")
    st.title("家計簿レシート分類アシスタント")
    st.caption("店舗名と明細からカテゴリを推定し、カテゴリ別の出力先へ整形します。")

    output_destinations, output_prefixes = build_output_destination_editor()

    with st.expander("分類ルールを見る", expanded=False):
        left, right = st.columns(2)

        with left:
            st.markdown("**店舗ルール**")
            st.dataframe(
                [{"店舗": k, "カテゴリ": v} for k, v in MERCHANT_RULES.items()],
                hide_index=True,
                use_container_width=True,
            )

            st.markdown("**曖昧店舗**")
            st.dataframe(
                [{"店舗": x} for x in AMBIGUOUS_MERCHANTS],
                hide_index=True,
                use_container_width=True,
            )

        with right:
            st.markdown("**明細キーワード**")
            st.dataframe(
                [{"キーワード": k, "カテゴリ": v} for k, v in ITEM_KEYWORD_RULES.items()],
                hide_index=True,
                use_container_width=True,
            )

    with st.form("receipt_form"):
        merchant_raw = st.text_input("店舗名", "ｾﾌﾞﾝ-ｲﾚﾌﾞﾝ 渋谷店")
        items_text = st.text_area("明細（1行1品目）", "おにぎり\n牛乳", height=120)
        submitted = st.form_submit_button("解析する", use_container_width=True)

    if not submitted:
        st.info("入力後に「解析する」を押してください。")
        return

    result = classify_receipt(merchant_raw=merchant_raw, items=parse_items(items_text))
    assigned_category = result.category or "要確認"
    output_destination = output_destinations.get(assigned_category, f"家計簿/{assigned_category}")

    c1, c2, c3 = st.columns(3)
    c1.metric("カテゴリ", assigned_category)
    c2.metric("信頼度", f"{result.confidence:.2f}")
    c3.metric("レビュー", "必要" if result.needs_review else "不要")

    tab1, tab2, tab3 = st.tabs(["出力プレビュー", "分類詳細", "スコア"])

    with tab1:
        formatted_output = {
            output_prefixes["date"]: date.today().isoformat(),
            output_prefixes["amount"]: "(入力データから抽出する想定)",
            output_prefixes["merchant"]: result.merchant_normalized,
            "category": assigned_category,
            "output_destination": output_destination,
        }

        st.success(f"保存先: {output_destination}")
        st.json(formatted_output, expanded=True)

    with tab2:
        st.json(
            {
                "merchant_normalized": result.merchant_normalized,
                "category": result.category,
                "confidence": result.confidence,
                "needs_review": result.needs_review,
                "reason": result.reason,
                "reasons": result.reasons,
                "output_destination": output_destination,
            },
            expanded=False,
        )

    with tab3:
        if result.scores:
            st.dataframe(
                [{"カテゴリ": s.category, "スコア": s.score} for s in result.scores],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.warning("スコアなし")


if __name__ == "__main__":
    main()
