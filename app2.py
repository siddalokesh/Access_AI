"""
app2.py — Programmatic WCAG 2.1 & 2.2 (Level A + AA) Checkpoint Auditor
(full 55/55 coverage edition)

This module does NOT call any AI model. It inspects a *live* Playwright
page (the same rendered page app1.py already loaded) and runs one
hardcoded check per WCAG success criterion (SC) — all 55 combined
2.1 + 2.2 (A/AA) checkpoints now have an implemented check.

------------------------------------------------------------------
Read this before trusting any single finding:
------------------------------------------------------------------
Every finding this module returns carries a "verification" field:

  "Automated"  -> deterministic, code can be highly confident
                  (e.g. a required <title> is empty, an id is
                  duplicated, computed contrast ratio is measurable).

  "Heuristic — verify manually"
               -> pattern-based detection. The pattern is a real,
                  known indicator of the problem, but it can produce
                  false positives/negatives because the underlying
                  success criterion fundamentally requires human
                  judgment, multi-page comparison, or behavioral/
                  interaction testing that a single static/DOM pass
                  cannot fully replicate (e.g. "does this heading
                  make sense", "is navigation consistent across the
                  whole site", "is this shortcut remappable").

Nothing here is invented or scored as a pass/fail guarantee — a
"Heuristic" finding means "the script noticed something suspicious
here; a person should look at it," not "this is definitely broken."
That distinction is preserved end-to-end: app1.py surfaces the
`verification` field in the dashboard as its own column/badge.

On top of the 55 per-checkpoint checks, a handful of extra
"doubt-raising" checks (see the EXTRA CHECKS section below) run
additional, more aggressive pattern scans against existing
checkpoints — e.g. very short color-only markers, repeated ambiguous
link text, iframes/aria-hidden misuse — so that a plausible-but-
unconfirmed issue still produces a finding (always tagged Heuristic
unless it's a deterministic markup bug) instead of staying silent.
The goal is to bias toward flagging more, and let a human rule
things out, rather than silently pass over anything doubtful.

Call run_all_checks(page) to get a list of findings using the SAME
schema as app1.py's Gemini findings (plus `verification`), so they
merge directly:
    element, location, wcag_criterion, severity, defect,
    actual_result, user_impact, remediation, verification
plus a "source": "Script" tag added by app1.py during merge.
"""

import re


# ============================================================
# CHECKPOINT REGISTRY (all 55 combined SC, 2.1 + 2.2, A + AA)
# method: "automated" -> deterministic code check
#         "heuristic"  -> pattern-based, needs manual confirmation
# ============================================================

