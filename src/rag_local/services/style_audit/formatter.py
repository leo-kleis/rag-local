from typing import Any


def format_audit_report(data: dict[str, Any]) -> str:
    """Formatea el reporte de auditoría de layout en texto claro para el agente."""
    if data.get("status") != "success":
        return f"ERROR: {data.get('message', 'No audit results available.')}"

    issues = data.get("issues", [])
    total = data.get("total_issues", 0)
    sev_filter = data.get("severity_filter", "ALL")

    if not issues:
        return (
            f"[CSS Layout Audit — 0 issues found (Severity Filter: {sev_filter})]\n"
            "  No CSS layout risk anti-patterns detected."
        )

    critical_count = sum(1 for i in issues if i["severity"] == "CRITICAL")
    warning_count = sum(1 for i in issues if i["severity"] == "WARNING")
    info_count = sum(1 for i in issues if i["severity"] == "INFO")

    summary_hdr = (
        f"[CSS Layout Audit — {total} issues found "
        f"({critical_count} CRITICAL, {warning_count} WARNING, {info_count} INFO)]"
    )
    lines = [summary_hdr]

    for issue in issues:
        sev_tag = f"[{issue['severity']}]"
        loc = f"{issue['file']}:L{issue['start_line']}-{issue['end_line']}"
        lines.append(f"\n{sev_tag} {loc} | {issue['category']}")
        lines.append(f"  Selector: {issue['selector']}")
        lines.append(f"  Issue: {issue['message']}")
        lines.append(f"  Fix: {issue['recommendation']}")

    return "\n".join(lines)
