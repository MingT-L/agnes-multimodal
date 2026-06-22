"""pdf_report.py - 任务报告转 PDF（v3，P2.9）。

读取 log_*.json 任务日志，生成包含状态汇总 / 错误分类 / 样例的 PDF 报告。

依赖：reportlab（可选）。未安装时输出 Markdown 报告。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _summary_block(data: dict) -> str:
    lines = ["## 状态汇总", ""]
    counter = Counter()
    error_counter = Counter()
    for v in data.values():
        if not isinstance(v, dict):
            continue
        s = v.get("status", "unknown")
        counter[s] += 1
        if s == "failed":
            error_counter[v.get("error_type", "unknown")] += 1
    lines.append("| 状态 | 数量 |")
    lines.append("|---|---|")
    for s, n in counter.most_common():
        lines.append(f"| {s} | {n} |")
    if error_counter:
        lines.append("")
        lines.append("## 失败错误分类", "")
        lines.append("| 错误类型 | 数量 |")
        lines.append("|---|---|")
        for et, n in error_counter.most_common():
            lines.append(f"| {et} | {n} |")
    return "\n".join(lines)


def _items_block(data: dict) -> str:
    lines = ["## 任务详情", "", "| key | status | error_type | saved_path |", "|---|---|---|---|"]
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        sp = v.get("saved_path") or v.get("video_url") or (v.get("saved_paths") and ", ".join(v["saved_paths"])) or "-"
        lines.append(f"| {k} | {v.get('status', '-')} | {v.get('error_type', '-')} | {str(sp)[:80]} |")
    return "\n".join(lines)


def to_markdown(data: dict, title: str) -> str:
    parts = [f"# {title}", "", _summary_block(data), "", _items_block(data)]
    return "\n".join(parts)


def to_pdf(data: dict, title: str, out_path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
    except ImportError:
        print("⚠️ reportlab 未安装，降级为 Markdown 输出", file=sys.stderr)
        out_path = out_path.with_suffix(".md")
        out_path.write_text(to_markdown(data, title), encoding="utf-8")
        print(f"已生成：{out_path}")
        return

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 0.5 * cm)]

    # 汇总
    counter = Counter()
    error_counter = Counter()
    for v in data.values():
        if not isinstance(v, dict):
            continue
        s = v.get("status", "unknown")
        counter[s] += 1
        if s == "failed":
            error_counter[v.get("error_type", "unknown")] += 1

    summary_rows = [["状态", "数量"]]
    for s, n in counter.most_common():
        summary_rows.append([s, str(n)])
    t = Table(summary_rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(Paragraph("状态汇总", styles["Heading2"]))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))

    if error_counter:
        err_rows = [["错误类型", "数量"]]
        for et, n in error_counter.most_common():
            err_rows.append([et, str(n)])
        t2 = Table(err_rows, hAlign="LEFT")
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]))
        story.append(Paragraph("失败错误分类", styles["Heading2"]))
        story.append(t2)
        story.append(Spacer(1, 0.5 * cm))

    # 详情
    story.append(Paragraph("任务详情", styles["Heading2"]))
    detail_rows = [["key", "status", "error_type", "path"]]
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        sp = v.get("saved_path") or v.get("video_url") or (v.get("saved_paths") and ", ".join(v["saved_paths"])) or "-"
        detail_rows.append([
            k[:30], v.get("status", "-"), v.get("error_type", "-") or "-",
            str(sp)[:50],
        ])
    t3 = Table(detail_rows, hAlign="LEFT", colWidths=[3 * cm, 2 * cm, 2.5 * cm, 6 * cm])
    t3.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(t3)

    doc.build(story)
    print(f"已生成 PDF：{out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="任务报告转 PDF")
    parser.add_argument("--log", required=True, help="log_*.json 路径")
    parser.add_argument("--out", default=None, help="输出路径（默认同目录）")
    parser.add_argument("--title", default="Agnes 任务报告", help="报告标题")
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"日志文件不存在：{log_path}", file=sys.stderr)
        return 1

    data = json.loads(log_path.read_text(encoding="utf-8"))
    out_path = Path(args.out) if args.out else log_path.with_suffix(".pdf")
    to_pdf(data, args.title, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
