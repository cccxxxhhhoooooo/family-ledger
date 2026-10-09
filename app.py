"""家庭记账与消费分析看板。"""

from __future__ import annotations

from datetime import date
from typing import Final

import pandas as pd
import plotly.express as px
import streamlit as st  # pyright: ignore[reportMissingImports]

from voice import SpokenBill, listen_for_speech, parse_bill_speech
from utils import (
    ID_COLUMN,
    BillStoreError,
    add_bill,
    delete_bill,
    export_bills_to_excel,
    load_bills,
    storage_caption,
)

# 常用支出类别，表单与汇总共用
CATEGORIES: Final[list[str]] = [
    "餐饮",
    "交通",
    "购物",
    "住房",
    "娱乐",
    "医疗",
    "教育",
    "其他",
]

# 家庭成员。云端表的 owner 约束要和这里保持一致
OWNERS: Final[list[str]] = ["老公", "美女"]
SCOPE_ALL: Final[str] = "全家"
APP_PASSWORD: Final[str] = "525"


def inject_styles() -> None:
    """注入简洁的现代 Dashboard 样式。"""
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.6rem; padding-bottom: 2rem; }
        [data-testid="stSidebar"] { background: #f7f8fa; }
        .metric-card {
            background: #ffffff;
            border: 1px solid #e6e8ee;
            border-radius: 14px;
            padding: 1rem 1.1rem 0.85rem;
            box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
        }
        .metric-label { color: #667085; font-size: 0.85rem; margin-bottom: 0.25rem; }
        .metric-value { color: #101828; font-size: 1.6rem; font-weight: 650; line-height: 1.2; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def require_login() -> None:
    """未输入正确密码时停在登录页，不加载账单。"""
    if st.session_state.get("authenticated"):
        return

    st.title("家庭记账")
    st.caption("请输入密码后进入。")
    with st.form("login_form"):
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("进入", width="stretch")

    if submitted:
        # 密码不对就留在登录页，不继续渲染账本
        if str(password) == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        st.error("密码不正确。")
    st.stop()


def render_sidebar() -> str:
    """渲染使用者、查看范围和新增账单表单，返回当前查看范围。"""
    st.sidebar.header("家庭成员")
    # 选项改过之后，清掉会话里已经不存在的旧人选，避免下拉框报错
    if "current_owner" in st.session_state and st.session_state.current_owner not in OWNERS:
        st.session_state.current_owner = OWNERS[0]
    if "bill_scope" in st.session_state and st.session_state.bill_scope not in (SCOPE_ALL, *OWNERS):
        st.session_state.bill_scope = SCOPE_ALL
    st.sidebar.selectbox("当前使用者", OWNERS, key="current_owner")
    st.sidebar.caption("新账单会记在这个人名下。")
    scope = st.sidebar.selectbox("查看范围", [SCOPE_ALL, *OWNERS], key="bill_scope")

    st.sidebar.header("新增账单")
    # rerun 会清掉当次提示，所以用会话状态把保存结果带到下一轮
    if st.session_state.get("bill_saved"):
        st.sidebar.success("账单已保存。")
        st.session_state.bill_saved = False

    with st.sidebar.form("add_bill_form", clear_on_submit=True):
        bill_date = st.date_input("日期", value=date.today())
        category = st.selectbox("类别", CATEGORIES)
        amount = st.number_input("金额", min_value=0.0, step=1.0, format="%.2f")
        note = st.text_input("备注", placeholder="可选，例如：午餐")
        submitted = st.form_submit_button("保存账单", width="stretch")

    if submitted:
        # 金额为 0 时不落库，避免空记录污染统计
        if float(amount) <= 0:
            st.sidebar.error("金额必须大于 0。")
        else:
            try:
                add_bill(
                    bill_date=bill_date.isoformat(),
                    category=str(category),
                    amount=float(amount),
                    note=str(note),
                    owner=str(st.session_state.get("current_owner", OWNERS[0])),
                )
            except BillStoreError as exc:
                st.sidebar.error(str(exc))
            else:
                st.session_state.bill_saved = True
                st.rerun()
    return str(scope)


def render_voice_panel() -> None:
    """手机主页面上的语音记账。说完后确认保存，不用手动填写。"""
    st.subheader("语音记账")
    st.caption("先点输入框，再点键盘上的麦克风说话。例如：老公今天餐饮三十五块，午饭。")
    if st.session_state.get("voice_saved"):
        st.success("语音账单已保存。")
        st.session_state.voice_saved = False

    # 苹果手机和国内网络打不开网页语音，键盘麦克风用的是手机自己的识别
    st.text_input("键盘麦克风", placeholder="点这里，再点键盘上的麦克风", key="voice_typed")
    if st.button("识别这句话", width="stretch"):
        typed = str(st.session_state.get("voice_typed", "")).strip()
        if not typed:
            st.warning("还没有文字。请先点输入框，再用键盘上的麦克风说话。")
        else:
            st.session_state.voice_draft = parse_bill_speech(
                typed,
                categories=list(CATEGORIES),
                owners=list(OWNERS),
            )

    st.caption("安卓 Chrome 也可以直接点下面的黑按钮。苹果手机会一点就失败，请用上面的输入框。")
    heard = listen_for_speech()
    if heard is not None and heard["id"] != st.session_state.get("voice_result_id"):
        st.session_state.voice_result_id = heard["id"]
        st.session_state.voice_draft = parse_bill_speech(
            heard["text"],
            categories=list(CATEGORIES),
            owners=list(OWNERS),
        )

    draft = st.session_state.get("voice_draft")
    if not isinstance(draft, SpokenBill):
        return

    owner = draft.owner or str(st.session_state.get("current_owner", OWNERS[0]))
    st.write(
        f"听到：{draft.raw_text}",
    )
    if draft.amount is None or draft.amount <= 0:
        st.warning("没有听清金额，请再说一次，例如：餐饮三十五块。")
        return

    note = draft.note or "无"
    st.info(
        f"{draft.bill_date.isoformat()} · {owner} · {draft.category} · "
        f"{draft.amount:,.2f}元 · {note}"
    )
    if not st.button("保存这条语音账单", type="primary", width="stretch"):
        return

    try:
        add_bill(
            bill_date=draft.bill_date.isoformat(),
            category=draft.category,
            amount=float(draft.amount),
            note=draft.note,
            owner=owner,
        )
    except BillStoreError as exc:
        st.error(str(exc))
        return
    st.session_state.voice_draft = None
    st.session_state.voice_saved = True
    st.rerun()


def filter_bills(bills: pd.DataFrame, scope: str) -> pd.DataFrame:
    """按查看范围筛出全家或某一个使用者的账单。"""
    if bills.empty or scope == SCOPE_ALL:
        return bills
    return bills.loc[bills["使用者"] == scope].reset_index(drop=True)


def render_export(bills: pd.DataFrame, scope: str) -> None:
    """提供当前查看范围的 Excel 下载。没有记录时按钮不可用。"""
    st.sidebar.divider()
    st.sidebar.subheader("导出数据")
    scope_label = "" if scope == SCOPE_ALL else f"_{scope}"
    st.sidebar.download_button(
        "导出 Excel",
        data=export_bills_to_excel(bills),
        file_name=f"账单{scope_label}_{date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=bills.empty,
        width="stretch",
    )
    if bills.empty:
        st.sidebar.caption("暂无账单，无法导出。")


def calc_month_total(bills: pd.DataFrame, today: date) -> float:
    """统计指定日期所在月份的消费合计。"""
    if bills.empty:
        return 0.0

    dates = pd.to_datetime(bills["日期"], errors="coerce")
    in_month = (dates.dt.year == today.year) & (dates.dt.month == today.month)
    return float(bills.loc[in_month, "金额"].sum())


def render_metric_cards(bills: pd.DataFrame) -> None:
    """展示总消费、笔数、类别数和本月消费。"""
    total = float(bills["金额"].sum()) if not bills.empty else 0.0
    count = int(len(bills))
    category_count = int(bills["类别"].nunique()) if not bills.empty else 0
    month_total = calc_month_total(bills, date.today())

    cards = [
        ("总消费", f"{total:,.2f}元"),
        ("账单笔数", f"{count}"),
        ("消费类别", f"{category_count}"),
        ("本月消费", f"{month_total:,.2f}元"),
    ]
    columns = st.columns(4)
    for column, (label, value) in zip(columns, cards):
        column.markdown(
            (
                '<div class="metric-card">'
                f'<div class="metric-label">{label}</div>'
                f'<div class="metric-value">{value}</div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def render_pie_chart(bills: pd.DataFrame) -> None:
    """按类别汇总金额，并绘制 Plotly 环形图。"""
    st.subheader("类别占比")
    if bills.empty:
        st.info("还没有账单。在左侧添加一笔后，这里会显示类别占比。")
        return

    # 同一类别合并金额，饼图只表达占比
    summary = (
        bills.groupby("类别", as_index=False)["金额"]
        .sum()
        .sort_values("金额", ascending=False)
    )
    figure = px.pie(
        summary,
        names="类别",
        values="金额",
        hole=0.46,
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    figure.update_traces(textposition="inside", textinfo="percent+label")
    figure.update_layout(
        margin={"t": 12, "b": 12, "l": 12, "r": 12},
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend_title_text="类别",
    )
    st.plotly_chart(figure, width="stretch")


def sort_bills(bills: pd.DataFrame) -> pd.DataFrame:
    """按日期从新到旧排列，供明细和删除列表共用。"""
    ordered = bills.copy()
    ordered["_排序日期"] = pd.to_datetime(ordered["日期"], errors="coerce")
    return (
        ordered.sort_values("_排序日期", ascending=False)
        .drop(columns="_排序日期")
        .reset_index(drop=True)
    )


def bill_option_labels(bills: pd.DataFrame) -> dict[str, str]:
    """把编号映射成可读标签。内容完全相同的记录加上序号以便区分。"""
    base_labels: dict[str, str] = {}
    for _, row in bills.iterrows():
        note = str(row["备注"]).strip()
        text = (
            f"{row['日期']} · {row['使用者']} · {row['类别']} · {float(row['金额']):,.2f}元"
        )
        if note:
            text = f"{text} · {note}"
        base_labels[str(row[ID_COLUMN])] = text

    counts: dict[str, int] = {}
    for text in base_labels.values():
        counts[text] = counts.get(text, 0) + 1

    labels: dict[str, str] = {}
    seen: dict[str, int] = {}
    for bill_id, text in base_labels.items():
        if counts[text] == 1:
            labels[bill_id] = text
            continue
        seen[text] = seen.get(text, 0) + 1
        labels[bill_id] = f"{text}（{seen[text]}）"
    return labels


def render_table(bills: pd.DataFrame) -> None:
    """按日期倒序展示账单明细。编号只用于删除，不展示。"""
    st.subheader("账单明细")
    if bills.empty:
        st.info("暂无账单记录。")
        return

    display = sort_bills(bills).drop(columns=[ID_COLUMN])
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "金额": st.column_config.NumberColumn("金额", format="%.2f"),
        },
    )


def render_delete(bills: pd.DataFrame) -> None:
    """选择一条写错的账单并删除，之后可以在左侧重新添加。"""
    if bills.empty:
        return

    # 删除后的下一轮再清选择，避免同一次运行里改已经画出的下拉框
    if st.session_state.get("clear_delete_select"):
        st.session_state.pop("delete_bill_select", None)
        st.session_state.clear_delete_select = False

    ordered = sort_bills(bills)
    labels = bill_option_labels(ordered)
    st.subheader("删除账单")
    st.caption("选中写错的记录后删除，再从左侧重新添加。")
    selected = st.selectbox(
        "选择要删除的记录",
        options=list(labels),
        format_func=lambda bill_id: labels[str(bill_id)],
        key="delete_bill_select",
    )
    if not st.button("删除这条账单"):
        return

    try:
        removed = delete_bill(str(selected))
    except BillStoreError as exc:
        st.error(str(exc))
        return
    if not removed:
        st.error("没有找到这条账单，可能已经被删除。")
        return
    st.session_state.clear_delete_select = True
    st.session_state.bill_deleted = True
    st.rerun()


def main() -> None:
    """组装侧边栏表单与主界面的统计、图表和列表。"""
    st.set_page_config(page_title="家庭记账", page_icon="📒", layout="wide")
    inject_styles()
    require_login()
    scope = render_sidebar()

    st.title("家庭记账")
    st.caption(storage_caption())
    render_voice_panel()

    try:
        bills = filter_bills(load_bills(), scope)
    except BillStoreError as exc:
        st.error(str(exc))
        st.stop()
    render_export(bills, scope)
    render_metric_cards(bills)
    if st.session_state.get("bill_deleted"):
        st.success("这条账单已删除，可以在左侧重新添加。")
        st.session_state.bill_deleted = False

    chart_col, table_col = st.columns([1, 1.15], gap="large")
    with chart_col:
        render_pie_chart(bills)
    with table_col:
        render_table(bills)
    render_delete(bills)


if __name__ == "__main__":
    main()
