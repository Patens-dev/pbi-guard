# pbi-guard

Automated contract testing, architectural linting, and governance for Power BI semantic models (`.pbip` / TMDL).

Run DAX logic checks, tabular modeling assertions, and auditable UAT sign-off reports locally in milliseconds, without opening Power BI Desktop, writing complex regular expressions, or configuring cloud credentials.

---

## The Complete Guide: Why, When & How

### 1. Why `pbi-guard` Exists
* **The "Executive Embarrassment" Filter:** Dashboard consumers rarely notice subtle calculation errors; errors are usually caught by the leadership team during Monday morning executive meetings.
* **The "Magic Number" Epidemic:** Developers under pressure frequently slip arbitrary multipliers or scalar adjustments (`* 1.042` or `+ 15000`) into production measures to force dashboards to match manual spreadsheets.
* **The Compiler for AI Agents:** When using Claude or Cursor via MCP to modify TMDL files, probabilistic models hallucinate code or invent non-existent columns. `pbi-guard` provides a deterministic gate that prevents unvetted changes from being saved or deployed.
* **Ending "YOLO Friday" Releases:** Replacing manual, error-prone spot checks in Excel with automated, deterministic assertions that run in under 100 milliseconds.

---

### 2. When to Use It
| Stage | Trigger | Purpose                                                                                                         |
| :--- | :--- |:----------------------------------------------------------------------------------------------------------------|
| **Local Dev** | Before saving or staging git commits | Verify DAX syntax, check for uncompressed calculated columns, and validate formula definitions locally.         |
| **AI / MCP Workflows** | After an LLM/agent edits TMDL | Validate agent output. If `pbi-guard` fails, pipe the failure output back to the agent for self-correction.     |
| **Production UAT Sign-Off** | Release deployment pipeline | Generate a formal `--export-report uat_audit.md` artifact attached to release tickets for stakeholder sign-off. |

---

### 3. How to Use It

#### Local Execution
Run against any `.pbip` project file, `.SemanticModel` directory, or raw `definition/` folder :

```bash
# Run standard test suite
python main.py -c pbi_tests.yml -m "../path/to/Sales.pbip"

# Run tests and generate an auditable UAT sign-off report
python main.py -c pbi_tests.yml -m "../path/to/Sales.pbip" --export-report uat_audit.md
```

---

## Installation

Requires Python 3.9+.

```bash
git clone https://github.com/Patens-dev/pbi-guard.git
cd pbi-guard
pip install pyyaml
```

---

## Full Test Suite Example (`pbi_tests.yml`)

```yaml
version: 1

assertions:
  # ==============================================================================
  # 1. STORAGE & ARCHITECTURE
  # ==============================================================================

  # Keep uncompressed memory down
  - name: "Arch: No calculated columns in financials table"
    type: no_calculated_columns
    table: "financials"

  # Prevent file size bloat from hidden calendars
  - name: "Arch: Block auto date/time hidden tables"
    type: no_auto_date_tables

  # Stop numeric IDs, years, or zip codes from auto-summing in visuals
  - name: "Arch: Prevent default aggregation on Year column"
    type: column_rule
    table: "financials"
    column: "Year"
    summarize_by: "none"

  # ==============================================================================
  # 2. DAX LOGIC CONTRACTS & FORMULA PINNING
  # ==============================================================================

  # Ensure critical business measures are never renamed or deleted
  - name: "DAX: Core sales measure must exist"
    type: measure_exists
    measure: "[Total Sales]"

  # Block raw slash division across all ratio and margin metrics
  - name: "DAX: Safe division required for all ratio and margin metrics"
    type: dax_rule
    measures_matching: ["*Margin*", "*Ratio*", "*%*"]
    must_call: "DIVIDE"

  # Strict zero-tolerance check for raw slash on board-level metrics
  - name: "DAX: Forbid raw slash division anti-pattern"
    type: dax_rule
    measure: "[Profit Margin %]"
    forbid_operator: "/"

  # Ensure metric reuse (DRY)
  - name: "DAX: Complex metrics must reference base measures"
    type: dax_rule
    measure: "[Gross Profit]"
    references: ["[Total Sales]", "[Total COGS]"]

  # Freeze financial KPI logic character-for-character
  - name: "Contract: Gross Profit formula definition is pinned"
    type: dax_exact
    measure: "[Gross Profit]"
    expected: "[Total Sales] - [Total COGS]"

  # Enforce calculation lineage and metric inheritance (DAG)
  - name: "DAG: Enforce metric dependency hierarchy for Profit Margin"
    type: dax_dependency_chain
    measure: "[Profit Margin %]"
    must_depend_on:
      - "[Gross Profit]"
      - "[Total Sales]"

  # ==============================================================================
  # 3. INTEGRITY & ANTI-CHEAT (MAGIC NUMBERS & COLUMN SCOPING)
  # ==============================================================================

  # Catch magic numbers, arbitrary multipliers (* 1.042), and scalar adjustments
  - name: "Integrity: No hardcoded scalar adjustments in financial KPIs"
    type: dax_rule
    measures_matching: ["*Sales*", "*Profit*", "*COGS*"]
    forbid_raw_numeric_literals: true
    allowed_literals: [0, 1]

  # Stop metrics from querying raw fact columns directly instead of base measures
  - name: "Governance: Ratio metrics must not query raw fact columns directly"
    type: column_usage_rule
    measures_matching: ["*Margin*", "*Ratio*", "*%*"]
    forbid_column_references:
      - "financials[Sales]"
      - "financials[COGS]"

  # ==============================================================================
  # 4. GOVERNANCE & FORMATTING
  # ==============================================================================

  # Enforce currency strings to prevent raw decimals on cards
  - name: "Gov: Financial measures must have currency format strings"
    type: measure_format
    measures: ["[Total Sales]", "[Total COGS]", "[Gross Profit]"]
    format: "currency"

  # Enforce percentage formatting to avoid 0.24 instead of 24%
  - name: "Gov: Percentage measures must specify percentage formatting"
    type: measure_format
    measures_matching: ["*%*", "*Margin*"]
    format: "percentage"

  # Eliminate obsolete technical prefixes for self-service usability
  - name: "Gov: Enforce clean naming conventions"
    type: measure_naming
    forbid_prefixes: ["m_", "meas_", "calc_"]
```

