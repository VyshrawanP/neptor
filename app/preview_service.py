import html
from typing import Dict, Any


def render_preview_dashboard_html(
    user_id: str,
    draft: Dict[str, Any],
    html_diff: str,
    current_live_version: int,
) -> str:
    """
    Renders an interactive, secure frontend preview dashboard.
    Security: The proposed draft is rendered in an appropriately sandboxed iframe
    to prevent untrusted scripts from executing in the main application context.
    """
    draft_id = html.escape(str(draft.get("draft_id", "")))
    instruction = html.escape(str(draft.get("instruction", "")))
    base_version = int(draft.get("base_version", 1))
    created_at = html.escape(str(draft.get("created_at", "")))
    status = html.escape(str(draft.get("status", "pending")))
    change_summary = html.escape(str(draft.get("change_summary", "No summary available.")))

    validation = draft.get("validation_results", {})
    checks = validation.get("checks", {})
    is_valid = validation.get("is_valid", False)

    validation_html = f"""
    <div style="display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px;">
        <span style="background: {'#166534' if checks.get('html_structure') else '#991b1b'}; color: #fff; padding: 4px 10px; border-radius: 999px; font-size: 12px;">
            {'✓ HTML Structure' if checks.get('html_structure') else '✗ HTML Structure'}
        </span>
        <span style="background: {'#166534' if checks.get('booking_elements') else '#991b1b'}; color: #fff; padding: 4px 10px; border-radius: 999px; font-size: 12px;">
            {'✓ Booking Modal DOM IDs' if checks.get('booking_elements') else '✗ Booking Modal DOM IDs'}
        </span>
        <span style="background: {'#166534' if checks.get('runtime_script') else '#991b1b'}; color: #fff; padding: 4px 10px; border-radius: 999px; font-size: 12px;">
            {'✓ Runtime Script' if checks.get('runtime_script') else '✗ Runtime Script'}
        </span>
        <span style="background: {'#166534' if checks.get('content_integrity') else '#991b1b'}; color: #fff; padding: 4px 10px; border-radius: 999px; font-size: 12px;">
            {'✓ Content Integrity' if checks.get('content_integrity') else '✗ Content Integrity'}
        </span>
    </div>
    """

    escaped_user_id = html.escape(user_id)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Preview & Approval — {escaped_user_id} (Draft {draft_id})</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #38bdf8;
            --success: #22c55e;
            --danger: #ef4444;
            --border: #334155;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 24px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }}
        .title-group h1 {{
            font-size: 1.5rem;
            font-weight: 700;
        }}
        .badge-pending {{
            display: inline-block;
            background: #d97706;
            color: #fff;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .meta-strip {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px 20px;
            margin-bottom: 24px;
        }}
        .actions {{
            display: flex;
            gap: 12px;
        }}
        .btn {{
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
        }}
        .btn-approve {{
            background: var(--success);
            color: #ffffff;
        }}
        .btn-approve:hover {{
            background: #16a34a;
        }}
        .btn-reject {{
            background: var(--danger);
            color: #ffffff;
        }}
        .btn-reject:hover {{
            background: #dc2626;
        }}
        .tabs {{
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
        }}
        .tab-btn {{
            background: transparent;
            color: var(--text-secondary);
            border: none;
            padding: 8px 16px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            border-radius: 6px;
        }}
        .tab-btn.active {{
            background: var(--surface);
            color: var(--accent);
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
        .split-view {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            min-height: 550px;
        }}
        .preview-pane {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .pane-header {{
            padding: 10px 16px;
            background: #090d16;
            font-weight: 600;
            font-size: 13px;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
        }}
        .iframe-container {{
            flex: 1;
            position: relative;
            background: #ffffff;
        }}
        iframe {{
            width: 100%;
            height: 100%;
            min-height: 520px;
            border: none;
        }}
        #feedback {{
            margin-top: 16px;
            padding: 12px;
            border-radius: 8px;
            display: none;
            font-weight: 600;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="title-group">
            <h1>AI Preview & Approval System <span class="badge-pending">{status}</span></h1>
            <p style="color: var(--text-secondary); font-size: 13px; margin-top: 4px;">
                User: <strong>{escaped_user_id}</strong> &bull; Draft ID: <code>{draft_id}</code> &bull; Base Version: <strong>v{base_version}</strong> &rarr; Target: <strong>v{base_version + 1}</strong>
            </p>
        </div>
        <div class="actions">
            <button class="btn btn-reject" id="rejectBtn" onclick="submitDecision('reject')">Reject Changes</button>
            <button class="btn btn-approve" id="approveBtn" onclick="submitDecision('approve')">Approve &amp; Publish</button>
        </div>
    </div>

    <div id="feedback"></div>

    <div class="meta-strip">
        <div style="margin-bottom: 12px;">
            <strong style="color: var(--accent);">User Instruction:</strong>
            <span style="color: var(--text-primary); margin-left: 6px;">"{instruction}"</span>
        </div>
        <div style="margin-bottom: 12px;">
            <strong style="color: var(--accent);">AI Change Summary:</strong>
            <span style="color: var(--text-primary); margin-left: 6px;">{change_summary}</span>
        </div>
        <div>
            <strong style="color: var(--accent);">Automated QA Gate Results:</strong>
            {validation_html}
        </div>
    </div>

    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('split')">Side-by-Side Visual Comparison</button>
        <button class="tab-btn" onclick="switchTab('diff')">HTML Code Diff (Red/Green)</button>
    </div>

    <div id="tab-split" class="tab-content active">
        <div class="split-view">
            <div class="preview-pane">
                <div class="pane-header">
                    <span>CURRENT LIVE WEBSITE (v{current_live_version})</span>
                    <span>Read-Only</span>
                </div>
                <div class="iframe-container">
                    <!-- Sandboxed live site -->
                    <iframe sandbox="allow-same-origin allow-scripts" src="/site/{escaped_user_id}"></iframe>
                </div>
            </div>
            <div class="preview-pane">
                <div class="pane-header">
                    <span>PROPOSED PREVIEW (Draft v{base_version + 1})</span>
                    <span style="color: #38bdf8;">Sandboxed</span>
                </div>
                <div class="iframe-container">
                    <!-- Restrictive sandboxed proposed draft (no top-level navigation, isolated) -->
                    <iframe sandbox="allow-same-origin allow-forms" src="/preview/{escaped_user_id}/render"></iframe>
                </div>
            </div>
        </div>
    </div>

    <div id="tab-diff" class="tab-content">
        <div style="border: 1px solid var(--border); border-radius: 8px; overflow: hidden;">
            {html_diff}
        </div>
    </div>

    <script>
        function switchTab(name) {{
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            if (name === 'split') {{
                document.querySelectorAll('.tab-btn')[0].classList.add('active');
                document.getElementById('tab-split').classList.add('active');
            }} else {{
                document.querySelectorAll('.tab-btn')[1].classList.add('active');
                document.getElementById('tab-diff').classList.add('active');
            }}
        }}

        async function submitDecision(action) {{
            const feedback = document.getElementById('feedback');
            const approveBtn = document.getElementById('approveBtn');
            const rejectBtn = document.getElementById('rejectBtn');
            approveBtn.disabled = true;
            rejectBtn.disabled = true;

            const url = action === 'approve' ? '/approve/{escaped_user_id}' : '/reject/{escaped_user_id}';
            try {{
                const res = await fetch(url, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ draft_id: '{draft_id}' }})
                }});
                const data = await res.json();
                if (res.ok) {{
                    feedback.style.display = 'block';
                    feedback.style.background = action === 'approve' ? '#14532d' : '#7f1d1d';
                    feedback.style.color = '#fff';
                    feedback.textContent = data.message || (action === 'approve' ? 'Published successfully!' : 'Draft rejected.');
                    setTimeout(() => {{ window.location.reload(); }}, 1500);
                }} else {{
                    feedback.style.display = 'block';
                    feedback.style.background = '#7f1d1d';
                    feedback.style.color = '#fff';
                    feedback.textContent = data.detail || 'Action failed: ' + res.statusText;
                    approveBtn.disabled = false;
                    rejectBtn.disabled = false;
                }}
            }} catch (err) {{
                feedback.style.display = 'block';
                feedback.style.background = '#7f1d1d';
                feedback.style.color = '#fff';
                feedback.textContent = 'Network error: ' + err.message;
                approveBtn.disabled = false;
                rejectBtn.disabled = false;
            }}
        }}
    </script>
</body>
</html>"""
