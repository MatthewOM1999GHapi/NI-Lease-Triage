import json
import os
import re
from datetime import datetime
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# ---------------------------------------------------------
# 1. PAGE CONFIG & STYLING
# ---------------------------------------------------------
st.set_page_config(
    page_title="NI Commercial Lease & Valuation Risk Triage",
    page_icon="🏢",
    layout="wide",
)

# ---------------------------------------------------------
# 2. STRUCTURED OUTPUT SCHEMA (PYDANTIC)
# ---------------------------------------------------------
class ClauseRisk(BaseModel):
    category: str = Field(
        description="One of: Term & Break Options, Rent Review Mechanism, Repairing Obligations, Service Charge & Caps, Alienation & User Clause, Incentives & Side Letters"
    )
    risk_level: str = Field(description="High, Medium, or Low")
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


# ---------------------------------------------------------
# 3. PRE-LOADED NORTHERN IRELAND SAMPLE LEASES
# ---------------------------------------------------------
SAMPLE_LEASES = {
    "Select a sample NI lease extract...": "",
    "Sample 1: Belfast Industrial Unit (Duncrue / Mallusk)": """HEADS OF TERMS & LEASE EXTRACT - UNIT 4, DUNCRUE INDUSTRIAL ESTATE, BELFAST BT3 9BP
Landlord: Lagan Harbour Developments Ltd
Tenant: Ulster Freight & Logistics (NI) Ltd
Demise: Detached warehouse of 18,500 sq ft GIA with concrete service yard.
Term: 10 years from 1 May 2024, contracted inside the Business Tenancies (Northern Ireland) Order 1996.
Break Option: Tenant-only option to determine on 1 May 2027 (Year 3) upon giving not less than 6 months' prior written notice, conditional upon payment of principal rent up to the break date and delivery of vacant possession. No break penalty payable.
Passing Rent: £129,500 per annum exclusive (£7.00 per sq ft), payable quarterly in advance.
Rent Review: Upward-only open market rent review at Year 5 (1 May 2029), assuming a willing landlord and willing tenant on a 5-year term, disregarding tenant's voluntary fit-out works.
Repairing Liability: Full Repairing and Insuring (FRI), save that the Tenant's obligation to put and keep the roof cladding and structural steelwork in repair shall be limited by reference to a photographic Schedule of Condition dated April 2024 (which notes extensive cut-edge corrosion to the south roof slope).
Alienation: Assignment of whole permitted with Landlord's consent (not to be unreasonably withheld), subject to an Authorised Guarantee Agreement (AGA). Subletting of part prohibited.""",
    "Sample 2: Belfast City Centre Office Suite (Linen Quarter)": """LEASE & SIDE LETTER SUMMARY - 3RD FLOOR, DONEGALL SQUARE EAST, BELFAST BT1 5HD
Landlord: Clarendon Commercial Properties PLC
Tenant: FinTech Advisory Solutions UK Ltd
Demise: 6,200 sq ft NIA Grade A open-plan office suite plus 4 basement car spaces.
Term: 5 years from 1 September 2025.
Passing Rent: Headline rent of £142,600 per annum (£23.00 per sq ft).
Side Letter Concession: By a personal side letter dated 28 August 2025, the Landlord granted the Tenant a 12-month rent-free period from term commencement, plus a £31,000 capital contribution toward partitioning works.
Rent Review: None during the 5-year term.
Service Charge: Tenant pays a fair proportion (18.4%) of building running costs, subject to an all-inclusive Service Charge Cap of £4.50 per sq ft per annum (£27,900), indexed annually to CPI capped at 3%. Note: Current building service charge budget is running at £6.15 per sq ft due to lift modernization and M&E energy costs.
User Clause: Restricted strictly to Class B1(a) financial and professional services offices.""",
    "Sample 3: High Street Retail Unit (Lisburn Road, Belfast)": """EXTRACT OF LEASE - 412 LISBURN ROAD, BELFAST BT9 6GN
Landlord: Malone Retail Investments Ltd
Tenant: Artisan Roastery & Bakery (Belfast) Ltd
Demise: Ground floor retail unit (1,450 sq ft) and basement storage (400 sq ft).
Term: 15 years from 1 January 2021.
Passing Rent: £42,000 per annum exclusive.
Rent Review: 5-yearly upward-only open market rent review (next review 1 January 2026). Hypothetical lease assumptions require the unit to be valued as fully fitted-out for high-end cafe/bakery use including extraction plant installed by the Tenant at a cost of £65,000.
User Clause: Use permitted solely as an artisan coffee shop and bakery; general Class A1 retail use is expressly prohibited without Landlord's absolute discretion.
Repair & Insurance: Internal repairing only for the Tenant; Landlord responsible for structure and exterior, with no mechanism in the lease to recover structural roof repairs via service charge.""",
}