CHECKPOINTS = {
    "1.1.1":  {"name": "Non-text Content",                          "level": "A",  "version": "2.1", "method": "automated"},
    "1.2.1":  {"name": "Audio-only and Video-only (Prerecorded)",   "level": "A",  "version": "2.1", "method": "heuristic"},
    "1.2.2":  {"name": "Captions (Prerecorded)",                    "level": "A",  "version": "2.1", "method": "heuristic"},
    "1.2.3":  {"name": "Audio Description or Media Alternative",    "level": "A",  "version": "2.1", "method": "heuristic"},
    "1.2.4":  {"name": "Captions (Live)",                           "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.2.5":  {"name": "Audio Description (Prerecorded)",           "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.3.1":  {"name": "Info and Relationships",                    "level": "A",  "version": "2.1", "method": "automated"},
    "1.3.2":  {"name": "Meaningful Sequence",                       "level": "A",  "version": "2.1", "method": "heuristic"},
    "1.3.3":  {"name": "Sensory Characteristics",                   "level": "A",  "version": "2.1", "method": "heuristic"},
    "1.3.4":  {"name": "Orientation",                               "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.3.5":  {"name": "Identify Input Purpose",                    "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.4.1":  {"name": "Use of Color",                              "level": "A",  "version": "2.1", "method": "heuristic"},
    "1.4.2":  {"name": "Audio Control",                             "level": "A",  "version": "2.1", "method": "automated"},
    "1.4.3":  {"name": "Contrast (Minimum)",                        "level": "AA", "version": "2.1", "method": "automated"},
    "1.4.4":  {"name": "Resize Text",                               "level": "AA", "version": "2.1", "method": "automated"},
    "1.4.5":  {"name": "Images of Text",                            "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.4.10": {"name": "Reflow",                                     "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.4.11": {"name": "Non-text Contrast",                         "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.4.12": {"name": "Text Spacing",                              "level": "AA", "version": "2.1", "method": "heuristic"},
    "1.4.13": {"name": "Content on Hover or Focus",                 "level": "AA", "version": "2.1", "method": "heuristic"},
    "2.1.1":  {"name": "Keyboard",                                  "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.1.2":  {"name": "No Keyboard Trap",                          "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.1.4":  {"name": "Character Key Shortcuts",                   "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.2.1":  {"name": "Timing Adjustable",                         "level": "A",  "version": "2.1", "method": "automated"},
    "2.2.2":  {"name": "Pause, Stop, Hide",                         "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.3.1":  {"name": "Three Flashes or Below Threshold",          "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.4.1":  {"name": "Bypass Blocks",                             "level": "A",  "version": "2.1", "method": "automated"},
    "2.4.2":  {"name": "Page Titled",                               "level": "A",  "version": "2.1", "method": "automated"},
    "2.4.3":  {"name": "Focus Order",                               "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.4.4":  {"name": "Link Purpose (In Context)",                 "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.4.5":  {"name": "Multiple Ways",                             "level": "AA", "version": "2.1", "method": "heuristic"},
    "2.4.6":  {"name": "Headings and Labels",                       "level": "AA", "version": "2.1", "method": "automated"},
    "2.4.7":  {"name": "Focus Visible",                             "level": "AA", "version": "2.1", "method": "heuristic"},
    "2.5.1":  {"name": "Pointer Gestures",                          "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.5.2":  {"name": "Pointer Cancellation",                      "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.5.3":  {"name": "Label in Name",                             "level": "A",  "version": "2.1", "method": "heuristic"},
    "2.5.4":  {"name": "Motion Actuation",                          "level": "A",  "version": "2.1", "method": "heuristic"},
    "3.1.1":  {"name": "Language of Page",                          "level": "A",  "version": "2.1", "method": "automated"},
    "3.1.2":  {"name": "Language of Parts",                         "level": "AA", "version": "2.1", "method": "heuristic"},
    "3.2.1":  {"name": "On Focus",                                  "level": "A",  "version": "2.1", "method": "automated"},
    "3.2.2":  {"name": "On Input",                                  "level": "A",  "version": "2.1", "method": "automated"},
    "3.2.3":  {"name": "Consistent Navigation",                     "level": "AA", "version": "2.1", "method": "heuristic"},
    "3.2.4":  {"name": "Consistent Identification",                 "level": "AA", "version": "2.1", "method": "heuristic"},
    "3.3.1":  {"name": "Error Identification",                      "level": "A",  "version": "2.1", "method": "heuristic"},
    "3.3.2":  {"name": "Labels or Instructions",                    "level": "A",  "version": "2.1", "method": "automated"},
    "3.3.3":  {"name": "Error Suggestion",                          "level": "AA", "version": "2.1", "method": "heuristic"},
    "3.3.4":  {"name": "Error Prevention (Legal, Financial, Data)", "level": "AA", "version": "2.1", "method": "heuristic"},
    "4.1.1":  {"name": "Parsing",                                   "level": "A",  "version": "2.1", "method": "automated"},
    "4.1.2":  {"name": "Name, Role, Value",                         "level": "A",  "version": "2.1", "method": "automated"},
    "4.1.3":  {"name": "Status Messages",                           "level": "AA", "version": "2.1", "method": "heuristic"},
    # ---- New in WCAG 2.2 (brings combined A+AA total to 55) ----
    "2.4.11": {"name": "Focus Not Obscured (Minimum)",              "level": "AA", "version": "2.2", "method": "heuristic"},
    "2.5.7":  {"name": "Dragging Movements",                        "level": "AA", "version": "2.2", "method": "heuristic"},
    "2.5.8":  {"name": "Target Size (Minimum)",                     "level": "AA", "version": "2.2", "method": "automated"},
    "3.2.6":  {"name": "Consistent Help",                           "level": "A",  "version": "2.2", "method": "heuristic"},
    "3.3.7":  {"name": "Redundant Entry",                           "level": "A",  "version": "2.2", "method": "heuristic"},
}

KNOWN_ARIA_ROLES = {
    "alert", "alertdialog", "application", "article", "banner", "button",
    "cell", "checkbox", "columnheader", "combobox", "complementary",
    "contentinfo", "definition", "dialog", "directory", "document",
    "feed", "figure", "form", "grid", "gridcell", "group", "heading",
    "img", "link", "list", "listbox", "listitem", "log", "main",
    "marquee", "math", "menu", "menubar", "menuitem", "menuitemcheckbox",
    "menuitemradio", "navigation", "none", "note", "option", "presentation",
    "progressbar", "radio", "radiogroup", "region", "row", "rowgroup",
    "rowheader", "scrollbar", "search", "searchbox", "separator", "slider",
    "spinbutton", "status", "switch", "tab", "table", "tablist", "tabpanel",
    "term", "textbox", "timer", "toolbar", "tooltip", "tree", "treegrid",
    "treeitem",
}

AUTOCOMPLETE_HINTS = {
    "email": "email", "e-mail": "email",
    "phone": "tel", "tel": "tel", "mobile": "tel",
    "name": "name", "fullname": "name", "full_name": "name",
    "firstname": "given-name", "first_name": "given-name",
    "lastname": "family-name", "last_name": "family-name",
    "address": "street-address", "city": "address-level2",
    "zip": "postal-code", "postal": "postal-code", "postcode": "postal-code",
    "country": "country", "cc-number": "cc-number", "card": "cc-number",
    "username": "username", "password": "current-password",
}

LOW_INFO_LINK_TEXT = {
    "click here", "here", "read more", "more", "link", "learn more",
    "click", "this link", "more info", "details",
}

SENSITIVE_FIELD_HINTS = ("password", "ssn", "credit", "card", "payment", "delete", "cvv", "iban", "account")

SHAPE_COLOR_WORDS = r"(red|green|blue|yellow|orange|purple|round|square|circular)"
SENSORY_PATTERN = re.compile(
    r"\b(click|press|select|tap|choose)\b[^.]{0,25}\b" + SHAPE_COLOR_WORDS + r"\b"
    r"|\b" + SHAPE_COLOR_WORDS + r"\b[^.]{0,25}\b(button|icon|link)\b",
    re.IGNORECASE,
)


def _finding(element, location, sc, severity, defect, actual, impact, remediation, force_heuristic=False):
    meta = CHECKPOINTS.get(sc, {})
    name = meta.get("name", "")
    method = "heuristic" if force_heuristic else meta.get("method", "heuristic")
    verification = "Automated — high confidence" if method == "automated" else "Heuristic — verify manually"
    return {
        "element": element,
        "location": location,
        "wcag_criterion": f"{sc} {name}".strip(),
        "severity": severity,
        "defect": defect,
        "actual_result": actual,
        "user_impact": impact,
        "remediation": remediation,
        "verification": verification,
    }



# ============================================================
# UPDATED CHECK FUNCTIONS
# ============================================================
# These functions keep the original public names/schema, but use broader
# rendered-DOM, CSS, ARIA, keyboard, and interaction heuristics. They are
# intentionally conservative about claims that require human judgement.


def _collect_accessibility_tree(page):
    """Collect the browser accessibility tree/ARIA snapshot when supported.

    Playwright versions that expose aria_snapshot() are preferred. A small
    DOM/ARIA fallback is used so the checker still has structured accessibility
    information on older Playwright versions.
    """
    try:
        aria_snapshot = getattr(page, "aria_snapshot", None)
        if callable(aria_snapshot):
            try:
                return aria_snapshot(timeout=5000)
            except TypeError:
                return aria_snapshot()
    except Exception:
        pass

    try:
        return page.evaluate(r"""
        () => {
            const clean = s => (s || '').replace(/\s+/g, ' ').trim();
            const implicitRole = el => {
                const r = el.getAttribute('role');
                if (r) return r.split(/\s+/)[0];
                const t = el.tagName.toLowerCase();
                if (t === 'a' && el.hasAttribute('href')) return 'link';
                if (t === 'button') return 'button';
                if (/^h[1-6]$/.test(t)) return 'heading';
                if (t === 'img') return 'img';
                if (t === 'nav') return 'navigation';
                if (t === 'main') return 'main';
                if (t === 'header') return 'banner';
                if (t === 'footer') return 'contentinfo';
                if (t === 'form') return 'form';
                if (t === 'input' || t === 'textarea') return 'textbox';
                if (t === 'select') return 'combobox';
                return null;
            };
            const name = el => {
                const labelled = (el.getAttribute('aria-labelledby') || '')
                    .split(/\s+/).filter(Boolean)
                    .map(id => document.getElementById(id)?.innerText || '')
                    .join(' ');
                return clean(labelled || el.getAttribute('aria-label') ||
                    el.getAttribute('alt') || el.innerText || el.textContent);
            };
            return Array.from(document.querySelectorAll('body *'))
                .filter(el => implicitRole(el) || el.hasAttribute('aria-label') ||
                              el.hasAttribute('aria-labelledby') || el.hasAttribute('tabindex'))
                .slice(0, 1000)
                .map(el => ({
                    role: implicitRole(el),
                    name: name(el).slice(0, 200),
                    tag: el.tagName.toLowerCase(),
                    aria: Object.fromEntries(Array.from(el.attributes)
                        .filter(a => a.name.startsWith('aria-'))
                        .map(a => [a.name, a.value]))
                }));
        }
        """)
    except Exception:
        return ""


def collect_page_inspection_data(page):
    """Collect structured accessibility, computed-style and geometry data.

    This is read-only: it does not modify the target webpage DOM, CSS, or
    application state. The same live Playwright page is used by app2.py.
    """
    accessibility_tree = _collect_accessibility_tree(page)

    try:
        elements = page.evaluate(r"""
        () => {
            const clean = s => (s || '').replace(/\s+/g, ' ').trim();
            const selectorFor = el => {
                if (el.id) return '#' + CSS.escape(el.id);
                let s = el.tagName.toLowerCase();
                if (el.getAttribute('name')) s += '[name="' + el.getAttribute('name').replace(/"/g, '\\"') + '"]';
                if (el.classList.length) s += '.' + Array.from(el.classList).slice(0, 2).map(CSS.escape).join('.');
                return s;
            };
            const elements = document.querySelectorAll(
                'a,button,input,select,textarea,summary,' +
                'h1,h2,h3,h4,h5,h6,img,video,audio,form,table,th,td,' +
                'header,nav,main,aside,footer,[role],[tabindex]'
            );
            return Array.from(elements).slice(0, 1200).map((el, index) => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                const visible = s.display !== 'none' && s.visibility !== 'hidden' &&
                    parseFloat(s.opacity || '1') > 0 && r.width > 0 && r.height > 0;
                const attrs = {};
                for (const a of el.attributes) {
                    if (a.name === 'class' || a.name.startsWith('data-')) continue;
                    attrs[a.name] = a.value;
                }
                const aria = {};
                for (const a of el.attributes) {
                    if (a.name.startsWith('aria-')) aria[a.name] = a.value;
                }
                return {
                    index,
                    selector: selectorFor(el),
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role'),
                    text: clean(el.innerText || el.textContent).slice(0, 300),
                    accessible_name: clean(
                        el.getAttribute('aria-label') ||
                        el.getAttribute('alt') ||
                        el.innerText || el.textContent
                    ).slice(0, 200),
                    visible,
                    enabled: !el.disabled,
                    focused: document.activeElement === el,
                    attributes: attrs,
                    aria,
                    geometry: {
                        x: +r.x.toFixed(2), y: +r.y.toFixed(2),
                        top: +r.top.toFixed(2), right: +r.right.toFixed(2),
                        bottom: +r.bottom.toFixed(2), left: +r.left.toFixed(2),
                        width: +r.width.toFixed(2), height: +r.height.toFixed(2)
                    },
                    computed_style: {
                        display: s.display,
                        visibility: s.visibility,
                        opacity: s.opacity,
                        color: s.color,
                        backgroundColor: s.backgroundColor,
                        fontFamily: s.fontFamily,
                        fontSize: s.fontSize,
                        fontWeight: s.fontWeight,
                        lineHeight: s.lineHeight,
                        letterSpacing: s.letterSpacing,
                        wordSpacing: s.wordSpacing,
                        textAlign: s.textAlign,
                        textDecoration: s.textDecoration,
                        padding: s.padding,
                        margin: s.margin,
                        border: s.border,
                        borderColor: s.borderColor,
                        borderWidth: s.borderWidth,
                        outline: s.outline,
                        outlineColor: s.outlineColor,
                        outlineWidth: s.outlineWidth,
                        boxShadow: s.boxShadow,
                        position: s.position,
                        zIndex: s.zIndex,
                        overflow: s.overflow,
                        overflowX: s.overflowX,
                        overflowY: s.overflowY,
                        cursor: s.cursor,
                        transform: s.transform
                    }
                };
            });
        }
        """)
    except Exception:
        elements = []

    try:
        viewport = page.evaluate("() => ({width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, devicePixelRatio: devicePixelRatio})")
    except Exception:
        viewport = {}

    return {
        "accessibility_tree": accessibility_tree,
        "elements": elements,
        "viewport": viewport,
    }


def _get_inspection_data(page):
    data = getattr(page, "_app2_inspection_data", None)
    if data is None:
        data = collect_page_inspection_data(page)
        try:
            setattr(page, "_app2_inspection_data", data)
        except Exception:
            pass
    return data


def _safe_text(el):
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        try:
            return (el.text_content() or "").strip()
        except Exception:
            return ""


def _attr(el, name):
    try:
        return el.get_attribute(name)
    except Exception:
        return None


def _visible(el):
    try:
        return bool(el.is_visible())
    except Exception:
        try:
            return bool(el.evaluate("e => { const s=getComputedStyle(e),r=e.getBoundingClientRect(); return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; }"))
        except Exception:
            return True


def _accessible_name(page, el):
    try:
        return page.evaluate("""el => {
          const clean = s => (s || '').replace(/\\s+/g,' ').trim();
          const labelled = id => id ? document.getElementById(id) : null;
          const ids = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
          if (ids.length) {
            const t = ids.map(id => labelled(id)?.innerText || labelled(id)?.textContent || '').join(' ');
            if (clean(t)) return clean(t);
          }
          const aria = clean(el.getAttribute('aria-label'));
          if (aria) return aria;
          if (el.tagName === 'IMG') return clean(el.getAttribute('alt'));
          const img = el.querySelector && el.querySelector('img');
          if (img && clean(img.getAttribute('alt'))) return clean(img.getAttribute('alt'));
          const title = clean(el.getAttribute('title'));
          const text = clean(el.innerText || el.textContent);
          if (text) return text;
          return title;
        }""", el)
    except Exception:
        return _safe_text(el) or (_attr(el, 'aria-label') or _attr(el, 'title') or '')


def _has_programmatic_label(page, el):
    try:
        return bool(page.evaluate("""el => {
          const id=el.id;
          if (id && document.querySelector(`label[for="${CSS.escape(id)}"]`)) return true;
          if (el.closest('label')) return true;
          if (el.getAttribute('aria-label')) return true;
          if (el.getAttribute('aria-labelledby')) {
            return el.getAttribute('aria-labelledby').split(/\\s+/).some(id => document.getElementById(id));
          }
          return false;
        }""", el))
    except Exception:
        return bool(_attr(el,'aria-label') or _attr(el,'aria-labelledby'))


def _selector(el):
    try:
        return el.evaluate("e => { if(e.id) return '#'+e.id; let s=e.tagName.toLowerCase(); if(e.classList.length) s+='.'+[...e.classList].slice(0,2).join('.'); return s; }")
    except Exception:
        return "element"


def _is_natively_interactive(el):
    try:
        return bool(el.evaluate("e => /^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(e.tagName) || e.hasAttribute('contenteditable')"))
    except Exception:
        return False


def _finding_for(el, page, sc, severity, defect, actual, impact, remediation, force_heuristic=False):
    return _finding(_selector(el), _selector(el), sc, severity, defect, actual, impact, remediation, force_heuristic=force_heuristic)


def check_1_1_1_non_text_content(page):
    findings=[]
    for img in page.query_selector_all('img'):
        alt=_attr(img,'alt')
        if alt is None:
            findings.append(_finding_for(img,page,'1.1.1','serious','Image is missing an alt attribute','No alt attribute is present on the rendered image.','Screen-reader users may receive no useful text alternative.','Add accurate alt text, or alt="" when the image is genuinely decorative.'))
        elif alt.strip() and re.match(r'^(image|img|photo|picture|graphic|icon)[ _-]?\d*\.?\w*$',alt.strip(),re.I):
            findings.append(_finding_for(img,page,'1.1.1','minor','Alt text appears to be a non-descriptive filename or generic label',f'Alt text is "{alt.strip()}".','Users may not learn the purpose or information conveyed by the image.','Replace generic/filename-like alt text with a concise description of the image purpose.',True))
    for el in page.query_selector_all('input[type=image], area'):
        if not (_attr(el,'alt') or '').strip():
            findings.append(_finding_for(el,page,'1.1.1','serious','Image control/map area has no text alternative','No non-empty alt attribute was found.','Assistive-technology users cannot determine the purpose of the image control.','Provide a concise alt attribute describing the action or linked region.'))
    for el in page.query_selector_all('svg'):
        if _attr(el,'aria-hidden')!='true' and not _attr(el,'role') and not _attr(el,'aria-label') and not _safe_text(el):
            # Only flag standalone SVGs that appear interactive or meaningful.
            if _attr(el,'tabindex') is not None or el.query_selector('title'):
                continue
    return findings


def check_1_2_1_audio_video_only(page):
    findings=[]
    for el in page.query_selector_all('audio, video'):
        if _attr(el,'aria-hidden')=='true' or _attr(el,'data-decorative') is not None: continue
        transcript = page.evaluate("""el => { let n=el; for(let i=0;i<4&&n;i++,n=n.parentElement){ const t=(n.innerText||'').toLowerCase(); if(/transcript|text alternative|audio description/.test(t)) return true; } return false; }""", el)
        if not transcript:
            tag=el.evaluate('e=>e.tagName.toLowerCase()')
            findings.append(_finding_for(el,page,'1.2.1','moderate','Media has no nearby transcript or equivalent text alternative',f'No transcript/alternative text was detected near the <{tag}> element.','People who cannot access the media may miss its information.','Provide a transcript for audio-only content or a complete text alternative for video-only content.',True))
    return findings


def check_1_2_2_captions_prerecorded(page):
    findings=[]
    for video in page.query_selector_all('video'):
        tracks=video.query_selector_all('track')
        caption=video.query_selector('track[kind="captions"], track[kind="subtitles"]')
        if tracks and not caption:
            findings.append(_finding_for(video,page,'1.2.2','serious','Video has tracks but no captions track','Track elements exist, but none has kind="captions" or kind="subtitles".','Deaf and hard-of-hearing users may miss spoken content.','Provide synchronized captions for prerecorded video.',True))
        elif not tracks:
            findings.append(_finding_for(video,page,'1.2.2','serious','No caption track detected for prerecorded video','No <track> child was found.','Deaf and hard-of-hearing users may miss the audio information.','Add a synchronized <track kind="captions"> resource.',True))
    return findings


def check_1_2_3_audio_description(page):
    findings=[]
    for video in page.query_selector_all('video'):
        has_desc=bool(video.query_selector('track[kind="descriptions"]'))
        has_alt=bool(page.evaluate("el => { let n=el.parentElement; for(let i=0;i<4&&n;i++,n=n.parentElement){ const t=(n.innerText||'').toLowerCase(); if(/audio description|media alternative|transcript/.test(t)) return true;} return false; }",video))
        if not has_desc and not has_alt:
            findings.append(_finding_for(video,page,'1.2.3','moderate','Video has no detected audio description or media alternative','No description track or nearby full-text alternative was detected.','Blind users may miss important visual information.','Provide an audio-described version or a complete media alternative.',True))
    return findings


def check_1_2_4_captions_live(page):
    findings=[]
    for el in page.query_selector_all('[class*="live" i],[id*="live" i]'):
        if el.query_selector('video, audio, iframe') and not el.query_selector('track[kind="captions"], [class*="caption" i], [aria-label*="caption" i]'):
            findings.append(_finding_for(el,page,'1.2.4','minor','Possible live media has no detected caption mechanism','The rendered live-media container has no obvious caption track/control.','Live spoken content may be inaccessible to deaf or hard-of-hearing users.','If this is live media, provide synchronized real-time captions.',True))
    return findings


def check_1_2_5_audio_description_prerecorded(page):
    findings=[]
    for video in page.query_selector_all('video'):
        if not video.query_selector('track[kind="descriptions"]'):
            findings.append(_finding_for(video,page,'1.2.5','moderate','No dedicated audio-description track detected','No <track kind="descriptions"> was found.','Blind users may miss visual-only information.','Provide an audio-described version of prerecorded video.',True))
    return findings


def check_1_3_1_info_relationships(page):
    findings=[]
    for el in page.query_selector_all('input:not([type="hidden"]), select, textarea'):
        typ=(_attr(el,'type') or '').lower()
        if typ in {'submit','button','reset','image','checkbox','radio','range','color'}: continue
        if not _has_programmatic_label(page,el):
            findings.append(_finding_for(el,page,'1.3.1','serious','Form control has no programmatic label','No associated label, aria-label, or aria-labelledby was detected.','Screen-reader users may not know what information to enter.','Associate the control with a visible <label>, aria-label, or aria-labelledby.'))
    for table in page.query_selector_all('table'):
        if table.query_selector('td') and not table.query_selector('th'):
            findings.append(_finding_for(table,page,'1.3.1','moderate','Data table has no header cells','The table contains data cells but no <th> elements.','Assistive technology cannot reliably associate data with column/row headers.','Use appropriate <th> cells and scope/headers relationships.'))
    for dl in page.query_selector_all('dl'):
        if dl.query_selector('dd') and not dl.query_selector('dt'):
            findings.append(_finding_for(dl,page,'1.3.1','moderate','Description list lacks <dt> terms','<dd> elements exist without corresponding <dt> elements.','The relationship between terms and descriptions is lost.','Use <dt> for terms and <dd> for their descriptions.',True))
    return findings


def check_1_3_2_meaningful_sequence(page):
    findings=[]
    for el in page.query_selector_all('[tabindex]'):
        try: v=int(_attr(el,'tabindex'))
        except: continue
        if v>0:
            findings.append(_finding_for(el,page,'1.3.2','moderate','Positive tabindex can override the meaningful DOM sequence',f'tabindex="{v}" is used.','Keyboard and assistive-technology users may encounter content in an unexpected order.','Remove positive tabindex values and use logical source order.',True))
    # Detect obvious CSS visual reordering.
    for el in page.query_selector_all('[style*="order"], [style*="flex-direction"]'):
        style=(_attr(el,'style') or '').lower()
        if 'order:' in style or 'flex-direction:row-reverse' in style or 'flex-direction:column-reverse' in style:
            findings.append(_finding_for(el,page,'1.3.2','minor','CSS may visually reorder content',f'Inline style contains a visual-ordering rule: {style[:100]}','Visual order may differ from DOM/reading order.','Verify that the programmatic reading sequence remains meaningful.',True))
    return findings


def check_1_3_3_sensory_characteristics(page):
    findings=[]
    try: text=page.evaluate('()=>document.body.innerText').lower()
    except: text=''
    patterns=[r'click the (red|green|blue|yellow|orange|purple)',r'press the (left|right|top|bottom) button',r'select the (round|square|circular) (button|icon|link)',r'(red|green|blue) button']
    for p in patterns:
        for m in list(re.finditer(p,text,re.I))[:5]:
            sn=text[max(0,m.start()-25):m.end()+25]
            findings.append(_finding('body text', 'instruction text', '1.3.3','minor','Instruction may depend on sensory characteristics',f'Found potentially shape/color/directional-only instruction: "...{sn}...".','Users who cannot perceive color, shape, or position may not identify the intended control.','Include a non-sensory identifier such as the control label or text.',True))
    return findings


def check_1_3_4_orientation(page):
    findings=[]
    css=page.content().lower()
    if re.search(r'@media\s*\([^)]*orientation\s*:\s*(portrait|landscape)[^)]*\)',css) and re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',css):
        findings.append(_finding('<style>','CSS media queries','1.3.4','moderate','Orientation-specific CSS may hide content','The page contains orientation media queries together with hiding rules.','Users may lose content/functionality when the device is rotated or fixed in one orientation.','Verify that content and functionality remain available in both orientations unless a documented exception applies.',True))
    return findings


def check_1_3_5_identify_input_purpose(page):
    findings=[]
    hints=AUTOCOMPLETE_HINTS
    for el in page.query_selector_all('input, select, textarea'):
        typ=(_attr(el,'type') or 'text').lower()
        if typ in {'hidden','submit','button','reset','checkbox','radio','image','file'}: continue
        if _attr(el,'autocomplete'): continue
        probe=' '.join(filter(None,[_attr(el,'name'),_attr(el,'id'),_attr(el,'placeholder'),_safe_text(el)])).lower()
        for hint,token in hints.items():
            if re.search(r'\b'+re.escape(hint)+r'\b',probe):
                findings.append(_finding_for(el,page,'1.3.5','minor','Common personal-information field lacks autocomplete purpose','Field appears to collect '+hint+' information but has no autocomplete attribute.','Autofill and assistive technologies cannot reliably identify the input purpose.','Use the appropriate autocomplete token, such as autocomplete="email" or "given-name".',True)); break
    return findings


def check_1_4_1_use_of_color(page):
    findings=[]
    for el in page.query_selector_all('[class*="error" i],[class*="success" i],[class*="warning" i],[class*="required" i]'):
        txt=_safe_text(el)
        aria=_attr(el,'aria-label') or _attr(el,'aria-describedby')
        if len(txt)<2 and not aria and not el.query_selector('svg,img,[aria-label]'):
            findings.append(_finding_for(el,page,'1.4.1','minor','State indicator may rely on color alone','A state-related element has little/no textual or semantic content.','Users who cannot perceive the color may miss the state or instruction.','Provide text, an accessible icon, or another non-color indicator.',True))
    return findings


def check_1_4_2_audio_control(page):
    findings=[]
    for el in page.query_selector_all('audio[autoplay],video[autoplay]'):
        if _attr(el,'muted') is None and not el.query_selector('button,[aria-label*="pause" i],[aria-label*="stop" i]') and _attr(el,'controls') is None:
            findings.append(_finding_for(el,page,'1.4.2','critical','Auto-playing audio/video has no detected pause/stop/mute mechanism','The media element uses autoplay without controls or a detectable custom control.','Unexpected audio can interfere with screen readers and other assistive technology.','Avoid autoplay, or provide an immediately available mechanism to pause/stop/mute the audio.'))
    return findings


def check_1_4_3_contrast(page):
    findings=[]
    try:
        results=page.evaluate("""() => {
          const lum=c=>{c=c.map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return .2126*c[0]+.7152*c[1]+.0722*c[2]};
          const rgb=s=>{const m=s.match(/rgba?\\(([^)]+)\\)/);if(!m)return null;const p=m[1].split(',').map(x=>parseFloat(x.trim()));return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}};
          const out=[]; for(const e of document.querySelectorAll('body *')){if(out.length>=100)break; const t=(e.innerText||'').trim(); if(!t||e.children.length)continue; const s=getComputedStyle(e); if(s.display==='none'||s.visibility==='hidden')continue; const fg=rgb(s.color); if(!fg)continue; let n=e,bg=null; while(n&&n!==document.documentElement){const b=rgb(getComputedStyle(n).backgroundColor); if(b&&b.a>0){bg=b;break} n=n.parentElement} if(!bg)bg={r:255,g:255,b:255,a:1}; const ratio=(Math.max(lum([fg.r,fg.g,fg.b]),lum([bg.r,bg.g,bg.b]))+.05)/(Math.min(lum([fg.r,fg.g,fg.b]),lum([bg.r,bg.g,bg.b]))+.05); const fs=parseFloat(s.fontSize)||16,large=fs>=24||(fs>=18.66&&parseInt(s.fontWeight)>=700),need=large?3:4.5; if(ratio<need)out.push({tag:e.tagName.toLowerCase(),text:t.slice(0,60),ratio:+ratio.toFixed(2),need});} return out;
        }""")
    except: results=[]
    for r in results:
        findings.append(_finding(f'<{r["tag"]}> "{r["text"]}"','computed text contrast','1.4.3','serious','Text contrast is below WCAG AA threshold',f'Estimated contrast is {r["ratio"]}:1; required threshold is {r["need"]}:1.','Low-vision users may have difficulty reading the text.','Increase foreground/background contrast or change the text presentation.'))
    return findings


def check_1_4_4_resize_text(page):
    findings=[]
    vp=page.query_selector('meta[name="viewport"]')
    if vp:
        c=(_attr(vp,'content') or '').replace(' ','').lower()
        if re.search(r'user-scalable=no|maximum-scale=(0?\.?[01](?:\.\d+)?)\b',c):
            findings.append(_finding('<meta name="viewport">','document head','1.4.4','serious','Viewport configuration may prevent text resizing/zoom',f'Viewport content is "{c}".','Users with low vision may be unable to enlarge content sufficiently.','Remove user-scalable=no and restrictive maximum-scale values.',True))
    return findings


def check_1_4_5_images_of_text(page):
    findings=[]
    for img in page.query_selector_all('img'):
        alt=(_attr(img,'alt') or '').strip(); src=(_attr(img,'src') or '').lower()
        if alt and len(alt)>45 and ('logo' not in alt.lower()) and not re.search(r'chart|graph|diagram|screenshot',alt,re.I):
            findings.append(_finding_for(img,page,'1.4.5','minor','Image may contain text that should be real text',f'Alt text is unusually long ({len(alt)} characters).','Text embedded in images may not resize/reflow or remain selectable.','Prefer real HTML text; verify exceptions such as logos and essential imagery.',True))
        elif re.search(r'(text|heading|title|label|banner|button)',src,re.I) and alt:
            findings.append(_finding_for(img,page,'1.4.5','minor','Image filename suggests text is embedded in the image',f'Image source name is "{src.split("/")[-1][:80]}".','Important text baked into an image may be difficult to resize or restyle.','Use real text where practical and verify any permitted exception.',True))
    return findings


def check_1_4_10_reflow(page):
    findings=[]
    try:
        page.set_viewport_size({'width':320,'height':800})
        page.wait_for_timeout(100)
        overflow=page.evaluate('()=>({w:document.documentElement.scrollWidth,h:document.documentElement.clientWidth})')
        if overflow['w']>overflow['h']+2:
            findings.append(_finding('<html>','320px viewport','1.4.10','serious','Page produces horizontal overflow at narrow viewport',f'document.scrollWidth is {overflow["w"]} while clientWidth is {overflow["h"]}.','Users at 400% zoom or narrow screens may need two-dimensional scrolling.','Make content responsive and avoid fixed-width layouts that create horizontal scrolling.',True))
    except Exception: pass
    finally:
        try: page.set_viewport_size({'width':1440,'height':1000})
        except Exception: pass
    return findings


def check_1_4_11_non_text_contrast(page):
    findings=[]
    try:
        results=page.evaluate("""() => {const rgb=s=>{const m=s.match(/rgba?\\(([^)]+)\\)/);if(!m)return null;const p=m[1].split(',').map(x=>+x.trim());return p}; const lum=c=>{c=c.map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return .2126*c[0]+.7152*c[1]+.0722*c[2]}; const out=[]; for(const e of document.querySelectorAll('button,input,select,textarea,[role=button],[role=checkbox],[role=radio],[role=slider],[role=switch]')){if(out.length>=80)break; const s=getComputedStyle(e),b=rgb(s.backgroundColor),bc=rgb(s.borderColor); if(!bc||parseFloat(s.borderWidth)<1)continue; let bg=b; if(!bg||b[3]===0){let n=e.parentElement;while(n){const x=rgb(getComputedStyle(n).backgroundColor);if(x&&x[3]!==0){bg=x;break}n=n.parentElement}} if(!bg)bg=[255,255,255,1]; const r=(Math.max(lum(bc.slice(0,3)),lum(bg.slice(0,3)))+.05)/(Math.min(lum(bc.slice(0,3)),lum(bg.slice(0,3)))+.05); if(r<3)out.push({tag:e.tagName.toLowerCase(),ratio:+r.toFixed(2)})} return out}""")
    except: results=[]
    for r in results:
        findings.append(_finding(f'<{r["tag"]}>','interactive component boundary','1.4.11','minor','Non-text UI boundary may have insufficient contrast',f'Estimated border/background contrast is {r["ratio"]}:1, below 3:1.','Users may have difficulty perceiving control boundaries or states.','Increase contrast of essential component boundaries/indicators to at least 3:1.',True))
    return findings


def check_1_4_12_text_spacing(page):
    findings=[]
    for el in page.query_selector_all('[style*="line-height" i],[style*="letter-spacing" i],[style*="word-spacing" i],[style*="text-indent" i]'):
        style=(_attr(el,'style') or '').lower()
        if '!important' in style and re.search(r'line-height|letter-spacing|word-spacing|text-indent',style):
            findings.append(_finding_for(el,page,'1.4.12','minor','Inline !important text-spacing rule may block user overrides',f'Inline style contains: {style[:120]}','Users applying accessibility text-spacing overrides may not be able to override the author rule.','Avoid unnecessary !important on text-spacing properties and verify content under increased spacing.',True))
    return findings


def check_1_4_13_content_on_hover_focus(page):
    findings=[]
    for el in page.query_selector_all('[onmouseover],[onmouseenter],[onfocus]'):
        handlers=' '.join(filter(None,[_attr(el,'onmouseover'),_attr(el,'onmouseenter'),_attr(el,'onfocus')]))
        if re.search(r'(tooltip|popover|show|display|visibility|classList|innerHTML)',handlers,re.I) and (_attr(el,'onfocus') is None and _attr(el,'onmouseover') is not None):
            findings.append(_finding_for(el,page,'1.4.13','minor','Hover-triggered content may lack a keyboard equivalent',f'Pointer handler contains content-showing logic: {handlers[:100]}','Keyboard users may not receive the same additional content.','Provide equivalent focus behavior and a way to dismiss content without moving focus.',True))
    for el in page.query_selector_all('[title]'):
        if _is_natively_interactive(el) and (_attr(el,'title') or '').strip():
            findings.append(_finding_for(el,page,'1.4.13','minor','Important information may be exposed only through a browser tooltip',f'Interactive element uses title="{_attr(el,"title")[:80]}".','Title tooltips are inconsistent for keyboard and touch users.','Expose important instructions or context as persistent/accessible content.',True))
    return findings


def check_2_1_1_keyboard(page):
    findings=[]
    for el in page.query_selector_all('[onclick],[onmousedown],[ontouchstart],[role="button"],[role="link"]'):
        if not _visible(el): continue
        if not _is_natively_interactive(el) and _attr(el,'tabindex') is None:
            findings.append(_finding_for(el,page,'2.1.1','serious','Custom interactive element is not keyboard focusable','A scripted/clickable element has no native interactive semantics and no tabindex.','Keyboard-only users may be unable to reach or activate the control.','Use a native button/link where possible, or provide complete keyboard semantics and behavior.',True))
        elif _attr(el,'role') in ('button','link') and _attr(el,'tabindex') is not None and not _attr(el,'onkeydown') and not _is_natively_interactive(el):
            findings.append(_finding_for(el,page,'2.1.1','serious','Custom role control has no detected keyboard activation handler','A non-native role=button/link element is focusable but no keydown handler is present.','Enter/Space activation may not work as expected.','Prefer native controls or implement the required keyboard interaction.',True))
    return findings


def check_2_1_2_no_keyboard_trap(page):
    findings=[]
    for el in page.query_selector_all('[role="dialog"],[role="alertdialog"]'):
        close=el.query_selector('button,[role="button"],a[href],[aria-label*="close" i]')
        handler=' '.join(filter(None,[_attr(el,'onkeydown'),_attr(el,'onkeyup')]))
        if not close and not re.search(r'escape|key.?code.{0,10}27',handler,re.I):
            findings.append(_finding_for(el,page,'2.1.2','serious','Dialog has no obvious keyboard exit mechanism','No close control or detectable Escape handling was found.','Keyboard users may become trapped in the dialog.','Provide an accessible close button and ensure Escape can close the dialog when appropriate.',True))
    return findings


def check_2_1_4_character_key_shortcuts(page):
    findings=[]
    html=page.content()
    if re.search(r'(event\.key|keyCode|which)\s*(===|==|=)\s*["\'][a-z0-9]["\']',html,re.I):
        findings.append(_finding('script/handler','page scripts','2.1.4','minor','Possible single-character keyboard shortcut detected','Script compares a keyboard event directly to a single character without an obvious modifier check.','Speech-input users may trigger shortcuts unintentionally while dictating.','Require a modifier, provide a way to turn the shortcut off, or allow remapping.',True))
    return findings


def check_2_2_1_timing_adjustable(page):
    findings=[]
    for el in page.query_selector_all('meta[http-equiv="refresh" i]'):
        findings.append(_finding_for(el,page,'2.2.1','serious','Meta refresh introduces time-based navigation',f'content="{_attr(el,"content")}".','Users who need more time may be redirected before completing a task.','Avoid automatic refresh/redirects or provide an accessible mechanism to extend or disable the time limit.',True))
    for el in page.query_selector_all('[data-timeout],[data-countdown],[class*="countdown" i],[id*="countdown" i]'):
        if _visible(el):
            findings.append(_finding_for(el,page,'2.2.1','minor','Page contains a possible timed-session/countdown mechanism',f'Element {_selector(el)} suggests a timeout/countdown.','Users may need more time to complete tasks.','Verify the time limit has required adjustment/extension controls.',True))
    return findings


def check_2_2_2_pause_stop_hide(page):
    findings=[]
    for el in page.query_selector_all('marquee,blink,video[autoplay],audio[autoplay],[class*="carousel" i],[class*="slider" i]'):
        if not _visible(el): continue
        moving = el.evaluate("e=>{const s=getComputedStyle(e);return !!e.querySelector('video[autoplay],audio[autoplay]')||s.animationName!=='none'||s.animationDuration!=='0s'||s.transitionDuration!=='0s'||['MARQUEE','BLINK'].includes(e.tagName)}")
        if moving and not el.query_selector('button,[role="button"],input[type="button"]'):
            findings.append(_finding_for(el,page,'2.2.2','moderate','Moving/auto-updating content has no detected pause/stop control','Animation or autoplay was detected without a nearby button-like control.','Moving content can distract users and can be difficult to perceive or control.','Provide a pause/stop mechanism for applicable moving content.',True))
    return findings


def check_2_3_1_three_flashes(page):
    findings=[]
    html=page.content()
    if re.search(r'(animation|transition)[^{}]{0,150}(infinite|iteration-count)',html,re.I) and re.search(r'(0\.([0-4]\d*)?|[1-4]\d*ms)',html,re.I):
        findings.append(_finding('CSS animation','stylesheets/inline CSS','2.3.1','moderate','Possible fast repeating animation detected','CSS contains repeating animation with a potentially short duration.','Flashing content can trigger seizures or physical reactions in susceptible users.','Verify that flashing does not exceed applicable flash thresholds and provide a way to stop motion.',True))
    return findings


def check_2_4_1_bypass_blocks(page):
    findings=[]
    skip=False
    for a in page.query_selector_all('a[href^="#"]'):
        t=(_accessible_name(page,a) or '').lower()
        if 'skip' in t or 'main content' in t: skip=True; break
    landmarks=page.query_selector_all('main,[role="main"],nav,[role="navigation"]')
    if not skip and not landmarks:
        findings.append(_finding('<body>','page structure','2.4.1','moderate','No skip link or major navigation landmark detected','Neither a skip-to-content mechanism nor a main/navigation landmark was detected.','Keyboard and screen-reader users may have to traverse repeated content.','Provide a skip link and meaningful semantic landmarks.'))
    return findings


def check_2_4_2_page_titled(page):
    title=(page.title() or '').strip()
    if not title:
        return [_finding('<title>','document head','2.4.2','serious','Page has no descriptive title','The rendered document title is empty or missing.','Users cannot identify the page in tabs, history, or assistive technology.','Provide a concise, descriptive page title.')]
    if len(title)<3 or re.match(r'^(home|page|untitled|document)$',title,re.I):
        return [_finding('<title>','document head','2.4.2','minor','Page title is likely too generic',f'Title is "{title}".','Users may have difficulty distinguishing this page from other pages.','Use a specific title describing the page or task.',True)]
    return []


def check_2_4_3_focus_order(page):
    findings=[]
    positives=[]
    for el in page.query_selector_all('[tabindex]'):
        try:v=int(_attr(el,'tabindex'))
        except:continue
        if v>0: positives.append((v,el))
    if positives:
        for v,el in positives[:20]:
            findings.append(_finding_for(el,page,'2.4.3','moderate','Positive tabindex may create an illogical focus order',f'tabindex="{v}" explicitly changes natural tab sequence.','Keyboard users may experience a focus order that does not match the intended reading/visual order.','Remove positive tabindex values and arrange DOM order logically.',True))
    return findings


def check_2_4_4_link_purpose(page):
    findings=[]
    for a in page.query_selector_all('a[href]'):
        name=_accessible_name(page,a).strip()
        if not name:
            findings.append(_finding_for(a,page,'2.4.4','serious','Link has no accessible name','No visible text, aria-label, labelledby, or labelled image was detected.','Screen-reader users cannot determine the link destination.','Add descriptive link text or an accessible name.'))
        elif name.lower() in LOW_INFO_LINK_TEXT:
            findings.append(_finding_for(a,page,'2.4.4','minor','Link text is too generic out of context',f'Accessible name is "{name}".','Users navigating by a list of links may not know what each link does.','Use text that identifies the destination or action, optionally adding visually-hidden context.',True))
    return findings


def check_2_4_5_multiple_ways(page):
    findings=[]
    ways=0
    if page.query_selector('input[type="search"],input[name*="search" i],input[id*="search" i]'): ways+=1
    if page.query_selector('a[href*="sitemap" i]'): ways+=1
    if len(page.query_selector_all('nav a,[role="navigation"] a'))>=2: ways+=1
    if page.query_selector('form[action*="search" i]'): ways+=1
    if ways<2:
        findings.append(_finding('<body>','page/site navigation','2.4.5','minor','Fewer than two navigation methods were detected','Search, sitemap, and/or a multi-link navigation mechanism did not provide two distinct ways on this page.','Some users may have difficulty locating content using only the primary method.','Provide at least two applicable ways to locate pages, such as navigation and search or sitemap.',True))
    return findings


def check_2_4_6_headings_labels(page):
    findings=[]
    hs=page.query_selector_all('h1,h2,h3,h4,h5,h6')
    h1=len(page.query_selector_all('h1'))
    if h1==0:
        findings.append(_finding('<body>','heading structure','2.4.6','minor','No h1 heading detected','The page has no top-level h1 heading.','Users navigating by headings may have difficulty identifying the page topic.','Provide a meaningful top-level heading when appropriate.',True))
    elif h1>1:
        findings.append(_finding('<h1>','heading structure','2.4.6','minor','Multiple h1 headings detected',f'Found {h1} h1 elements.','Multiple top-level headings can make the page structure harder to understand.','Verify that headings form a clear, meaningful hierarchy.',True))
    prev=0
    for h in hs:
        txt=_safe_text(h); level=int(h.evaluate('e=>e.tagName.substring(1)'))
        if not txt: findings.append(_finding_for(h,page,'2.4.6','moderate','Heading has no text','The heading element is empty.','Heading navigation exposes an uninformative entry.','Add a meaningful heading or remove the empty element.'))
        if prev and level>prev+1: findings.append(_finding_for(h,page,'2.4.6','minor','Heading level skips a level',f'Heading jumps from h{prev} to h{level}.','Users may find the information hierarchy harder to navigate.','Use a logical heading hierarchy and avoid unnecessary level jumps.',True))
        if txt: prev=level
    for el in page.query_selector_all('input,select,textarea,button'):
        if not _visible(el): continue
        if not _accessible_name(page,el).strip():
            findings.append(_finding_for(el,page,'2.4.6','moderate','Interactive control has no meaningful label',f'{_selector(el)} has no detected accessible name.','Users may not understand the purpose of the control.','Provide a visible label or accessible name.',True))
    return findings


def check_2_4_7_focus_visible(page):
    findings=[]
    for el in page.query_selector_all('a,button,input,select,textarea,[tabindex],[role="button"],[role="link"]'):
        style=(_attr(el,'style') or '').replace(' ','').lower()
        if 'outline:none' in style or 'outline:0' in style:
            if not re.search(r'box-shadow|border|outline',style):
                findings.append(_finding_for(el,page,'2.4.7','serious','Inline CSS removes the focus outline with no visible replacement','The inline style disables outline without an obvious replacement focus indicator.','Keyboard users may lose track of the focused control.','Keep a visible focus indicator or provide an equally visible custom style.',True))
    return findings


def check_2_5_1_pointer_gestures(page):
    findings=[]
    for el in page.query_selector_all('[class*="swipe" i],[class*="carousel" i],[class*="pinch" i],[class*="gesture" i]'):
        if _visible(el) and not el.query_selector('button,[role="button"],a,input'):
            findings.append(_finding_for(el,page,'2.5.1','moderate','Possible gesture-based component has no simple pointer alternative','The component name suggests a swipe/pinch/gesture interaction without visible controls.','Users unable to perform complex gestures may be unable to operate the component.','Provide simple single-pointer controls such as buttons.',True))
    return findings


def check_2_5_2_pointer_cancellation(page):
    findings=[]
    for el in page.query_selector_all('[onmousedown],[ontouchstart],[pointerdown]'):
        down=' '.join(filter(None,[_attr(el,'onmousedown'),_attr(el,'ontouchstart'),_attr(el,'pointerdown')]))
        if re.search(r'click|submit|open|delete|save|navigate|location',down,re.I) and not re.search(r'click|mouseup|touchend|pointerup',down,re.I):
            findings.append(_finding_for(el,page,'2.5.2','moderate','Potential action starts on pointer-down','Pointer-down handler appears to perform an action before release.','Users may have less opportunity to cancel an accidental pointer activation.','Trigger non-drag actions on release/click where appropriate and provide cancellation.',True))
    return findings


def check_2_5_3_label_in_name(page):
    findings=[]
    for el in page.query_selector_all('button,a,input[type="button"],input[type="submit"],input[type="reset"],[role="button"],[role="link"]'):
        visible=(_safe_text(el) or _attr(el,'value') or '').strip().lower()
        aria=(_attr(el,'aria-label') or '').strip().lower()
        if visible and aria and visible not in aria:
            findings.append(_finding_for(el,page,'2.5.3','moderate','Accessible name does not contain visible label text',f'Visible text is "{visible[:50]}" while aria-label is "{aria[:70]}".','Speech-input users may say the visible label and fail to activate the control.','Include the visible label in the accessible name, or remove the conflicting aria-label.',True))
    return findings


def check_2_5_4_motion_actuation(page):
    findings=[]
    html=page.content()
    if re.search(r'devicemotion|deviceorientation|accelerometer|gyroscope',html,re.I):
        findings.append(_finding('script','motion sensor API usage','2.5.4','moderate','Motion/orientation-based interaction is present','The page references device motion/orientation APIs.','Users unable to move or tilt a device may be unable to perform the action.','Provide an equivalent conventional control and allow motion-based activation to be disabled.',True))
    return findings


def check_3_1_1_language_of_page(page):
    lang=page.eval_on_selector('html','e=>e.getAttribute("lang")') or ''
    if not lang.strip() or not re.match(r'^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$',lang.strip()):
        return [_finding('<html>','document root','3.1.1','serious','Page language is missing or malformed',f'html[lang] is "{lang}".','Screen readers may choose an incorrect pronunciation language.','Set a valid language tag such as lang="en" or lang="en-US".')]
    return []


def check_3_1_2_language_of_parts(page):
    findings=[]
    try:
        results=page.evaluate("""() => {const re=/[\\u0400-\\u04FF\\u0590-\\u05FF\\u0600-\\u06FF\\u0900-\\u097F\\u3040-\\u30FF\\u4E00-\\u9FFF]/; const out=[]; for(const e of document.querySelectorAll('body *')){if(out.length>=20||e.children.length)continue;const t=(e.innerText||'').trim();if(t.length<6||!re.test(t))continue;let n=e,ok=false;while(n){if(n.getAttribute&&n.getAttribute('lang')){ok=true;break}n=n.parentElement}if(!ok)out.push(t.slice(0,50))}return out}""")
    except:results=[]
    for t in results:
        findings.append(_finding('text node','mixed-language content','3.1.2','minor','Possible language-of-part missing',f'Non-default-script text was detected without a local lang attribute: "{t}".','Screen readers may pronounce foreign-language text incorrectly.','Mark passages whose language differs from the surrounding text with the correct lang attribute.',True))
    return findings


def check_3_2_1_on_focus(page):
    findings=[]
    for el in page.query_selector_all('[onfocus]'):
        v=_attr(el,'onfocus') or ''
        if re.search(r'location|submit\s*\(|window\.open|\.click\s*\(',v,re.I):
            findings.append(_finding_for(el,page,'3.2.1','serious','Focus handler appears to trigger a context change',f'onfocus contains: {v[:120]}','Keyboard users can trigger navigation/submission merely by moving focus.','Do not change context solely because a control receives focus.',True))
    return findings


def check_3_2_2_on_input(page):
    findings=[]
    for el in page.query_selector_all('[oninput],[onchange]'):
        v=' '.join(filter(None,[_attr(el,'oninput'),_attr(el,'onchange')]))
        if re.search(r'location|submit\s*\(|window\.open',v,re.I):
            findings.append(_finding_for(el,page,'3.2.2','serious','Input/change handler appears to trigger a context change',f'Handler contains: {v[:120]}','Users may be unexpectedly navigated or submitted while entering data.','Require an explicit action before changing context.',True))
    return findings


def check_3_2_3_consistent_navigation(page):
    return [_finding('<nav>','site-wide consistency','3.2.3','minor','Site-wide navigation consistency requires multiple pages','A single-page render cannot prove that navigation appears in the same relative order throughout the site.','No single-page DOM test can establish site-wide consistency.','Compare navigation across representative pages and verify order/placement remains consistent.',True)]


def check_3_2_4_consistent_identification(page):
    findings=[]
    # On one page, identify repeated controls whose accessible names differ by trivial casing/spacing.
    groups={}
    for el in page.query_selector_all('button,a,[role="button"],[role="link"]'):
        n=re.sub(r'\s+',' ',(_accessible_name(page,el) or '').strip().lower())
        if n: groups.setdefault(n,[]).append(el)
    # This is mainly a reminder, plus a concrete suspicious duplicate-name pattern.
    for n,els in groups.items():
        if len(els)>=3 and n in {'menu','settings','help','close','more'}:
            findings.append(_finding('repeated controls',f'{len(els)} controls named "{n}"','3.2.4','minor','Repeated generic controls may be difficult to distinguish','The same short generic accessible name is used on multiple controls.','Users navigating controls out of context may not know which instance they reached.','Add contextual names when the controls perform different functions.',True))
    findings.append(_finding('repeated components','site-wide component naming','3.2.4','minor','Site-wide component identification requires comparison across pages','A single page cannot establish consistent naming of equivalent components across the site.','Inconsistent names can make repeated tasks harder for assistive-technology users.','Compare repeated components across representative pages and keep equivalent functions consistently identified.',True))
    return findings


def check_3_3_1_error_identification(page):
    findings=[]
    for el in page.query_selector_all('[aria-invalid="true"],input:invalid,select:invalid,textarea:invalid'):
        if not _visible(el): continue
        described=_attr(el,'aria-describedby') or ''
        msg=False
        if described:
            msg=any(page.query_selector('#'+i) for i in described.split())
        parent_text=page.evaluate("el=>{const p=el.parentElement;return p?(p.innerText||''):''}",el)[:300]
        if not msg and not re.search(r'error|invalid|required|must|enter|please|valid',parent_text,re.I):
            findings.append(_finding_for(el,page,'3.3.1','serious','Invalid field has no detected error identification','The control is invalid but no associated error message or nearby error text was detected.','Users may know that validation failed without knowing what is wrong.','Provide a specific text error and associate it programmatically.',True))
    return findings


def check_3_3_2_labels_or_instructions(page):
    findings=[]
    for el in page.query_selector_all('input[required],select[required],textarea[required]'):
        if not _has_programmatic_label(page,el):
            findings.append(_finding_for(el,page,'3.3.2','serious','Required field has no programmatic label','The required control lacks a detected label/name.','Users may not know what information is required.','Provide a visible label and clearly indicate the required state.',True))
    return findings


def check_3_3_3_error_suggestion(page):
    findings=[]
    for el in page.query_selector_all('[aria-invalid="true"],input:invalid,select:invalid,textarea:invalid'):
        described=_attr(el,'aria-describedby') or ''
        texts=[]
        for i in described.split():
            m=page.query_selector('#'+i)
            if m:texts.append(_safe_text(m))
        if texts and not re.search(r'enter|use|choose|select|format|must|should|try|valid|example', ' '.join(texts),re.I):
            findings.append(_finding_for(el,page,'3.3.3','minor','Error message may not provide correction guidance',f'Associated message: "{" ".join(texts)[:120]}".','Users may be told an error occurred without knowing how to correct it.','Where appropriate, explain how to correct the input.',True))
    return findings


def check_3_3_4_error_prevention(page):
    findings=[]
    for form in page.query_selector_all('form'):
        sensitive=form.query_selector('[name*="payment" i],[name*="card" i],[name*="account" i],[name*="delete" i],[name*="transfer" i],[name*="amount" i],[id*="payment" i],[id*="card" i],[id*="delete" i]')
        if sensitive:
            txt=_safe_text(form).lower()
            if not re.search(r'review|confirm|verify|summary',txt) and not form.query_selector('[type="checkbox"][name*="confirm" i],button[name*="confirm" i]'):
                findings.append(_finding_for(form,page,'3.3.4','moderate','Sensitive transaction form has no obvious review/confirmation step','Financial/account/deletion-related fields were detected without a visible review/confirmation mechanism.','Users may submit consequential actions by mistake.','Provide review/confirmation or an appropriate reversible mechanism.',True))
    return findings


def check_4_1_1_parsing(page):
    findings=[]
    try:
        dupes=page.evaluate("""()=>{const m={};document.querySelectorAll('[id]').forEach(e=>m[e.id]=(m[e.id]||0)+1);return Object.entries(m).filter(([k,v])=>k&&v>1)}""")
    except:dupes=[]
    for id_,count in dupes[:30]:
        findings.append(_finding(f'id="{id_}"',f'{count} elements','4.1.1','moderate','Duplicate id value detected',f'id="{id_}" occurs {count} times.','Labeling, ARIA references, anchors, and scripting may target the wrong element.','Make every id unique within the document.'))
    return findings


def check_4_1_2_name_role_value(page):
    findings=[]

    # Use the browser accessibility snapshot/structured inspection collected
    # once for this live page. This supplements the DOM-level ARIA checks below.
    try:
        data = _get_inspection_data(page)
        for item in data.get("elements", []):
            role = (item.get("role") or "").split()[0].lower()
            tag = item.get("tag", "")
            interactive_roles = {"button", "link", "checkbox", "radio", "switch", "textbox", "combobox", "slider", "tab", "menuitem"}
            interactive_tags = {"button", "a", "input", "select", "textarea", "summary"}
            if (role in interactive_roles or tag in interactive_tags) and item.get("visible") and not (item.get("accessible_name") or "").strip():
                findings.append(_finding(
                    item.get("selector", tag),
                    "accessibility tree / ARIA snapshot",
                    "4.1.2",
                    "serious",
                    "Interactive element has no accessible name in the collected accessibility data",
                    "The live browser inspection data contains an interactive element with no detected accessible name.",
                    "Screen-reader users may not know what the control does.",
                    "Give the control a meaningful accessible name using visible text, a label, aria-label, or aria-labelledby.",
                    True
                ))
    except Exception:
        pass

    for el in page.query_selector_all('[role]'):
        role=(_attr(el,'role') or '').split()[0].lower()
        if role and role not in KNOWN_ARIA_ROLES:
            findings.append(_finding_for(el,page,'4.1.2','moderate','Invalid ARIA role detected',f'role="{role}" is not in the known role set used by this checker.','Assistive technology may not interpret the intended role correctly.','Use a valid ARIA role or remove the unnecessary role.',True))
    for el in page.query_selector_all('button,a[href],[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="switch"],[role="textbox"]'):
        if _visible(el) and not _accessible_name(page,el).strip():
            findings.append(_finding_for(el,page,'4.1.2','serious','Interactive element has no accessible name','No accessible name was computed from text, label, ARIA, or an image.','Screen-reader users may not know what the control does.','Give the control a meaningful accessible name.'))
    for el in page.query_selector_all('[aria-expanded],[aria-checked],[aria-pressed],[aria-selected]'):
        val=_attr(el,'aria-expanded') or _attr(el,'aria-checked') or _attr(el,'aria-pressed') or _attr(el,'aria-selected')
        if val not in {'true','false','mixed'}:
            findings.append(_finding_for(el,page,'4.1.2','minor','ARIA state/value is malformed',f'ARIA state value is "{val}".','Assistive technology may not interpret the component state reliably.','Use a valid ARIA state value.',True))
    return findings


def check_4_1_3_status_messages(page):
    findings=[]
    candidates=page.query_selector_all('[class*="toast" i],[class*="notification" i],[class*="snackbar" i],[class*="status" i],[role="status"],[role="alert"]')
    for el in candidates:
        if _safe_text(el) and not (_attr(el,'aria-live') or _attr(el,'role') in {'status','alert'}):
            findings.append(_finding_for(el,page,'4.1.3','moderate','Status/notification-like element lacks a live-region mechanism','A dynamic-message-looking element has text but no aria-live, role=status, or role=alert.','Screen-reader users may not be informed when the message appears.','Use an appropriate live region for dynamically injected status messages.',True))
    return findings


def check_2_4_11_focus_not_obscured(page):
    findings=[]
    try:
        result=page.evaluate("""()=>{const fixed=[];for(const e of document.querySelectorAll('*')){const s=getComputedStyle(e);if((s.position==='fixed'||s.position==='sticky')&&s.zIndex!=='auto'){const r=e.getBoundingClientRect();if(r.width>innerWidth*.5&&r.height>30)fixed.push({tag:e.tagName.toLowerCase(),top:r.top,bottom:r.bottom,height:r.height})}}return fixed.slice(0,10)}""")
    except:result=[]
    for r in result:
        findings.append(_finding(f'<{r["tag"]}>','fixed/sticky overlay','2.4.11','minor','Fixed/sticky content may obscure focused controls',f'A {r["height"]}px fixed/sticky region spans much of the viewport.','Focused controls can become visually hidden behind persistent headers/footers.','Test keyboard focus near the overlay and use scroll-margin or equivalent positioning.',True))
    return findings


def check_2_5_7_dragging_movements(page):
    findings=[]
    for el in page.query_selector_all('[draggable="true"],[class*="drag" i],[class*="sortable" i]'):
        if not _visible(el):continue
        alt=el.query_selector('button,[role="button"]')
        if not alt:
            findings.append(_finding_for(el,page,'2.5.7','moderate','Possible drag interaction has no non-drag alternative','A draggable/sortable-looking element has no nearby button alternative.','Users who cannot drag with a pointer may be unable to complete the task.','Provide buttons or another single-pointer alternative for the same operation.',True))
    return findings


def check_2_5_8_target_size(page):
    """Flag interactive targets whose rendered box is smaller than 24x24 CSS px.

    This is a WCAG 2.2 AA minimum-target-size heuristic/automated screen based
    check. Exceptions and spacing between adjacent targets require contextual
    verification, so findings are marked heuristic.
    """
    findings = []
    try:
        data = _get_inspection_data(page)
        for item in data.get("elements", []):
            tag = item.get("tag", "")
            role = (item.get("role") or "").split()[0].lower()
            interactive = (
                tag in {"a", "button", "input", "select", "textarea", "summary"} or
                role in {"button", "link", "checkbox", "radio", "switch", "tab", "option", "menuitem", "slider"} or
                "tabindex" in item.get("attributes", {})
            )
            if not interactive or not item.get("visible"):
                continue
            g = item.get("geometry") or {}
            w = float(g.get("width", 0) or 0)
            h = float(g.get("height", 0) or 0)
            if 0 < w < 24 or 0 < h < 24:
                findings.append(_finding(
                    item.get("selector", tag),
                    f'geometry {w:.1f}x{h:.1f}px',
                    '2.5.8',
                    'moderate',
                    'Interactive target may be smaller than the WCAG 2.2 minimum target size',
                    f'The rendered target box is {w:.1f}px by {h:.1f}px; at least one dimension is below 24 CSS px.',
                    'People with limited dexterity or difficulty accurately targeting small controls may have difficulty activating the control.',
                    'Increase the target size to at least 24 by 24 CSS px, or verify that a WCAG 2.2 target-size exception applies.',
                    True
                ))
    except Exception:
        pass
    return findings


def check_3_2_6_consistent_help(page):
    findings=[]
    helpel=page.query_selector('a[href*="help" i],a[href*="contact" i],a[href*="support" i],[aria-label*="help" i],[class*="help" i],[id*="help" i]')
    if not helpel:
        findings.append(_finding('<body>','help mechanism','3.2.6','minor','No obvious help/contact/support mechanism detected','No common help/contact/support affordance was found on this page.','Users who need assistance may have difficulty finding support.','If the site provides help, expose it consistently from the applicable pages.',True))
    return findings


def check_3_3_7_redundant_entry(page):
    findings=[]
    forms=page.query_selector_all('form')
    seen={}
    for i,form in enumerate(forms):
        for el in form.query_selector_all('input[name],select[name],textarea[name]'):
            n=(_attr(el,'name') or '').strip().lower()
            typ=(_attr(el,'type') or '').lower()
            if n and typ not in {'hidden','submit','button'}:seen.setdefault(n,set()).add(i)
    for n,idx in seen.items():
        if len(idx)>1:
            findings.append(_finding(f'field name="{n}"',f'forms {sorted(idx)}','3.3.7','minor','Same information appears to be requested in multiple forms',f'Field name "{n}" occurs in {len(idx)} separate forms.','Users may have to remember/re-enter information unnecessarily.','Reuse previously supplied information or provide autofill/selection mechanisms where appropriate.',True))
    return findings


def check_extra_iframe_accessible_name(page):
    findings=[]
    for el in page.query_selector_all('iframe'):
        if not (_attr(el,'title') or _attr(el,'aria-label') or _attr(el,'aria-labelledby')):
            findings.append(_finding_for(el,page,'4.1.2','serious','iframe has no accessible name','No title, aria-label, or aria-labelledby was detected.','Screen-reader users cannot identify the embedded content.','Add a concise title such as title="Embedded video player".'))
    return findings


def check_extra_aria_hidden_focusable(page):
    findings=[]
    for el in page.query_selector_all('[aria-hidden="true"]'):
        focus=el.query_selector('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])')
        if focus:
            findings.append(_finding_for(el,page,'4.1.2','serious','aria-hidden container contains a focusable descendant','A descendant can receive focus while its ancestor is hidden from assistive technology.','Users may tab to content that screen readers do not expose.','Remove aria-hidden or ensure all descendants are unfocusable/disabled while hidden.'))
    return findings


def check_extra_placeholder_only_reliance(page):
    findings=[]
    for el in page.query_selector_all('input:not([type="hidden"]),textarea,select'):
        if (_attr(el,'placeholder') or '').strip() and not _has_programmatic_label(page,el):
            findings.append(_finding_for(el,page,'3.3.2','minor','Placeholder appears to be the only label','A form control has placeholder text but no detected persistent programmatic label.','Placeholder text disappears and may not be announced consistently.','Add a persistent visible label and use the placeholder only as supplemental guidance.',True))
    return findings


def check_extra_ambiguous_duplicate_link_text(page):
    findings=[]
    seen={}
    for a in page.query_selector_all('a[href]'):
        n=re.sub(r'\s+',' ',(_accessible_name(page,a) or '').strip().lower())
        if n:seen.setdefault(n,[]).append(_attr(a,'href') or '')
    for n,hrefs in seen.items():
        uniq={h.split('#')[0] for h in hrefs}
        if len(hrefs)>2 and len(uniq)>1 and (n in LOW_INFO_LINK_TEXT or len(n)<=12):
            findings.append(_finding(f'<a>{n}</a>',f'{len(hrefs)} occurrences','2.4.4','minor','Generic link name is repeated for different destinations',f'"{n}" is used for {len(hrefs)} links with multiple destinations.','Users navigating links out of context cannot distinguish the destinations.','Make link names unique/descriptive or add accessible context.',True))
    return findings


def check_extra_empty_aria_label(page):
    findings=[]
    for el in page.query_selector_all('[aria-label=""]'):
        findings.append(_finding_for(el,page,'4.1.2','moderate','Empty aria-label detected','aria-label is present but empty.','The empty accessible name can suppress otherwise useful naming information.','Remove the empty aria-label or replace it with a meaningful accessible name.'))
    return findings


def check_extra_generic_landmarks_missing(page):
    findings=[]
    if not page.query_selector('header,[role="banner"]') and not page.query_selector('footer,[role="contentinfo"]'):
        findings.append(_finding('<body>','page structure','2.4.1','minor','No header or footer landmark detected','Neither a header/banner nor footer/contentinfo landmark was found.','Landmark navigation may be less useful for orientation.','Consider adding semantic landmarks where they represent actual page regions.',True))
    return findings


# ============================================================
# MASTER RUNNER
# ============================================================

_CHECK_FUNCTIONS = [
    check_1_1_1_non_text_content,
    check_1_2_1_audio_video_only,
    check_1_2_2_captions_prerecorded,
    check_1_2_3_audio_description,
    check_1_2_4_captions_live,
    check_1_2_5_audio_description_prerecorded,
    check_1_3_1_info_relationships,
    check_1_3_2_meaningful_sequence,
    check_1_3_3_sensory_characteristics,
    check_1_3_4_orientation,
    check_1_3_5_identify_input_purpose,
    check_1_4_1_use_of_color,
    check_1_4_2_audio_control,
    check_1_4_3_contrast,
    check_1_4_4_resize_text,
    check_1_4_5_images_of_text,
    check_1_4_10_reflow,
    check_1_4_11_non_text_contrast,
    check_1_4_12_text_spacing,
    check_1_4_13_content_on_hover_focus,
    check_2_1_1_keyboard,
    check_2_1_2_no_keyboard_trap,
    check_2_1_4_character_key_shortcuts,
    check_2_2_1_timing_adjustable,
    check_2_2_2_pause_stop_hide,
    check_2_3_1_three_flashes,
    check_2_4_1_bypass_blocks,
    check_2_4_2_page_titled,
    check_2_4_3_focus_order,
    check_2_4_4_link_purpose,
    check_2_4_5_multiple_ways,
    check_2_4_6_headings_labels,
    check_2_4_7_focus_visible,
    check_2_5_1_pointer_gestures,
    check_2_5_2_pointer_cancellation,
    check_2_5_3_label_in_name,
    check_2_5_4_motion_actuation,
    check_3_1_1_language_of_page,
    check_3_1_2_language_of_parts,
    check_3_2_1_on_focus,
    check_3_2_2_on_input,
    check_3_2_3_consistent_navigation,
    check_3_2_4_consistent_identification,
    check_3_3_1_error_identification,
    check_3_3_2_labels_or_instructions,
    check_3_3_3_error_suggestion,
    check_3_3_4_error_prevention,
    check_4_1_1_parsing,
    check_4_1_2_name_role_value,
    check_4_1_3_status_messages,
    check_2_4_11_focus_not_obscured,
    check_2_5_7_dragging_movements,
    check_2_5_8_target_size,
    check_3_2_6_consistent_help,
    check_3_3_7_redundant_entry,
    check_extra_iframe_accessible_name,
    check_extra_aria_hidden_focusable,
    check_extra_placeholder_only_reliance,
    check_extra_ambiguous_duplicate_link_text,
    check_extra_empty_aria_label,
    check_extra_generic_landmarks_missing,
]


def run_all_checks(page):
    """
    Run every implemented WCAG check (all 55 combined 2.1+2.2 A/AA
    checkpoints) against the given live Playwright `page` and return
    a flat list of findings using the same schema as the Gemini
    findings in app1.py, plus a `verification` field.
    """
    findings = []
    # Collect accessibility tree + computed styles + geometry once from the
    # same live page. Existing checks still receive the original Playwright page.
    _get_inspection_data(page)
    for fn in _CHECK_FUNCTIONS:
        try:
            findings.extend(fn(page) or [])
        except Exception as e:
            findings.append(_finding(
                "(script error)", fn.__name__, "0.0.0", "minor",
                "Internal checker error",
                f"The check {fn.__name__} raised an exception: {e}",
                "This checkpoint could not be evaluated for this page.",
                "No action needed for the site; this is a limitation of the script.",
            ))
    return findings


def coverage_report():
    """Return how many of the 55 combined checkpoints are automated
    vs. heuristic. All 55 now have an implemented check."""
    counts = {"automated": 0, "heuristic": 0}
    for sc in CHECKPOINTS.values():
        counts[sc["method"]] += 1
    return {
        "total_checkpoints": len(CHECKPOINTS),
        "automated": counts["automated"],
        "heuristic": counts["heuristic"],
        "manual_only": 0,
        "checks_implemented": len(_CHECK_FUNCTIONS),
    }


if __name__ == "__main__":
    import sys
    from playwright.sync_api import sync_playwright

    if len(sys.argv) < 2:
        print("Usage: python app2.py <url>")
        sys.exit(1)

    target_url = sys.argv[1]
    print(f"Checkpoint coverage: {coverage_report()}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(target_url, wait_until="networkidle")
        results = run_all_checks(page)
        browser.close()

    print(f"\nFound {len(results)} issues:\n")
    for f in results:
        print(f'[{f["severity"].upper()}] [{f["verification"]}] {f["wcag_criterion"]} — {f["defect"]}')