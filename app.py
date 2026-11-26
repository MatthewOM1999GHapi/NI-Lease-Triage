import json
import os
import re
from datetime import datetime
from typing import Literal

import streamlit as st
from pydantic import BaseModel, Field

# ---------------------------------------------------------
# 1. PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(
    page_title="NI Commercial Lease & Valuation Risk Triage",
    page_icon="🏢",
    layout="wide",
)

# ---------------------------------------------------------
# 2. STRUCTURED OUTPUT SCHEMA
# ---------------------------------------------------------
Category = Literal[
    "Term & Break Options",
    "Rent Review Mechanism",
    "Repairing Obligations",
    "Service Charge & Caps",
    "Alienation & User Clause",
    "Incentives & Side Letters",
]


class ClauseRisk(BaseModel):
    category: Category
    risk_level: Literal["High", "Medium", "Low"]
    source_quote: str = Field(
        description="Exact verbatim snippet from the input text supporting this finding, or 'Not stated in extract'"
    )
    valuation_impact: str = Field(
        description="Concise explanation of how this affects ERV, net operating income, WAULT, capex deductions, or yield"
    )
    surveyor_verification_question: str = Field(
        description="Specific due-diligence question a chartered surveyor must verify before signing off"
    )


class LeaseTriageReport(BaseModel):
    sector_detected: str
    headline_summary: str
    wault_and_income_note: str
    net_effective_rent_note: str
    risk_items: list[ClauseRisk]
    overall_valuation_caution: str


CAUTION = (
    "DRAFT TRIAGE ONLY — requires physical inspection, full title/lease verification, "
    "and RICS surveyor sign-off before adoption in any valuation."
)

# ---------------------------------------------------------
# 3. SAMPLE NI LEASES (text + matching sector)
# ---------------------------------------------------------
PLACEHOLDER = "Select a sample NI lease extract..."
SECTORS = ["Industrial / Logistics", "City Centre Office", "High Street Retail", "Mixed-Use / Investment"]