# ---------------------------------------------------------
# 4. LOCAL PII & ENTITY REDACTOR (RUNS BEFORE API CALL)
# ---------------------------------------------------------
def redact_sensitive_entities(text: str) -> tuple[str, list[str]]:
    """Strips company names, postcodes, and street numbers locally before external LLM processing."""
    redacted = text
    log = []

    # Redact UK/NI Postcodes (e.g., BT3 9BP, BT1 5HD)
    postcode_pattern = r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"
    if re.search(postcode_pattern, redacted, flags=re.IGNORECASE):
        redacted = re.sub(postcode_pattern, "[POSTCODE_REDACTED]", redacted, flags=re.IGNORECASE)
        log.append("UK/NI postcodes masked")

    # Redact Company Names ending in Ltd, Limited, PLC, LLP
    company_pattern = r"\b[A-Z][A-Za-z0-9&\(\)\s\-]{2,45}?\b(?:Ltd|Limited|PLC|plc|LLP)\b\.?"
    companies = re.findall(company_pattern, redacted)
    for idx, comp in enumerate(dict.fromkeys(companies), start=1):
        redacted = redacted.replace(comp, f"[ENTITY_{idx}]")
    if companies:
        log.append(f"{len(set(companies))} corporate entity name(s) masked")

    # Redact Street Numbers + Road/Street/Square/Estate names
    address_pattern = r"\b(?:\d+[A-Za-z]?|Unit\s+\d+)\s+[A-Z][A-Za-z\s]+(?:Road|Street|Square|Estate|Avenue|Lane|Park|Place|East|West|North|South)\b"
    if re.search(address_pattern, redacted):
        redacted = re.sub(address_pattern, "[PROPERTY_ADDRESS_REDACTED]", redacted)
        log.append("Street/unit address masked")

    return redacted, log


# ---------------------------------------------------------
# 5. OFFLINE FALLBACK DATA (IF API KEY NOT SET OR RATE-LIMITED)
# ---------------------------------------------------------
FALLBACK_REPORT = LeaseTriageReport(
    sector_detected="Industrial / Logistics (Northern Ireland)",
    headline_summary="10-year lease at £129,500 pax (£7.00/sq ft) with a major Year 3 tenant-only break option and a Schedule of Condition limiting roof repair liability.",
    wault_and_income_note="Headline unexpired term is ~7.4 years, but the May 2027 tenant-only break compresses WAULT-to-break to under 6 months (subject to notice check). Valuers must capitalize to the break date unless strong evidence shows the tenant will remain.",
    net_effective_rent_note="Passing rent is £7.00/sq ft with no disclosed rent-free period in the extract; verify whether any separate side letter exists.",
    risk_items=[
        ClauseRisk(
            category="Term & Break Options",
            risk_level="High",
            source_quote="Tenant-only option to determine on 1 May 2027 (Year 3) upon giving not less than 6 months' prior written notice... No break penalty payable.",
            valuation_impact="Creates immediate void and re-letting risk in May 2027; significantly increases equivalent yield compared to a 10-year certain income stream.",
            surveyor_verification_question="Has the tenant already served written break notice (deadline ~1 November 2026), or have lease renewal/re-gear discussions started?",
        ),
        ClauseRisk(
            category="Repairing Obligations",
            risk_level="High",
            source_quote="limited by reference to a photographic Schedule of Condition dated April 2024 (which notes extensive cut-edge corrosion to the south roof slope).",
            valuation_impact="Restricts Landlord's ability to recover future roof remediation costs via dilapidations; valuer should allow a explicit capital expenditure (capex) deduction.",
            surveyor_verification_question="Obtain the April 2024 Schedule of Condition and building survey cost estimate for treating/overcladding the south roof slope.",
        ),
        ClauseRisk(
            category="Rent Review Mechanism",
            risk_level="Low",
            source_quote="Upward-only open market rent review at Year 5 (1 May 2029)... disregarding tenant's voluntary fit-out works.",
            valuation_impact="Standard institutional upward-only review pattern protecting downside income if the break is not exercised.",
            surveyor_verification_question="Confirm £7.00/sq ft aligns with current Duncrue/Mallusk prime & secondary industrial ERVs.",
        ),
        ClauseRisk(
            category="Alienation & User Clause",
            risk_level="Medium",
            source_quote="Assignment of whole permitted with Landlord's consent... subject to an Authorised Guarantee Agreement (AGA). Subletting of part prohibited.",
            valuation_impact="AGA provides covenant protection on assignment, though prohibition on subletting part slightly limits flexibility for larger 18,500 sq ft floorplates.",
            surveyor_verification_question="Verify tenant covenant strength (credit check / filed accounts) to assess likelihood of assignment or break.",
        ),
    ],
    overall_valuation_caution="DRAFT AI TRIAGE ONLY — Requires physical inspection, full title/lease verification, and RICS Registered Valuer sign-off before adoption in any valuation calculation.",
)

