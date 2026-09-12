from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.feishu_bitable import FeishuBitableClient  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    client = FeishuBitableClient.from_env()
    try:
        report = client.validate_schema()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"enabled={report.enabled}")
    print(f"configured={report.configured}")
    print(f"schema_ok={report.ok}")
    print(report.message)
    if report.available_fields:
        print("当前字段：")
        for field_name in report.available_fields:
            print(f"- {field_name}")
    if report.missing_fields:
        print("缺少字段：")
        for field_name in report.missing_fields:
            print(f"- {field_name}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
