"""
app1.py — GenAI + Script Accessibility Linter — Live Dashboard

Features:
- Enter a webpage URL
- Upload a PDF screenshot of that webpage
- Send URL + PDF to Flask using POST multipart/form-data
- Playwright renders the webpage
- Gemini audits the rendered page using:
    1. Rendered DOM/HTML
    2. Accessibility Tree / ARIA snapshot
    3. Computed styles + geometry
    4. Live Chromium screenshot
    5. Original server source
    6. Uploaded PDF
- app2.py (hardcoded WCAG 2.1/2.2 checkpoint checks) independently
  audits the SAME live rendered page
- Both result sets are merged, deduplicated, and displayed together
  in the dashboard, tagged by source ("AI", "Script", or "AI + Script")

Setup:

    pip install flask playwright google-genai

    playwright install chromium

Windows PowerShell:

    $env:GEMINI_API_KEY="your-key-here"

Run:

    python app1.py

(app2.py must be in the same folder — it is imported, not run separately.)
"""

import json
import os
import re
import threading
import webbrowser

from flask import Flask, jsonify, render_template_string, request

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError
)

from google import genai
from google.genai import types

import app2


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_NAME = "gemini-3.5-flash"

HOST = "127.0.0.1"
PORT = 5000

MAX_HTML_BYTES = 500_000

PAGE_LOAD_TIMEOUT_MS = 30_000

# Maximum PDF upload size = 20 MB
MAX_PDF_BYTES = 20 * 1024 * 1024


# ============================================================
# GEMINI API KEY
# ============================================================

API_KEY = os.environ.get("GEMINI_API_KEY")


# ============================================================
# GEMINI SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
You are a strict WCAG 2.2 accessibility QA auditor.

You audit a webpage using ALL available browser and visual evidence:

1. The fully rendered HTML/DOM.
2. The browser Accessibility Tree / ARIA snapshot.
3. Computed styles and rendered geometry/bounding boxes.
4. A live Chromium screenshot of the rendered page.
5. The original server HTTP response source.
6. A user-supplied PDF screenshot/document.

Use the sources together. Prefer live rendered evidence (DOM, accessibility
tree, computed styles/geometry, and screenshot) when evaluating the current
page. Treat original source and the PDF as supporting evidence.

Use the rendered HTML to identify:

- Semantic HTML issues
- ARIA issues
- Missing labels
- Missing alt text
- Heading structure
- Links
- Form controls
- Accessible names
- Keyboard-related markup
- DOM structure
- WCAG implementation issues

Use the live Chromium screenshot and PDF visual representations to identify visual issues such as:

- Color contrast
- Text visibility
- Visual hierarchy
- Layout problems
- Text clipping
- Overlapping content
- Images of text
- Color-dependent information
- Visible focus indicators if shown
- Target-size issues where visually determinable
- Reflow/layout problems where visually determinable
- Other accessibility problems visible in the screenshot or PDF

IMPORTANT:

Do not invent accessibility issues.

Only report issues that are reasonably supported by the
rendered HTML or the supplied PDF.

Audit against WCAG 2.1 and WCAG 2.2.

Consider A, AA and AAA success criteria where applicable.

For every distinct defect, produce one finding object.

Each finding must contain:

element:
The offending HTML element as a short tag/selector snippet.

location:
Precisely where the element occurs.
Include id, class, name, DOM path, or ancestor chain where available.

wcag_criterion:
The specific WCAG success criterion number and name.

severity:
One of:
critical
serious
moderate
minor

defect:
A short, specific defect name/title.

actual_result:
What the webpage actually does today.

user_impact:
Who is affected and how.

remediation:
A concrete actionable recommendation.
Include corrected markup where useful.

summary:
A concise overall accessibility summary.

Be thorough.

Do not invent issues.