# ---------------------------------------------------------
# 6. GEMINI API CALL FUNCTION
# ---------------------------------------------------------
def analyze_lease_with_gemini(lease_text: str, sector: str, api_key: str) -> LeaseTriageReport:
    client = genai.Client(api_key=api_key)

    system_instruction = """
    You are a Senior Commercial Property Valuation & Lease Advisory Assistant operating in Northern Ireland (UK).
    Analyze the provided commercial lease extract or Heads of Terms strictly from an RICS commercial valuation perspective.
    
    RULES:
    1. NEVER invent or hallucinate clauses, dates, or figures not present in the text.
    2. Always quote the exact supporting phrase from the input text in 'source_quote'.
    3. Focus on valuation drivers: WAULT (to break vs expiry), net effective rent vs headline rent, service charge leakage/caps, repairing liabilities (FRI vs Schedule of Condition), and rent-review assumptions/disregards.
    4. Note Northern Ireland statutory context (e.g., Business Tenancies (NI) Order 1996) where relevant, rather than English Landlord & Tenant Act 1954.
    5. Generate practical, specific verification questions for a chartered surveyor.
    """

    prompt = f"Property Sector: {sector}\n\nLease / Heads of Terms Extract:\n{lease_text}"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=LeaseTriageReport,
            temperature=0.1,
        ),
    )

    parsed_dict = json.loads(response.text)
    return LeaseTriageReport(**parsed_dict)