---

## Assertion Reference

### Storage & Architecture

#### `no_calculated_columns`
* **Why:** Calculated columns evaluate row-by-row and sit in RAM uncompressed. Fact tables with millions of rows quickly exhaust capacity .
* **When:** On all transaction/fact tables (`*Fact*`, `financials`, `Orders`) .
* **Parameters:** `table` (string/wildcard) .

#### `no_auto_date_tables`
* **Why:** Power BI Desktop silently generates a hidden calendar table for every date column, causing massive file bloat .
* **When:** Across all production models; teams should use a dedicated `DimDate` dimension .
* **Parameters:** None .

#### `column_rule`
* **Why:** By default, Power BI attempts to sum numeric fields, displaying `Sum of Year: 6,075` on executive card visuals .
* **When:** On foreign keys, IDs, Postal Codes, and Date attributes (`summarize_by: "none"`) .
* **Parameters:** `table`, `column`, `summarize_by`, `hidden` .

---

### DAX Logic Contracts & Integrity

#### `dax_exact`
* **Why:** Broad linters pass even if calculations are inverted (e.g., `DIVIDE([COGS], [Sales])` passes a loose check). `dax_exact` freezes the formula definition character-for-character, ignoring trivial comment or whitespace drift.
* **When:** On core board-level financial metrics (`[Gross Profit]`, `[EBITDA]`, `[ARR]`).
* **Parameters:** `measure` (string), `expected` (string DAX expression).

#### `dax_dependency_chain`
* **Why:** Enforces measure inheritance via Directed Acyclic Graph (DAG) traversal. Prevents developers or AI agents from skipping intermediate base metrics and querying raw columns directly.
* **When:** On secondary and tertiary metrics (`[Net Margin %]` must depend on `[Net Profit]`, which must depend on `[Total Sales]`).
* **Parameters:** `measure` (string), `must_depend_on` (list of strings), `forbid_dependencies` (list of strings), `direct_only` (bool, default `false`).

#### `column_usage_rule`
* **Why:** Prevents column disambiguation errors (e.g., writing time-intelligence calculations against `FactSales[OrderDate]` instead of `DimDate[Date]`).
* **When:** Guarding time-intelligence measures or ensuring ratio calculations only query certified dimensions.
* **Parameters:** `measures_matching`, `forbid_column_references`, `must_reference_tables`, `allowed_tables`, `exclude_measures`.

#### `dax_rule`
* **Why:** General-purpose DAX validator. Strips comments and string literals to prevent false positives.
* **When:** Enforcing safe functions, blocking `/` division, and catching hardcoded numbers .
* **Parameters:** 
  * `must_call`: Function name(s) required (e.g., `DIVIDE`) .
  * `forbid_operator`: Disallowed operator (e.g., `"/"`) .
  * `references`: Base measure references required (DRY) .
  * `forbid_raw_numeric_literals`: Set `true` to block magic numbers.
  * `allowed_literals`: Whitelist acceptable numbers (e.g., `[0, 1]`).

---

### Governance & Formatting

#### `measure_format`
* **Why:** Prevents raw floating-point numbers from rendering on cards (e.g., displays `$1,000,000` instead of `1000000.32891`, and `24%` instead of `0.24`) .
* **When:** On all customer-facing or executive semantic models .
* **Parameters:** `measures`, `measures_matching`, `format` (`"currency"`, `"percentage"`, or custom mask) .

#### `measure_naming`
* **Why:** Bans legacy Hungarian notation and technical prefixes that confuse self-service business users in the visual field picker .
* **When:** Global model governance .
* **Parameters:** `forbid_prefixes` (list of prefixes to disallow) .

---

## Automated UAT & CYA Sign-Off Reports

Use `--export-report <path>` to export an auditable compliance artifact in **Markdown (`.md`)**, **HTML (`.html`)**, or **JSON (`.json`)** :

```bash
python main.py -c pbi_tests.yml -m ./Sales.SemanticModel --export-report uat_audit.md
```

The generated report includes:
1. **Formal Stakeholder UAT Sign-Off Table:** Prepared for BI Engineers, Domain Owners, and Governance Leads.
2. **Deterministic Assertion Log:** Full pass/fail audit records with diff diagnostics for CI logs.
3. **Semantic Model Inventory:** Summary of all tables, business measures, and calculated columns.
4. **Git Metadata:** Embedded commit SHA and branch name for deployment tracking.

---

## License

MIT License - Copyright (c) 2026 Patens.dev. Free for commercial and private use.