Return ONLY the JSON described by the response schema.
"""


# ============================================================
# GEMINI RESPONSE SCHEMA
# ============================================================

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element": {"type": "string"},
                    "location": {"type": "string"},
                    "wcag_criterion": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "serious", "moderate", "minor"]
                    },
                    "defect": {"type": "string"},
                    "actual_result": {"type": "string"},
                    "user_impact": {"type": "string"},
                    "remediation": {"type": "string"}
                },
                "required": [
                    "element", "location", "wcag_criterion", "severity",
                    "defect", "actual_result", "user_impact", "remediation"
                ]
            }
        },
        "summary": {"type": "string"}
    },
    "required": ["findings", "summary"]
}


# ============================================================
# SEVERITY ORDER
# ============================================================

SEVERITY_ORDER = {
    "critical": 0,
    "serious": 1,
    "moderate": 2,
    "minor": 3
}


# ============================================================
# PLAYWRIGHT — RENDER PAGE + RUN app2 SCRIPT CHECKS
# ============================================================

def render_and_run_script_checks(url: str):
    """Render the target once and collect every audit representation from the
    same live Chromium page. No DOM/CSS/content is injected or modified.

    Returns:
        rendered_html, original_source, accessibility_tree, element_data,
        screenshot_bytes, script_findings
    """

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            response = None
            try:
                response = page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="networkidle")
            except PlaywrightTimeoutError:
                # Some websites never reach networkidle. Continue with the
                # already-loaded live page.
                pass

            # Let client-side rendering settle without changing the page.
            page.wait_for_timeout(500)

            rendered_html = page.content()

            # Original HTTP response source (before client-side DOM changes).
            original_source = ""
            if response is not None:
                try:
                    original_source = response.body().decode("utf-8", errors="replace")
                except Exception:
                    try:
                        original_source = response.text()
                    except Exception:
                        original_source = ""

            # Shared structured inspection data: accessibility tree/ARIA plus
            # computed styles and rendered geometry.
            inspection = app2.collect_page_inspection_data(page)
            # Reuse the exact same inspection data inside app2.run_all_checks()
            # instead of collecting it a second time.
            try:
                setattr(page, "_app2_inspection_data", inspection)
            except Exception:
                pass
            accessibility_tree = inspection.get("accessibility_tree", "")
            element_data = inspection.get("elements", [])

            # Real screenshot of the target webpage from the same Chromium page.
            screenshot_bytes = page.screenshot(type="png", full_page=False)

            print("[INFO] Running app2.py hardcoded WCAG checkpoint checks...")
            script_findings = app2.run_all_checks(page)
            print(f"[INFO] Script checks found {len(script_findings)} issues.")

        finally:
            browser.close()

    # Limit large text payloads sent to Gemini.
    rendered_html = rendered_html.encode("utf-8")[:MAX_HTML_BYTES].decode("utf-8", errors="ignore")
    original_source = original_source.encode("utf-8")[:MAX_HTML_BYTES].decode("utf-8", errors="ignore")

    return (
        rendered_html,
        original_source,
        accessibility_tree,
        element_data,
        screenshot_bytes,
        script_findings,
    )


# ============================================================
# GEMINI ACCESSIBILITY AUDIT
# ============================================================

def run_accessibility_audit(html_source: str, original_source: str, accessibility_tree: str, element_data: list, screenshot_bytes: bytes, pdf_bytes: bytes) -> dict:

    if not API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set.\n\n"
            "Windows PowerShell:\n"
            '$env:GEMINI_API_KEY="your-key-here"\n\n'
            "Then restart the server."
        )

    client = genai.Client(api_key=API_KEY)

    # Keep the UI unchanged: all additional browser evidence is collected
    # internally and sent only to Gemini.
    element_data_json = json.dumps(element_data, ensure_ascii=False, separators=(",", ":"))

    prompt = (
        "Perform a complete WCAG 2.1 and WCAG 2.2 accessibility audit of the webpage.\n\n"
        "Use ALL supplied evidence together. Do not invent issues. Report only issues supported by the evidence.\n\n"
        "SOURCE 1 - RENDERED DOM/HTML (highest priority for implementation):\n"
        "```html\n" + html_source + "\n```\n\n"
        "SOURCE 2 - ACCESSIBILITY TREE / ARIA SNAPSHOT:\n"
        "```text\n" + str(accessibility_tree) + "\n```\n\n"
        "SOURCE 3 - COMPUTED STYLES + RENDERED GEOMETRY:\n"
        "The following JSON contains selected live elements, their visibility, accessible/ARIA attributes, computed CSS styles, and getBoundingClientRect geometry. Use it for contrast, focus indicators, target size, clipping, visibility, layout, and other measurable presentation issues.\n"
        "```json\n" + element_data_json + "\n```\n\n"
        "SOURCE 4 - LIVE CHROMIUM SCREENSHOT:\n"
        "Use the attached PNG image to evaluate visual appearance, layout, overlap, text visibility, visual focus indicators, images of text, and other issues that require visual context.\n\n"
        "SOURCE 5 - ORIGINAL SERVER SOURCE:\n"
        "```html\n" + original_source + "\n```\n\n"
        "SOURCE 6 - USER-SUPPLIED PDF SCREENSHOT/DOCUMENT:\n"
        "Use the attached PDF as additional visual evidence. If it conflicts with the live Chromium screenshot, prefer the live Chromium screenshot for the current rendered page and use the PDF only as supporting evidence.\n\n"
        "Important evidence rules:\n"
        "- Rendered DOM describes the current post-JavaScript page.\n"
        "- Accessibility tree describes the browser accessibility representation.\n"
        "- Computed styles and geometry describe actual rendered CSS/layout values.\n"
        "- Screenshot describes actual visual appearance.\n"
        "- Original source is secondary and may differ from the rendered DOM.\n"
        "- Do not claim a violation from a theoretical possibility alone.\n\n"
        "Return only the JSON defined by the response schema."
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png"),
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    try:
        result = json.loads(response.text)
    except (json.JSONDecodeError, AttributeError) as e:
        raise RuntimeError("Failed to parse Gemini response as JSON: " + str(e))

    return result


# ============================================================
# MERGE + DEDUPE  (AI findings  +  app2.py script findings)
# ============================================================

def _sc_number(wcag_criterion: str) -> str:
    m = re.match(r"\s*(\d+\.\d+\.\d+)", wcag_criterion or "")
    return m.group(1) if m else (wcag_criterion or "").strip().lower()


def _element_signature(element: str) -> str:
    # Strip quoted attribute values so minor text differences
    # ("src=a.png" vs "src=b.png") don't block a duplicate match
    # when the same tag + criterion is flagged by both sources.
    cleaned = re.sub(r'["\'].*?["\']', "", element or "")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def merge_findings(ai_findings, script_findings):
    tagged = []
    for f in ai_findings:
        g = dict(f)
        g["source"] = "AI"
        tagged.append(g)
    for f in script_findings:
        g = dict(f)
        g["source"] = "Script"
        tagged.append(g)

    merged = {}
    order = []
    for f in tagged:
        key = (_sc_number(f.get("wcag_criterion")), _element_signature(f.get("element")))
        if key in merged:
            existing = merged[key]
            if f["source"] not in existing["source"].split(" + "):
                existing["source"] = existing["source"] + " + " + f["source"]
            # Prefer whichever finding has the more detailed remediation.
            if len(f.get("remediation", "")) > len(existing.get("remediation", "")):
                for field in ("defect", "actual_result", "user_impact", "remediation"):
                    existing[field] = f.get(field, existing.get(field))
        else:
            merged[key] = f
            order.append(key)

    result = [merged[k] for k in order]
    result.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity", "minor"), 99))
    return result


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES + (1 * 1024 * 1024)


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/audit", methods=["POST"])
def api_audit():

    url = (request.form.get("url") or "").strip()
    pdf_file = request.files.get("screenshot")

    if not url:
        return jsonify({"ok": False, "error": "Please enter a URL."}), 400

    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"ok": False, "error": "URL must start with http:// or https://"}), 400

    if not pdf_file:
        return jsonify({"ok": False, "error": "Please upload a PDF screenshot."}), 400

    filename = (pdf_file.filename or "").lower()
    if not filename.endswith(".pdf"):
        return jsonify({"ok": False, "error": "Only PDF files are allowed."}), 400

    allowed_mime_types = {"application/pdf", "application/x-pdf"}
    if pdf_file.mimetype not in allowed_mime_types:
        return jsonify({"ok": False, "error": "Uploaded file must be a PDF."}), 400

    try:
        pdf_bytes = pdf_file.read()

        if not pdf_bytes:
            return jsonify({"ok": False, "error": "The uploaded PDF is empty."}), 400

        if len(pdf_bytes) > MAX_PDF_BYTES:
            return jsonify({"ok": False, "error": "PDF is too large. Maximum size is 20 MB."}), 400

        if not pdf_bytes.startswith(b"%PDF"):
            return jsonify({"ok": False, "error": "The uploaded file does not appear to be a valid PDF."}), 400

        # ----------------------------------------------------
        # Render webpage ONCE + run app2.py script checks on it
        # ----------------------------------------------------
        print("\n[INFO] Rendering URL:", url)
        (
            html_source,
            original_source,
            accessibility_tree,
            element_data,
            screenshot_bytes,
            script_findings,
        ) = render_and_run_script_checks(url)
        print("[INFO] Webpage rendered and all browser evidence collected successfully.")

        # ----------------------------------------------------
        # Gemini audit (DOM + ARIA + styles/geometry + screenshot + source + PDF)
        # ----------------------------------------------------
        print("[INFO] Sending rendered DOM + accessibility tree + styles/geometry + screenshot + source + PDF to Gemini...")
        audit_result = run_accessibility_audit(
            html_source=html_source,
            original_source=original_source,
            accessibility_tree=accessibility_tree,
            element_data=element_data,
            screenshot_bytes=screenshot_bytes,
            pdf_bytes=pdf_bytes,
        )
        print("[INFO] Gemini accessibility audit completed.")

        # ----------------------------------------------------
        # Merge AI + Script results, dedupe
        # ----------------------------------------------------
        combined_findings = merge_findings(audit_result.get("findings", []), script_findings)

    except Exception as e:
        print("[ERROR]", str(e))
        return jsonify({"ok": False, "error": str(e)}), 502

    return jsonify({
        "ok": True,
        "source": url,
        "summary": audit_result.get("summary", ""),
        "findings": combined_findings,
        "counts": {
            "ai_only": sum(1 for f in combined_findings if f["source"] == "AI"),
            "script_only": sum(1 for f in combined_findings if f["source"] == "Script"),
            "both": sum(1 for f in combined_findings if f["source"] not in ("AI", "Script")),
        },
        "coverage": app2.coverage_report(),
    })


# ============================================================
# DASHBOARD HTML
# ============================================================

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Accessibility Audit Dashboard</title>

<style>

:root {
    --bg: #0f1115;
    --panel: #171a21;
    --border: #262b36;
    --text: #e6e8ec;
    --muted: #9aa3b2;
    --critical: #dc2626;
    --serious: #ea580c;
    --moderate: #ca8a04;
    --minor: #2563eb;
    --ai: #7c3aed;
    --script: #059669;
    --both: #0891b2;
}

* { box-sizing: border-box; }

body {
    margin: 0;
    padding: 32px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
}

h1 { margin: 0 0 4px 0; font-size: 24px; }
.subtitle { color: var(--muted); font-size: 13px; margin-bottom: 24px; }

.input-section {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
}

.input-label {
    display: block; color: var(--muted); font-size: 12px;
    font-weight: 600; margin-bottom: 7px;
}

.input-row { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }

.url-container { flex: 1; min-width: 300px; }

.url-input {
    width: 100%; background: #0b0d11; border: 1px solid var(--border);
    color: var(--text); padding: 12px 14px; border-radius: 8px; font-size: 14px;
}
.url-input:focus { outline: 2px solid #2563eb; outline-offset: 1px; }

.pdf-container { min-width: 300px; }
.pdf-input-wrapper {
    background: #0b0d11; border: 1px solid var(--border); color: var(--text);
    padding: 9px 10px; border-radius: 8px;
}
.pdf-input { color: var(--muted); font-size: 13px; width: 100%; }

.run-button {
    background: #2563eb; border: none; color: #fff; padding: 12px 24px;
    border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; height: 42px;
}
.run-button:hover { background: #1d4ed8; }
.run-button:disabled { background: #374151; cursor: not-allowed; }

.pdf-info { display: none; margin-top: 12px; color: var(--muted); font-size: 12px; }
.pdf-info strong { color: var(--text); }

.status { font-size: 13px; margin-bottom: 20px; min-height: 18px; }
.status.error { color: #fca5a5; }
.status.success { color: #86efac; }
.status.loading { color: var(--muted); }

.spinner {
    display: inline-block; width: 13px; height: 13px; border: 2px solid #374151;
    border-top-color: #9ca3af; border-radius: 50%; animation: spin .7s linear infinite;
    margin-right: 6px; vertical-align: -2px;
}
@keyframes spin { to { transform: rotate(360deg); } }

.meta { color: var(--muted); font-size: 13px; margin-bottom: 16px; display: none; }

.coverage {
    display: none; background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 20px; margin-bottom: 16px; font-size: 13px; color: var(--muted);
}
.coverage strong { color: var(--text); }

.summary {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 20px; margin-bottom: 24px; line-height: 1.5; display: none;
}

.stats { display: none; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.stat-card {
    background: var(--panel); border: 1px solid var(--border); border-top: 3px solid var(--accent);
    border-radius: 10px; padding: 14px 22px; min-width: 110px;
}
.stat-count { font-size: 28px; font-weight: 700; }
.stat-label { color: var(--muted); font-size: 13px; margin-top: 2px; }

.tabs { display: none; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
.tab-btn {
    background: transparent; border: none; color: var(--muted); padding: 10px 18px;
    font-size: 14px; font-weight: 600; cursor: pointer; border-bottom: 2px solid transparent;
}
.tab-btn.active { color: var(--text); border-bottom-color: #2563eb; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

.filters { display: none; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.filter-btn {
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    padding: 6px 14px; border-radius: 20px; cursor: pointer; font-size: 13px;
}
.filter-btn.active { background: #2563eb; border-color: #2563eb; }

.source-filters { display: none; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
.source-btn {
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    padding: 6px 14px; border-radius: 20px; cursor: pointer; font-size: 13px;
}
.source-btn.active { background: #0891b2; border-color: #0891b2; }

.findings { display: flex; flex-direction: column; gap: 14px; }
.finding-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; }
.finding-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }

.badge { color: #fff; font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 5px; letter-spacing: .03em; }
.source-badge { font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 5px; letter-spacing: .03em; color: #fff; }

.criterion { font-weight: 600; color: var(--text); font-size: 14px; }

.finding-element code {
    background: #0b0d11; border: 1px solid var(--border); padding: 2px 6px;
    border-radius: 4px; font-size: 12.5px; color: #93c5fd;
}
.finding-element .loc { color: var(--muted); font-size: 12px; margin-left: 8px; }

.finding-row { font-size: 14px; line-height: 1.5; margin-top: 8px; }
.finding-row strong { color: var(--muted); font-weight: 600; }
.remediation { background: #0b0d11; border-radius: 6px; padding: 8px 10px; }

.empty { color: var(--muted); padding: 40px 0; text-align: center; }

.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
table.sheet { border-collapse: collapse; width: 100%; min-width: 1200px; font-size: 13px; background: var(--panel); }
table.sheet th {
    background: #1d212b; color: var(--text); text-align: left; padding: 10px 12px;
    border: 1px solid var(--border); white-space: nowrap;
}
table.sheet td { padding: 10px 12px; border: 1px solid var(--border); vertical-align: top; line-height: 1.4; }
table.sheet tbody tr:nth-child(even) { background: #14171e; }
table.sheet tbody tr:hover { background: #1b2029; }
table.sheet .col-sno { width: 44px; text-align: center; color: var(--muted); }
table.sheet .col-element code {
    background: #0b0d11; border: 1px solid var(--border); padding: 2px 6px;
    border-radius: 4px; font-size: 12px; color: #93c5fd;
}
table.sheet .col-location { color: var(--muted); min-width: 140px; }

@media (max-width: 900px) {
    body { padding: 18px; }
    .url-container, .pdf-container { width: 100%; min-width: 100%; }
    .run-button { width: 100%; }
}

</style>
</head>

<body>

<h1>Accessibility Audit Dashboard</h1>
<div class="subtitle">
Enter the webpage URL and upload a PDF screenshot of that page. The Gemini
AI model audits both, app2.py's hardcoded WCAG 2.1/2.2 checkpoints
independently scan the live rendered page, and the two result sets are
merged below.
</div>

<div class="input-section">
<div class="input-row">

<div class="url-container">
<label class="input-label" for="urlInput">Webpage URL</label>
<input type="text" id="urlInput" class="url-input" placeholder="https://example.com"/>
</div>

<div class="pdf-container">
<label class="input-label" for="screenshotInput">Screenshot PDF</label>
<div class="pdf-input-wrapper">
<input type="file" id="screenshotInput" class="pdf-input" accept="application/pdf,.pdf"/>
</div>
</div>

<button id="runBtn" class="run-button" onclick="runAudit()">Run Audit</button>

</div>

<div id="pdfInfo" class="pdf-info">
Selected PDF: <strong id="pdfName"></strong> &nbsp; | &nbsp; Size: <strong id="pdfSize"></strong>
</div>

</div>

<div class="status" id="status"></div>

<div class="meta" id="meta"></div>
<div class="coverage" id="coverage"></div>
<div class="summary" id="summary"></div>
<div class="stats" id="stats"></div>

<div class="tabs" id="tabs">
<button class="tab-btn active" data-tab="dashboard" onclick="switchTab('dashboard')">Dashboard</button>
<button class="tab-btn" data-tab="table" onclick="switchTab('table')">Table View</button>
</div>

<div class="tab-panel active" id="tab-dashboard">
<div class="filters" id="filters"></div>
<div class="source-filters" id="sourceFilters"></div>
<div class="findings" id="findings"></div>
</div>

<div class="tab-panel" id="tab-table">
<div class="table-wrap">
<table class="sheet">
<thead>
<tr>
<th>S.No</th>
<th>Source</th>
<th>Defect Name</th>
<th>WCAG 2.1 &amp; 2.2 Guideline</th>
<th>Severity</th>
<th>Actual Result</th>
<th>Recommendation</th>
<th>Violated HTML Element</th>
<th>Location</th>
</tr>
</thead>
<tbody id="tableBody"></tbody>
</table>
</div>
</div>

<script>

const SEVERITY_COLORS = {
    critical: 'var(--critical)',
    serious: 'var(--serious)',
    moderate: 'var(--moderate)',
    minor: 'var(--minor)'
};

const SOURCE_COLORS = {
    'AI': 'var(--ai)',
    'Script': 'var(--script)',
};

function sourceColor(source) {
    if (source === 'AI') return 'var(--ai)';
    if (source === 'Script') return 'var(--script)';
    return 'var(--both)'; // "AI + Script"
}

let currentFindings = [];
let activeSeverity = 'all';
let activeSource = 'all';

function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = (s === undefined || s === null) ? '' : String(s);
    return div.innerHTML;
}

document.getElementById('screenshotInput').addEventListener('change', function () {
    const file = this.files[0];
    const pdfInfo = document.getElementById('pdfInfo');
    const pdfName = document.getElementById('pdfName');
    const pdfSize = document.getElementById('pdfSize');
    if (!file) { pdfInfo.style.display = 'none'; return; }
    pdfName.textContent = file.name;
    pdfSize.textContent = formatFileSize(file.size);
    pdfInfo.style.display = 'block';
});

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-' + tab).classList.add('active');
}

function applyFilters() {
    document.querySelectorAll('.finding-card').forEach(card => {
        const sevOk = (activeSeverity === 'all' || card.dataset.severity === activeSeverity);
        const srcOk = (activeSource === 'all' || card.dataset.source === activeSource);
        card.style.display = (sevOk && srcOk) ? '' : 'none';
    });
    document.querySelectorAll('#tableBody tr').forEach(row => {
        const sevOk = (activeSeverity === 'all' || row.dataset.severity === activeSeverity);
        const srcOk = (activeSource === 'all' || row.dataset.source === activeSource);
        row.style.display = (sevOk && srcOk) ? '' : 'none';
    });
}

function applyFilter(filter) {
    activeSeverity = filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === filter));
    applyFilters();
}

function applySourceFilter(source) {
    activeSource = source;
    document.querySelectorAll('.source-btn').forEach(b => b.classList.toggle('active', b.dataset.source === source));
    applyFilters();
}

async function runAudit() {
    const url = document.getElementById('urlInput').value.trim();
    const screenshotInput = document.getElementById('screenshotInput');
    const pdfFile = screenshotInput.files[0];
    const statusEl = document.getElementById('status');
    const runBtn = document.getElementById('runBtn');

    if (!url) { statusEl.className = 'status error'; statusEl.textContent = 'Please enter a URL.'; return; }
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        statusEl.className = 'status error'; statusEl.textContent = 'URL must start with http:// or https://'; return;
    }
    if (!pdfFile) { statusEl.className = 'status error'; statusEl.textContent = 'Please upload a PDF screenshot.'; return; }
    if (!pdfFile.name.toLowerCase().endsWith('.pdf')) {
        statusEl.className = 'status error'; statusEl.textContent = 'Only PDF files are allowed.'; return;
    }
    if (pdfFile.type && pdfFile.type !== 'application/pdf') {
        statusEl.className = 'status error'; statusEl.textContent = 'The uploaded file must be a PDF.'; return;
    }
    const maxSize = 20 * 1024 * 1024;
    if (pdfFile.size > maxSize) {
        statusEl.className = 'status error'; statusEl.textContent = 'PDF is too large. Maximum size is 20 MB.'; return;
    }

    runBtn.disabled = true;
    statusEl.className = 'status loading';
    statusEl.innerHTML = '<span class="spinner"></span>Rendering webpage, running script checks and Gemini audit — please wait...';

    try {
        const formData = new FormData();
        formData.append('url', url);
        formData.append('screenshot', pdfFile);

        const response = await fetch('/api/audit', { method: 'POST', body: formData });
        const data = await response.json();

        if (!response.ok || !data.ok) {
            statusEl.className = 'status error';
            statusEl.textContent = data.error || 'Something went wrong running the audit.';
            return;
        }

        statusEl.className = 'status success';
        statusEl.textContent = 'Audit completed successfully.';
        renderResults(data);

    } catch (err) {
        statusEl.className = 'status error';
        statusEl.textContent = 'Could not reach the server: ' + err.message;
    } finally {
        runBtn.disabled = false;
    }
}

function renderResults(data) {
    currentFindings = data.findings || [];
    activeSeverity = 'all';
    activeSource = 'all';

    const meta = document.getElementById('meta');
    meta.style.display = 'block';
    meta.textContent = 'Source: ' + data.source + '  ·  ' + new Date().toLocaleString();

    const cov = data.coverage || {};
    const coverageEl = document.getElementById('coverage');
    coverageEl.style.display = 'block';
    coverageEl.innerHTML =
        '<strong>Checkpoint coverage:</strong> app2.py implements automated/heuristic checks for '
        + (cov.automated + cov.heuristic) + ' of ' + cov.total_checkpoints
        + ' combined WCAG 2.1 + 2.2 (A/AA) checkpoints ('
        + cov.automated + ' automated, ' + cov.heuristic + ' heuristic, '
        + cov.manual_only + ' require manual human review and are not auto-flagged).';

    const summary = document.getElementById('summary');
    summary.style.display = 'block';
    summary.textContent = data.summary || '';

    const counts = { critical: 0, serious: 0, moderate: 0, minor: 0 };
    currentFindings.forEach(f => { if (counts[f.severity] !== undefined) counts[f.severity]++; });

    const statsEl = document.getElementById('stats');
    statsEl.style.display = 'flex';
    statsEl.innerHTML = ['critical', 'serious', 'moderate', 'minor'].map(sev => `
        <div class="stat-card" style="--accent:${SEVERITY_COLORS[sev]}">
            <div class="stat-count">${counts[sev]}</div>
            <div class="stat-label">${sev.charAt(0).toUpperCase() + sev.slice(1)}</div>
        </div>
    `).join('');

    document.getElementById('tabs').style.display = 'flex';

    const filtersEl = document.getElementById('filters');
    filtersEl.style.display = 'flex';
    filtersEl.innerHTML = `
        <button class="filter-btn active" data-filter="all" onclick="applyFilter('all')">All (${currentFindings.length})</button>
        <button class="filter-btn" data-filter="critical" onclick="applyFilter('critical')">Critical (${counts.critical})</button>
        <button class="filter-btn" data-filter="serious" onclick="applyFilter('serious')">Serious (${counts.serious})</button>
        <button class="filter-btn" data-filter="moderate" onclick="applyFilter('moderate')">Moderate (${counts.moderate})</button>
        <button class="filter-btn" data-filter="minor" onclick="applyFilter('minor')">Minor (${counts.minor})</button>
    `;

    const srcCounts = data.counts || { ai_only: 0, script_only: 0, both: 0 };
    const sourceFiltersEl = document.getElementById('sourceFilters');
    sourceFiltersEl.style.display = 'flex';
    sourceFiltersEl.innerHTML = `
        <button class="source-btn active" data-source="all" onclick="applySourceFilter('all')">All sources</button>
        <button class="source-btn" data-source="AI" onclick="applySourceFilter('AI')">AI only (${srcCounts.ai_only})</button>
        <button class="source-btn" data-source="Script" onclick="applySourceFilter('Script')">Script only (${srcCounts.script_only})</button>
        <button class="source-btn" data-source="AI + Script" onclick="applySourceFilter('AI + Script')">Confirmed by both (${srcCounts.both})</button>
    `;

    const findingsEl = document.getElementById('findings');
    if (currentFindings.length) {
        findingsEl.innerHTML = currentFindings.map(f => `
            <div class="finding-card" data-severity="${escapeHtml(f.severity)}" data-source="${escapeHtml(f.source)}">
                <div class="finding-header">
                    <span class="badge" style="background:${SEVERITY_COLORS[f.severity] || '#6b7280'}">
                        ${escapeHtml((f.severity || '').toUpperCase())}
                    </span>
                    <span class="source-badge" style="background:${sourceColor(f.source)}">${escapeHtml(f.source)}</span>
                    <span class="criterion">${escapeHtml(f.wcag_criterion)}</span>
                </div>
                <div class="finding-element">
                    <code>${escapeHtml(f.element)}</code>
                    <span class="loc">${escapeHtml(f.location)}</span>
                </div>
                <div class="finding-row"><strong>Defect:</strong> ${escapeHtml(f.defect)}</div>
                <div class="finding-row"><strong>Actual result:</strong> ${escapeHtml(f.actual_result)}</div>
                <div class="finding-row"><strong>User impact:</strong> ${escapeHtml(f.user_impact)}</div>
                <div class="finding-row remediation"><strong>Remediation:</strong> ${escapeHtml(f.remediation)}</div>
            </div>
        `).join('');
    } else {
        findingsEl.innerHTML = '<div class="empty">No accessibility issues found.</div>';
    }

    const tableBody = document.getElementById('tableBody');
    if (currentFindings.length) {
        tableBody.innerHTML = currentFindings.map((f, i) => `
            <tr data-severity="${escapeHtml(f.severity)}" data-source="${escapeHtml(f.source)}">
                <td class="col-sno">${i + 1}</td>
                <td><span class="source-badge" style="background:${sourceColor(f.source)}">${escapeHtml(f.source)}</span></td>
                <td>${escapeHtml(f.defect)}</td>
                <td>${escapeHtml(f.wcag_criterion)}</td>
                <td><span class="badge" style="background:${SEVERITY_COLORS[f.severity] || '#6b7280'}">${escapeHtml((f.severity || '').toUpperCase())}</span></td>
                <td>${escapeHtml(f.actual_result)}</td>
                <td>${escapeHtml(f.remediation)}</td>
                <td class="col-element"><code>${escapeHtml(f.element)}</code></td>
                <td class="col-location">${escapeHtml(f.location)}</td>
            </tr>
        `).join('');
    } else {
        tableBody.innerHTML = '<tr><td colspan="9" class="empty">No accessibility issues found.</td></tr>';
    }
}

document.getElementById('urlInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') runAudit();
});

</script>

</body>
</html>
"""


# ============================================================
# OPEN CHROME
# ============================================================

def open_in_chrome(url: str):
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser"):
        try:
            webbrowser.get(name).open(url)
            return
        except webbrowser.Error:
            continue
    webbrowser.open(url)


# ============================================================
# MAIN
# ============================================================

def main():
    if not API_KEY:
        print(
            "\n"
            "==================================================\n"
            "WARNING: GEMINI_API_KEY IS NOT SET\n"
            "==================================================\n"
            "\n"
            "Windows PowerShell:\n"
            "\n"
            '$env:GEMINI_API_KEY="your-key-here"\n'
            "\n"
            "Then restart the server.\n"
        )

    url = f"http://{HOST}:{PORT}"
    threading.Timer(1.2, open_in_chrome, args=[url]).start()
    print(f"Starting dashboard at {url} ...")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()