SAMPLE_LEASES = {
    PLACEHOLDER: ("", SECTORS[0]),
    "Sample 1: Belfast Industrial Unit (Duncrue / Mallusk)": (
        """HEADS OF TERMS & LEASE EXTRACT - UNIT 4, DUNCRUE INDUSTRIAL ESTATE, BELFAST BT3 9BP
Landlord: Lagan Harbour Developments Ltd
Tenant: Ulster Freight & Logistics (NI) Ltd
Demise: Detached warehouse of 18,500 sq ft GIA with concrete service yard.
Term: 10 years from 1 May 2024, governed by the Business Tenancies (Northern Ireland) Order 1996.
Break Option: Tenant-only option to determine on 1 May 2027 (Year 3) upon giving not less than 6 months' prior written notice, conditional upon payment of principal rent up to the break date and delivery of vacant possession. No break penalty payable.
Passing Rent: £129,500 per annum exclusive (£7.00 per sq ft), payable quarterly in advance.
Rent Review: Upward-only open market rent review at Year 5 (1 May 2029), assuming a willing landlord and willing tenant on a 5-year term, disregarding tenant's voluntary fit-out works.
Repairing Liability: Full Repairing and Insuring (FRI), save that the Tenant's obligation to put and keep the roof cladding and structural steelwork in repair shall be limited by reference to a photographic Schedule of Condition dated April 2024 (which notes extensive cut-edge corrosion to the south roof slope).
Alienation: Assignment of whole permitted with Landlord's consent (not to be unreasonably withheld), subject to an Authorised Guarantee Agreement (AGA). Subletting of part prohibited.""",
        "Industrial / Logistics",
    ),
    "Sample 2: Belfast City Centre Office Suite (Linen Quarter)": (
        """LEASE & SIDE LETTER SUMMARY - 3RD FLOOR, DONEGALL SQUARE EAST, BELFAST BT1 5HD
Landlord: Clarendon Commercial Properties PLC
Tenant: FinTech Advisory Solutions UK Ltd
Demise: 6,200 sq ft NIA Grade A open-plan office suite plus 4 basement car spaces.
Term: 5 years from 1 September 2025.
Passing Rent: Headline rent of £142,600 per annum (£23.00 per sq ft).
Side Letter Concession: By a personal side letter dated 28 August 2025, the Landlord granted the Tenant a 12-month rent-free period from term commencement, plus a £31,000 capital contribution toward partitioning works.
Rent Review: None during the 5-year term.
Service Charge: Tenant pays a fair proportion (18.4%) of building running costs, subject to an all-inclusive Service Charge Cap of £4.50 per sq ft per annum (£27,900), indexed annually to CPI capped at 3%. Note: Current building service charge budget is running at £6.15 per sq ft due to lift modernization and M&E energy costs.
User Clause: Restricted strictly to Class B1(a) financial and professional services offices.""",
        "City Centre Office",
    ),
    "Sample 3: High Street Retail Unit (Lisburn Road, Belfast)": (
        """EXTRACT OF LEASE - 412 LISBURN ROAD, BELFAST BT9 6GN
Landlord: Malone Retail Investments Ltd
Tenant: Artisan Roastery & Bakery (Belfast) Ltd
Demise: Ground floor retail unit (1,450 sq ft) and basement storage (400 sq ft).
Term: 15 years from 1 January 2021.
Passing Rent: £42,000 per annum exclusive.
Rent Review: 5-yearly upward-only open market rent review (next review 1 January 2026). Hypothetical lease assumptions require the unit to be valued as fully fitted-out for high-end cafe/bakery use including extraction plant installed by the Tenant at a cost of £65,000.
User Clause: Use permitted solely as an artisan coffee shop and bakery; general Class A1 retail use is expressly prohibited without Landlord's absolute discretion.
Repair & Insurance: Internal repairing only for the Tenant; Landlord responsible for structure and exterior, with no mechanism in the lease to recover structural roof repairs via service charge.""",
        "High Street Retail",
    ),
}

# ---------------------------------------------------------
# 4. LOCAL PII & ENTITY REDACTOR
# ---------------------------------------------------------
def redact_sensitive_entities(text: str) -> tuple[str, list[str]]:
    """Masks postcodes, company names and street addresses locally before any external LLM call."""
    redacted = text
    log: list[str] = []

    # Postcodes
    postcode = r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"
    if re.search(postcode, redacted):
        redacted = re.sub(postcode, "[POSTCODE_REDACTED]", redacted)
        log.append("postcodes masked")

    # Company names: up to 6 words ending in Ltd/Limited/PLC/LLP, same line only ([ \t], not \s)
    company = r"\b[A-Z][\w&()\-]*(?:[ \t]+[\w&()\-]+){0,5}?[ \t]+(?:Ltd|Limited|PLC|plc|LLP)\b\.?"
    names = list(dict.fromkeys(m.group(0) for m in re.finditer(company, redacted)))
    mapping = {n: f"[ENTITY_{i}]" for i, n in enumerate(names, start=1)}
    for name in sorted(mapping, key=len, reverse=True):
        redacted = redacted.replace(name, mapping[name])
    if names:
        log.append(f"{len(names)} corporate name(s) masked")

    # Addresses: "412 Lisburn Road", "Unit 4", plus UPPERCASE headings like "DUNCRUE INDUSTRIAL ESTATE"
    suffix = r"(?:Road|Street|Square|Estate|Avenue|Lane|Park|Place)"
    mixed = rf"\b(?:\d+[A-Za-z]?|Unit[ \t]+\d+),?[ \t]+[A-Z][A-Za-z]*(?:[ \t]+[A-Z][A-Za-z]*){{0,3}}?[ \t]+{suffix}(?:[ \t]+(?:East|West|North|South))?\b"
    upper = r"\b(?:UNIT[ \t]+\d+,[ \t]+)?(?:[A-Z]{3,}[ \t]+){1,3}(?:ROAD|STREET|SQUARE|ESTATE|AVENUE|LANE|PARK|PLACE)(?:[ \t]+(?:EAST|WEST|NORTH|SOUTH))?\b"
    changed = False
    for pat in (mixed, upper):
        if re.search(pat, redacted):
            redacted = re.sub(pat, "[PROPERTY_ADDRESS_REDACTED]", redacted)
            changed = True
    if changed:
        log.append("street/unit addresses masked")

    return redacted, log


