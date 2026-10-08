"""账单数据存取层。

配置了 Supabase 时按单条读写云端表，家人之间不会互相覆盖。
未配置时仍写入本地 CSV，方便本机继续使用。
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

# 账单字段与数据库列对齐；编号只用于定位记录，不出现在导出文件里
BILL_COLUMNS: list[str] = ["日期", "类别", "金额", "备注", "使用者"]
ID_COLUMN: str = "编号"
OWNER_COLUMN: str = "使用者"
STORAGE_COLUMNS: list[str] = [ID_COLUMN, *BILL_COLUMNS]
DEFAULT_OWNER: str = "我自己"

# 云端表使用英文列，界面继续用中文列
_DB_COLUMN_MAP: dict[str, str] = {
    "id": ID_COLUMN,
    "bill_date": "日期",
    "category": "类别",
    "amount": "金额",
    "note": "备注",
    "owner": OWNER_COLUMN,
}

# 数据文件放在项目 data 目录，首次保存时自动创建
DATA_FILE: Path = Path(__file__).resolve().parent / "data" / "bills.csv"
_SUPABASE_TABLE: str = "bills"
_supabase_client: Any | None = None
_supabase_client_key: tuple[str, str] | None = None


class BillStoreError(Exception):
    """云端读写失败时抛出，由界面显示给使用者。"""


def supabase_configured() -> bool:
    """是否已经提供 Supabase 地址和 anon key。"""
    return bool(_read_setting("SUPABASE_URL") and _read_setting("SUPABASE_KEY"))


def storage_caption() -> str:
    """说明当前账单写到哪里，方便确认是不是家人共享的那一份。"""
    if supabase_configured():
        return "账单保存在 Supabase，家人打开的是同一份账。"
    return "尚未配置 Supabase，账单暂存在本机 data/bills.csv。"


def load_bills() -> pd.DataFrame:
    """加载全部账单。已配置云端时读 Supabase，否则读本地 CSV。"""
    if supabase_configured():
        return _load_remote_bills()
    return _load_local_bills()


def save_bills(bills: pd.DataFrame) -> None:
    """将账单全量写回本地 CSV。云端模式按单条新增和删除，不走这里。"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 只落标准列，避免多余字段污染文件；utf-8-sig 便于 Excel 打开中文
    bills.loc[:, STORAGE_COLUMNS].to_csv(DATA_FILE, index=False, encoding="utf-8-sig")


def add_bill(
    bill_date: str,
    category: str,
    amount: float,
    note: str,
    owner: str,
) -> pd.DataFrame:
    """追加一条账单并保存，返回包含新记录的完整账单表。"""
    owner_name = owner.strip() or DEFAULT_OWNER
    record = {
        ID_COLUMN: _new_bill_id(),
        "日期": bill_date,
        "类别": category.strip(),
        "金额": round(float(amount), 2),
        "备注": note.strip(),
        OWNER_COLUMN: owner_name,
    }
    if supabase_configured():
        # 只插入这一条，避免两位家人同时保存时互相覆盖整表
        _insert_remote_bill(record)
        return _load_remote_bills()

    bills = _load_local_bills()
    updated = pd.concat([bills, pd.DataFrame([record])], ignore_index=True)
    save_bills(updated)
    return updated


def delete_bill(bill_id: str) -> bool:
    """按编号删除一条账单。编号不存在时不改数据，返回 False。"""
    if supabase_configured():
        return _delete_remote_bill(str(bill_id))

    bills = _load_local_bills()
    remaining = bills.loc[bills[ID_COLUMN] != str(bill_id)].reset_index(drop=True)
    if len(remaining) == len(bills):
        return False
    save_bills(remaining)
    return True


def export_bills_to_excel(bills: pd.DataFrame) -> bytes:
    """把现有账单导出为 xlsx 字节，供界面直接下载。"""
    export_frame = bills.loc[:, BILL_COLUMNS].copy()
    if not export_frame.empty:
        # 按日期倒序，和页面明细保持一致
        export_frame["_排序日期"] = pd.to_datetime(export_frame["日期"], errors="coerce")
        export_frame = (
            export_frame.sort_values("_排序日期", ascending=False)
            .drop(columns="_排序日期")
            .reset_index(drop=True)
        )

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_frame.to_excel(writer, index=False, sheet_name="账单明细")
        worksheet = writer.sheets["账单明细"]
        # 加宽列，避免中文和备注在 Excel 里被挤成一行省略号
        for column, width in {"A": 14, "B": 12, "C": 12, "D": 28, "E": 12}.items():
            worksheet.column_dimensions[column].width = width
        for cell in worksheet["C"][1:]:
            cell.number_format = "0.00"
    return buffer.getvalue()


def _read_setting(name: str) -> str:
    """先读 st.secrets，没有再读同名环境变量。"""
    secret_value = _read_streamlit_secret(name)
    if secret_value:
        return secret_value
    return os.getenv(name, "").strip()


def _read_streamlit_secret(name: str) -> str:
    """从 Streamlit secrets 读取一项配置。未配置或当前不在应用里时返回空字符串。"""
    try:
        import streamlit as st

        secrets = st.secrets
        if name not in secrets:
            return ""
        return str(secrets[name]).strip()
    except Exception:
        return ""