# ---------------------------------------------------------
# 7. MARKDOWN FILE NOTE GENERATOR (FOR EXPORT)
# ---------------------------------------------------------
def build_markdown_file_note(report: LeaseTriageReport, sector: str, redacted: bool) -> str:
    timestamp = datetime.now().strftime("%d %B %Y, %H:%M")
    lines = [
        "# VALUATION & LEASE ADVISORY — PRELIMINARY RISK TRIAGE NOTE",
        f"**Generated:** {timestamp} | **Sector:** {sector} | **Local PII Redaction:** {'Enabled' if redacted else 'Disabled'}",
        "",
        "> **GOVERNANCE NOTICE:** Automated preliminary triage only. All figures, dates, and legal interpretations must be verified against executed lease documentation and inspected by an RICS Registered Valuer.",
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
        lines.extend(
            [
                f"### [{item.risk_level.upper()} RISK] {item.category}",
                f"- **Extracted Lease Text:** *\"{item.source_quote}\"*",
                f"- **Valuation Impact:** {item.valuation_impact}",
                f"- **Surveyor Verification Action:** {item.surveyor_verification_question}",
                "",
            ]
        )
    lines.extend(["---", f"**Sign-Off Reminder:** {report.overall_valuation_caution}"])
    return "\n".join(lines)


# ---------------------------------------------------------
# 8. STREAMLIT USER INTERFACE
# ---------------------------------------------------------
st.title("🏢 Commercial Lease & Rent-Review Risk Triage")
st.caption(
    "Prototype Valuation Prep Tool (Northern Ireland Context) • Built to support early-stage surveyor due diligence"
)

# Sidebar: Governance, Privacy & Settings
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

    if st.button("🗑️ Purge Session Data", use_container_width=True):
        st.session_state.clear()
        st.rerun()

    st.divider()
    st.header("⚙️ Demo Mode Settings")
    api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    demo_fallback = st.toggle(
        "Offline Demo Fallback Mode",
        value=not bool(api_key),
        help="Uses pre-computed sample output if no API key is configured or internet is unavailable.",
    )

# Main Two-Column Workspace
col_input, col_output = st.columns([1, 1.25], gap="large")

with col_input:
    st.subheader("1. Lease / Heads of Terms Input")

    selected_sample = st.selectbox(
        "Load a pre-built Northern Ireland test case:",
        options=list(SAMPLE_LEASES.keys()),
    )

    sector = st.selectbox(
        "Property Sector:",
        ["Industrial / Logistics", "City Centre Office", "High Street Retail", "Mixed-Use / Investment"],
    )

    default_text = SAMPLE_LEASES[selected_sample]
    raw_lease_text = st.text_area(
        "Paste anonymized lease clauses or Heads of Terms:",
        value=default_text,
        height=310,
        placeholder="Paste commercial lease extract, rent review clause, break option, or Heads of Terms here...",
    )

    if enable_redaction and raw_lease_text.strip():
        processed_text, redaction_log = redact_sensitive_entities(raw_lease_text)
        with st.expander("🔍 Preview Redacted Text Sent to AI"):
            if redaction_log:
                st.success("Applied local masks: " + ", ".join(redaction_log))
            else:
                st.write("No company names, postcodes, or street numbers detected.")
            st.code(processed_text, language="text")
    else:
        processed_text = raw_lease_text

    run_clicked = st.button("⚡ Run Valuation Risk Triage", type="primary", use_container_width=True)

with col_output:
    st.subheader("2. Valuation & Lease Advisory Triage")

    if run_clicked:
        if not processed_text.strip():
            st.warning("Please select a sample lease or paste lease text on the left first.")
        else:
            with st.spinner("Extracting lease covenants and evaluating valuation sensitivities..."):
                try:
                    if demo_fallback or not api_key:
                        report = FALLBACK_REPORT
                        st.caption("ℹ️ Running in Offline Demo Mode (Sample Industrial Output).")
                    else:
                        report = analyze_lease_with_gemini(processed_text, sector, api_key)
                    st.session_state["last_report"] = report
                    st.session_state["last_sector"] = sector
                except Exception as e:
                    st.error(f"API call failed ({e}). Switching to Offline Demo Fallback.")
                    st.session_state["last_report"] = FALLBACK_REPORT
                    st.session_state["last_sector"] = sector

    if "last_report" in st.session_state:
        rep: LeaseTriageReport = st.session_state["last_report"]

        st.markdown(f"**Executive Summary:** {rep.headline_summary}")

        m1, m2 = st.columns(2)
        with m1:
            st.info(f"**WAULT & Income Security**\n\n{rep.wault_and_income_note}")
        with m2:
            st.info(f"**Net Effective Rent & Incentives**\n\n{rep.net_effective_rent_note}")

        st.markdown("### Clause Risk Matrix & Verification Checklist")
        for item in rep.risk_items:
            badge = {"High": "🔴 HIGH", "Medium": "🟠 MEDIUM", "Low": "🟢 LOW"}.get(
                item.risk_level, f"⚪ {item.risk_level.upper()}"
            )
            with st.expander(f"{badge} — {item.category}", expanded=(item.risk_level == "High")):
                st.markdown(f"**Extracted Clause:** *\"{item.source_quote}\"*")
                st.markdown(f"**Valuation Impact:** {item.valuation_impact}")
                st.markdown(f"**Surveyor Verification Check:** {item.surveyor_verification_question}")

        st.warning(rep.overall_valuation_caution)

        file_note_md = build_markdown_file_note(
            rep, st.session_state.get("last_sector", sector), enable_redaction
        )
        st.download_button(
            label="📥 Download Valuation File Note (.md)",
            data=file_note_md,
            file_name="NI_Lease_Valuation_Triage_Note.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        st.markdown(
            """
            **Ready to analyze.** Select one of the three Northern Ireland sample leases on the left and click **Run Valuation Risk Triage** to see:
            - **WAULT & Yield Sensitivities** (tenant breaks, lease length, review patterns)
            - **Net Effective Rent Traps** (rent-free periods, capital contributions, service charge caps)
            - **Capex & Repair Exposure** (Schedules of Condition, non-recoverable structural repairs)
            - **Surveyor Verification Checklist** ready to export into a valuation file
            """
        )