# ---------------------------------------------------------
# 5. OFFLINE RULE-BASED ANALYSER (works on ANY pasted text)
# ---------------------------------------------------------
LEVEL_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _parse_date(s: str):
    try:
        return datetime.strptime(" ".join(s.split()), "%d %B %Y")
    except ValueError:
        return None


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _wault_note(text: str) -> str:
    m = re.search(r"Term:\s*(\d+)\s*years?\s*from\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text, re.I)
    if not m:
        return "Could not extract the term from this text. Confirm commencement, expiry and any breaks before relying on a WAULT figure."
    years, start = int(m.group(1)), _parse_date(m.group(2))
    if not start:
        return f"Term of {years} years identified but the start date could not be read — verify manually."
    expiry = start.replace(year=start.year + years)
    now = datetime.now()
    to_expiry = (expiry - now).days / 365.25
    note = f"Term of {years} years from {start:%d %B %Y} expires {expiry:%d %B %Y} (c. {max(to_expiry, 0):.1f} years unexpired from today)."
    b = re.search(r"(?:break|determine)[^\n]*?\bon\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text, re.I)
    brk = _parse_date(b.group(1)) if b else None
    if brk:
        to_break = (brk - now).days / 365.25
        if to_break > 0:
            note += f" A break on {brk:%d %B %Y} cuts the income-secure period to c. {to_break:.1f} years: WAULT to break, not expiry, is the prudent basis."
        else:
            note += f" A break date of {brk:%d %B %Y} appears to have passed — confirm whether it was exercised or lapsed."
    return note


def _ner_note(text: str) -> str:
    rent = re.search(r"(?:Passing Rent|Headline rent|Rent)[^£\n]*£\s*([\d,]+)", text, re.I)
    term = re.search(r"Term:\s*(\d+)\s*years?", text, re.I)
    free = re.search(r"(\d+)[- ]month rent[- ]free", text, re.I)
    cap = re.search(r"£\s*([\d,]+)\s*capital contribution", text, re.I)
    if not (free or cap):
        return "No rent-free period or capital contribution identified in the extract. Confirm there is no separate side letter or agreement for lease."
    if not (rent and term):
        return "Incentives identified but headline rent or term could not be extracted — calculate net effective rent manually."
    r, n = _money(rent.group(1)), int(term.group(1))
    f = int(free.group(1)) if free else 0
    c = _money(cap.group(1)) if cap else 0
    ner = (r * (n - f / 12) - c) / n
    return (
        f"Headline rent £{r:,.0f} pa. After a {f}-month rent-free and £{c:,.0f} capital contribution spread over {n} years, "
        f"net effective rent is c. £{ner:,.0f} pa ({(1 - ner / r) * 100:.0f}% below headline). "
        "Valuing at headline without adjustment would overstate value."
    )


def _rules(text: str):
    """(category, regex, level_fn, impact, question)"""
    def brk_level(line): return "High" if re.search(r"tenant", line, re.I) else "Medium"

    def review_level(line):
        if re.search(r"fully fitted|installed by the tenant", line, re.I) and not re.search(r"disregard", line, re.I):
            return "High"
        if re.search(r"\bnone\b|no rent review", line, re.I):
            return "Medium"
        if re.search(r"upward[- ]only", line, re.I) and not re.search(r"assum|disregard", line, re.I):
            return "Low"
        return "Medium"

    def sc_level(line):
        capped = re.search(r"\bcap\b", text, re.I)
        over = re.search(r"budget|running at|exceed", text, re.I)
        return "High" if capped and over else "Medium"

    def repair_level(line):
        if re.search(r"schedule of condition|corrosion|no mechanism|not recover|defect", line, re.I):
            return "High"
        if re.search(r"internal repairing|internal only", line, re.I):
            return "Medium"
        if re.search(r"full repairing|\bFRI\b", line, re.I):
            return "Low"
        return "Medium"

    def user_level(line):
        return "High" if re.search(r"solely|strictly|expressly prohibited|absolute discretion", line, re.I) else "Medium"

    return [
        ("Term & Break Options", r"break option|option to determine|\bbreak\b", brk_level,
         "A break shortens the income-secure term. WAULT should be run to the break, and void/re-letting exposure pushes the equivalent yield out.",
         "Has break notice been served, or are renewal/re-gear talks under way? What are the break conditions and any penalty?"),
        ("Incentives & Side Letters", r"rent[- ]free|capital contribution|side letter|reverse premium|incentive", lambda l: "High",
         "Headline rent overstates net effective rent. The valuation should reflect unexpired incentives and any side-letter terms.",
         "Obtain every side letter / agreement for lease. Have the rent-free and contribution expired or been paid? Is headline or net effective rent the right capitalisation basis?"),
        ("Rent Review Mechanism", r"rent review", review_level,
         "Review assumptions and disregards drive reversionary ERV. Unusual assumptions or no review limit income growth.",
         "Are the review assumptions and disregards standard? Does passing rent sit above or below current ERV for comparable units?"),
        ("Service Charge & Caps", r"service charge", sc_level,
         "A cap below actual running costs creates landlord leakage that reduces net operating income and net yield.",
         "Obtain the latest service charge accounts and budget. How large is the shortfall against the cap, and is it recoverable?"),
        ("Repairing Obligations", r"repairing|repair|schedule of condition|\bFRI\b", repair_level,
         "Limited repair liability or non-recoverable structural costs may justify a capex deduction and reduce dilapidations recovery.",
         "Obtain the Schedule of Condition and a building surveyor's cost estimate for outstanding structural / roof works."),
        ("Alienation & User Clause", r"alienation|assign|sublet|subletting", lambda l: "Medium",
         "Restrictions on assignment and subletting limit tenant flexibility and the investment's liquidity on re-letting.",
         "Check covenant strength of tenant and any guarantor / AGA. Are restrictions typical for the local market?"),
        ("Alienation & User Clause", r"user clause|use permitted|restricted (strictly )?to|permitted use", user_level,
         "A narrow user clause shrinks the pool of future tenants, which widens the yield and lengthens void assumptions.",
         "Is the permitted use consistent with planning consent? How marketable is the unit for alternative uses?"),
    ]


def analyze_offline(text: str, sector: str) -> LeaseTriageReport:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    items: list[ClauseRisk] = []
    for cat, pattern, level_fn, impact, question in _rules(text):
        best, best_hits = None, 0
        for line in lines:
            hits = len(re.findall(pattern, line, re.I))
            if hits > best_hits:
                best, best_hits = line, hits
        if best:
            items.append(ClauseRisk(
                category=cat, risk_level=level_fn(best), source_quote=best[:300],
                valuation_impact=impact, surveyor_verification_question=question,
            ))
    items.sort(key=lambda i: LEVEL_ORDER[i.risk_level])

    highs = [i.category for i in items if i.risk_level == "High"]
    counts = {lvl: sum(1 for i in items if i.risk_level == lvl) for lvl in LEVEL_ORDER}
    if items:
        summary = (
            f"Rule-based scan flagged {counts['High']} high, {counts['Medium']} medium and {counts['Low']} low risk items. "
            + (f"Priority areas: {', '.join(highs)}." if highs else "No high-risk items detected by keyword rules.")
        )
    else:
        summary = "No recognisable lease clauses were detected. Check the pasted text includes clause wording (break, rent review, repairs, service charge, etc.)."
    return LeaseTriageReport(
        sector_detected=f"{sector} (Northern Ireland)",
        headline_summary=summary,
        wault_and_income_note=_wault_note(text),
        net_effective_rent_note=_ner_note(text),
        risk_items=items,
        overall_valuation_caution=CAUTION,
    )


# ---------------------------------------------------------
# 6. GEMINI API CALL
# ---------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are a Commercial Property Valuation & Lease Advisory Assistant operating in Northern Ireland (UK).
Analyze the provided commercial lease extract or Heads of Terms strictly from a valuation perspective.

RULES:
1. NEVER invent or hallucinate clauses, dates, or figures not present in the text.
2. Always quote the exact supporting phrase from the input text in 'source_quote'. If a topic is not covered, either omit it or use 'Not stated in extract'.
3. Focus on valuation drivers: WAULT (to break vs expiry), net effective rent vs headline rent, service charge leakage/caps, repairing liabilities (FRI vs Schedule of Condition), and rent-review assumptions/disregards.
4. Note Northern Ireland statutory context (Business Tenancies (NI) Order 1996) where relevant, rather than the English Landlord & Tenant Act 1954.
5. Generate practical, specific verification questions for a chartered surveyor.
6. Redacted placeholders such as [ENTITY_1] or [POSTCODE_REDACTED] are intentional; do not comment on them.
7. Use today's date (provided) when computing unexpired term.
8. overall_valuation_caution must state this is draft triage requiring RICS surveyor sign-off.
"""


def analyze_lease_with_gemini(lease_text: str, sector: str, api_key: str) -> LeaseTriageReport:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = (
        f"Today's date: {datetime.now():%d %B %Y}\n"
        f"Property Sector: {sector}\n\nLease / Heads of Terms Extract:\n{lease_text}"
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=LeaseTriageReport,
            temperature=0.1,
        ),
    )
    if isinstance(getattr(response, "parsed", None), LeaseTriageReport):
        return response.parsed
    raw = re.sub(r"^```(?:json)?|```$", "", (response.text or "").strip(), flags=re.M).strip()
    return LeaseTriageReport(**json.loads(raw))


# ---------------------------------------------------------
# 7. HELPERS: quote verification + markdown export
# ---------------------------------------------------------
def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_verified(quote: str, source: str):
    """True/False if the quote appears verbatim in the analysed text; None if 'Not stated'."""
    if quote.strip().lower().startswith("not stated"):
        return None
    segments = [s.strip(" \"'") for s in re.split(r"\.{3}|…", quote) if s.strip(" \"'")]
    src = _norm(source)
    return bool(segments) and all(_norm(s) in src for s in segments)


def build_markdown_file_note(report: LeaseTriageReport, sector: str, redacted: bool, source_text: str, engine: str) -> str:
    timestamp = datetime.now().strftime("%d %B %Y, %H:%M")
    lines = [
        "# VALUATION & LEASE ADVISORY — PRELIMINARY RISK TRIAGE NOTE",
        f"**Generated:** {timestamp} | **Sector:** {sector} | **Engine:** {engine} | **Local PII Redaction:** {'Enabled' if redacted else 'Disabled'}",
        "",
        "> **GOVERNANCE NOTICE:** Automated preliminary triage only. All figures, dates, and legal interpretations must be verified against executed lease documentation and reviewed by a qualified surveyor.",
        "",
        "## 1. Executive Summary",
        report.headline_summary,
        "",
        "## 2. Income Security & Net Effective Rent Notes",
        f"- **WAULT & Term Security:** {report.wault_and_income_note}",
        f"- **Net Effective Rent & Incentives:** {report.net_effective_rent_note}",
        "",
        "## 3. Clause-by-Clause Valuation Risk Log",
    ]
    for item in report.risk_items:
        v = quote_verified(item.source_quote, source_text)
        tag = "" if v is None else (" ✅ verified in text" if v else " ⚠️ not found verbatim — check")
        lines.extend([
            f"### [{item.risk_level.upper()} RISK] {item.category}",
            f"- **Extracted Lease Text:** *\"{item.source_quote}\"*{tag}",
            f"- **Valuation Impact:** {item.valuation_impact}",
            f"- **Surveyor Verification Action:** {item.surveyor_verification_question}",
            "",
        ])
    lines.extend(["---", f"**Sign-Off Reminder:** {report.overall_valuation_caution}"])
    return "\n".join(lines)


# ---------------------------------------------------------
# 8. SESSION STATE + CALLBACKS
# ---------------------------------------------------------
def _get_env_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:
            key = ""
    return key


MODE_AI = "Gemini AI analysis"
MODE_OFFLINE = "Offline rule-based scan (no API key)"

env_key = _get_env_key()
st.session_state.setdefault("sample_choice", PLACEHOLDER)
st.session_state.setdefault("lease_text", "")
st.session_state.setdefault("sector", SECTORS[0])
st.session_state.setdefault("api_key_input", env_key)
st.session_state.setdefault("mode", MODE_AI if env_key else MODE_OFFLINE)


def load_sample():
    """Runs when the sample dropdown changes: pushes sample text + sector into the widgets and clears old results."""
    text, sector = SAMPLE_LEASES[st.session_state["sample_choice"]]
    st.session_state["lease_text"] = text
    st.session_state["sector"] = sector
    for k in ("last_report", "last_sig", "last_engine", "last_source", "last_sector", "last_redacted"):
        st.session_state.pop(k, None)


def purge():
    st.session_state.clear()


# ---------------------------------------------------------
# 9. UI
# ---------------------------------------------------------
st.title("🏢 Commercial Lease & Rent-Review Risk Triage")
st.caption("Prototype Valuation Prep Tool (Northern Ireland Context) • Built to support early-stage surveyor due diligence")

with st.sidebar:
    st.header("🛡️ Data Governance & Privacy")
    enable_redaction = st.toggle(
        "Auto-Redact Client & Property Names",
        value=True,
        help="Masks company names, postcodes, and street addresses locally in Python before any text is sent to the AI model.",
    )
    st.info(
        "**Client-Confidentiality Architecture:**\n"
        "- Stateless session memory (no database storage)\n"
        "- Pre-API local entity masking\n"
        "- Synthetic NI test cases only\n"
        "- Requires RICS surveyor sign-off"
    )
    st.button("🗑️ Purge Session Data", use_container_width=True, on_click=purge)

    st.divider()
    st.header("⚙️ Analysis Engine")
    st.radio("Mode", [MODE_AI, MODE_OFFLINE], key="mode")
    st.text_input(
        "Gemini API key",
        type="password",
        key="api_key_input",
        help="Or set GEMINI_API_KEY as an environment variable / Streamlit secret. Never stored beyond this session.",
    )
    if st.session_state["mode"] == MODE_AI and not st.session_state["api_key_input"]:
        st.warning("No API key — enter one above or switch to the offline scan.")

col_input, col_output = st.columns([1, 1.25], gap="large")

with col_input:
    st.subheader("1. Lease / Heads of Terms Input")

    st.selectbox(
        "Load a pre-built Northern Ireland test case:",
        options=list(SAMPLE_LEASES.keys()),
        key="sample_choice",
        on_change=load_sample,
    )
    st.selectbox("Property Sector:", SECTORS, key="sector")
    st.text_area(
        "Paste anonymized lease clauses or Heads of Terms (you can also edit the sample):",
        key="lease_text",
        height=310,
        placeholder="Paste commercial lease extract, rent review clause, break option, or Heads of Terms here...",
    )

    raw_text = st.session_state["lease_text"]
    sector = st.session_state["sector"]

    if enable_redaction and raw_text.strip():
        processed_text, redaction_log = redact_sensitive_entities(raw_text)
        with st.expander("🔍 Preview Redacted Text Sent to AI"):
            if redaction_log:
                st.success("Applied local masks: " + ", ".join(redaction_log))
            else:
                st.write("No company names, postcodes, or street addresses detected.")
            st.code(processed_text, language="text")
    else:
        processed_text = raw_text

    run_clicked = st.button("⚡ Run Valuation Risk Triage", type="primary", use_container_width=True)

current_sig = hash((processed_text, sector))

with col_output:
    st.subheader("2. Valuation & Lease Advisory Triage")

    if run_clicked:
        if not processed_text.strip():
            st.warning("Please select a sample lease or paste lease text on the left first.")
        else:
            use_ai = st.session_state["mode"] == MODE_AI and bool(st.session_state["api_key_input"])
            engine = "Gemini 2.5 Flash" if use_ai else "Offline rule-based scan"
            with st.spinner("Extracting lease covenants and evaluating valuation sensitivities..."):
                try:
                    if use_ai:
                        report = analyze_lease_with_gemini(processed_text, sector, st.session_state["api_key_input"])
                    else:
                        report = analyze_offline(processed_text, sector)
                except Exception as e:
                    st.error(f"Gemini call failed: {e}. Showing the offline rule-based scan of YOUR text instead.")
                    report = analyze_offline(processed_text, sector)
                    engine = "Offline rule-based scan (AI call failed)"
            st.session_state.update(
                last_report=report, last_sig=current_sig, last_engine=engine,
                last_source=processed_text, last_sector=sector, last_redacted=enable_redaction,
            )

    if "last_report" in st.session_state:
        rep: LeaseTriageReport = st.session_state["last_report"]
        src = st.session_state.get("last_source", processed_text)

        if st.session_state.get("last_sig") != current_sig:
            st.info("The input has changed since this report was generated — click **Run Valuation Risk Triage** to refresh.")
        st.caption(f"Engine: {st.session_state.get('last_engine', 'n/a')} • Sector: {rep.sector_detected}")

        st.markdown(f"**Executive Summary:** {rep.headline_summary}")

        m1, m2 = st.columns(2)
        with m1:
            st.info(f"**WAULT & Income Security**\n\n{rep.wault_and_income_note}")
        with m2:
            st.info(f"**Net Effective Rent & Incentives**\n\n{rep.net_effective_rent_note}")

        st.markdown("### Clause Risk Matrix & Verification Checklist")
        if not rep.risk_items:
            st.write("No clause risks identified.")
        for item in rep.risk_items:
            badge = {"High": "🔴 HIGH", "Medium": "🟠 MEDIUM", "Low": "🟢 LOW"}.get(item.risk_level, item.risk_level)
            with st.expander(f"{badge} — {item.category}", expanded=(item.risk_level == "High")):
                st.markdown(f"**Extracted Clause:** *\"{item.source_quote}\"*")
                v = quote_verified(item.source_quote, src)
                if v is True:
                    st.caption("✅ Quote verified verbatim in the analysed text")
                elif v is False:
                    st.caption("⚠️ Quote not found verbatim in the text — check before relying on it")
                st.markdown(f"**Valuation Impact:** {item.valuation_impact}")
                st.markdown(f"**Surveyor Verification Check:** {item.surveyor_verification_question}")

        st.warning(rep.overall_valuation_caution)

        st.download_button(
            label="📥 Download Valuation File Note (.md)",
            data=build_markdown_file_note(
                rep, st.session_state.get("last_sector", sector), st.session_state.get("last_redacted", enable_redaction),
                src, st.session_state.get("last_engine", "n/a"),
            ),
            file_name="NI_Lease_Valuation_Triage_Note.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        st.markdown(
            """
            **Ready to analyze.** Pick a sample on the left (or paste your own lease text) and click **Run Valuation Risk Triage** to see:
            - **WAULT & Yield Sensitivities** (tenant breaks, lease length, review patterns)
            - **Net Effective Rent Traps** (rent-free periods, capital contributions, service charge caps)
            - **Capex & Repair Exposure** (Schedules of Condition, non-recoverable structural repairs)
            - **Surveyor Verification Checklist** ready to export into a valuation file
            """
        )