def _get_supabase_client() -> Any:
    """创建并复用 Supabase 客户端。缺少密钥或依赖时给出可操作的错误。"""
    global _supabase_client, _supabase_client_key
    url = _read_setting("SUPABASE_URL")
    key = _read_setting("SUPABASE_KEY")
    if not url or not key:
        raise BillStoreError("未配置 SUPABASE_URL 或 SUPABASE_KEY。")
    # 密钥变更后重新建连，避免一直用启动时的旧客户端
    if _supabase_client is not None and _supabase_client_key == (url, key):
        return _supabase_client

    try:
        from supabase import create_client
    except ImportError as exc:
        raise BillStoreError(
            "缺少 supabase 包，请先运行：python -m pip install -r requirements.txt"
        ) from exc

    _supabase_client = create_client(url, key)
    _supabase_client_key = (url, key)
    return _supabase_client


def _load_remote_bills() -> pd.DataFrame:
    """从云端读取全部账单，并转成界面使用的中文列。"""
    try:
        response = (
            _get_supabase_client()
            .table(_SUPABASE_TABLE)
            .select("id,bill_date,category,amount,note,owner")
            .execute()
        )
    except BillStoreError:
        raise
    except Exception as exc:
        raise BillStoreError(f"读取 Supabase 账单失败：{exc}") from exc
    return _frame_from_remote_rows(list(response.data or []))


def _insert_remote_bill(record: dict[str, object]) -> None:
    """向云端插入一条账单。"""
    payload = {
        "id": str(record[ID_COLUMN]),
        "bill_date": str(record["日期"]),
        "category": str(record["类别"]),
        "amount": float(record["金额"]),
        "note": str(record["备注"]),
        "owner": str(record[OWNER_COLUMN]),
    }
    try:
        _get_supabase_client().table(_SUPABASE_TABLE).insert(payload).execute()
    except BillStoreError:
        raise
    except Exception as exc:
        raise BillStoreError(f"保存到 Supabase 失败：{exc}") from exc


def _delete_remote_bill(bill_id: str) -> bool:
    """按主键删除云端的一条账单。"""
    try:
        response = (
            _get_supabase_client()
            .table(_SUPABASE_TABLE)
            .delete()
            .eq("id", bill_id)
            .execute()
        )
    except BillStoreError:
        raise
    except Exception as exc:
        raise BillStoreError(f"从 Supabase 删除失败：{exc}") from exc
    return bool(response.data)


def _frame_from_remote_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """把 Supabase 返回的英文列转换成界面表格。"""
    if not rows:
        return pd.DataFrame(columns=STORAGE_COLUMNS)

    frame = pd.DataFrame(rows).rename(columns=_DB_COLUMN_MAP)
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["金额"] = pd.to_numeric(frame["金额"], errors="coerce").fillna(0.0)
    frame["备注"] = frame["备注"].fillna("").astype(str)
    frame[OWNER_COLUMN] = frame[OWNER_COLUMN].fillna("").astype(str).str.strip()
    frame.loc[frame[OWNER_COLUMN] == "", OWNER_COLUMN] = DEFAULT_OWNER
    frame[ID_COLUMN] = frame[ID_COLUMN].astype(str)
    return frame.loc[:, STORAGE_COLUMNS].reset_index(drop=True)


def _load_local_bills() -> pd.DataFrame:
    """从本地 CSV 加载全部账单。文件不存在时返回带标准列的空表。"""
    if not DATA_FILE.exists() or DATA_FILE.stat().st_size == 0:
        return pd.DataFrame(columns=STORAGE_COLUMNS)

    bills = pd.read_csv(
        DATA_FILE,
        encoding="utf-8-sig",
        dtype={"日期": str, "类别": str, "备注": str},
    )
    normalized, changed = _normalize_local_bills(bills)
    # 旧文件缺编号或使用者时写回一次，避免每次刷新都生成新编号
    if changed:
        save_bills(normalized)
    return normalized


def _normalize_local_bills(bills: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """补齐本地表缺列，并标记是否需要写回文件。"""
    changed = False
    for column in BILL_COLUMNS:
        if column not in bills.columns:
            if column == "金额":
                bills[column] = 0.0
            elif column == OWNER_COLUMN:
                bills[column] = DEFAULT_OWNER
            else:
                bills[column] = ""
            changed = True

    bills["金额"] = pd.to_numeric(bills["金额"], errors="coerce").fillna(0.0)
    bills["备注"] = bills["备注"].fillna("").astype(str)
    bills[OWNER_COLUMN] = bills[OWNER_COLUMN].fillna("").astype(str).str.strip()
    missing_owner = bills[OWNER_COLUMN] == ""
    if bool(missing_owner.any()):
        bills.loc[missing_owner, OWNER_COLUMN] = DEFAULT_OWNER
        changed = True

    if ID_COLUMN not in bills.columns:
        bills[ID_COLUMN] = ""
        changed = True
    bills[ID_COLUMN] = bills[ID_COLUMN].fillna("").astype(str)
    missing_id = bills[ID_COLUMN].str.strip() == ""
    if bool(missing_id.any()):
        bills.loc[missing_id, ID_COLUMN] = [
            _new_bill_id() for _ in range(int(missing_id.sum()))
        ]
        changed = True
    return bills.loc[:, STORAGE_COLUMNS].reset_index(drop=True), changed


def _new_bill_id() -> str:
    """生成一条账单的唯一编号。带连字符的形式可以直接写入 uuid 列。"""
    return str(uuid4())
