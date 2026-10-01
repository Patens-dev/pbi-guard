# PBI Guard

**The automated business logic firewall and governance studio for Power BI (`.pbip` / TMDL).**

Catch formula drift, unauthorized multipliers, and broken metrics in real time. Runs silent, deterministic AST checks offline on every `Ctrl+S` in under 50ms, with native Power BI External Tools ribbon integration and an interactive Low-Code / Raw YAML Test Studio

---

## Key Capabilities

* **Silent Real-Time `Ctrl+S` Watcher:** Monitors active Power BI Developer Mode (`.pbip`) TMDL definitions in the background. Evaluates formulas on save with zero terminal window flashes or command popups.
* **Split Test Studio (Low-Code Form <-> Raw YAML):** Build governance rules visually with pickers from active semantic model measures, or write raw YAML in a synchronized side-by-side editor.
* **1-Click Recipe Presets:** Pre-built governance assertions for safe `DIVIDE()`, banning arbitrary scalar adjustments/magic numbers, currency and percentage masks, and clean naming conventions.
* **Automated Baseline Pinning (`freeze`):** Snapshot existing models character-for-character into an immutable contract baseline (`pbi-guard.lock.yml`) with one click.
* **Power BI External Tools Integration:** Launches directly from the Power BI Desktop ribbon tab via a windowless native manifest.
* **100% Offline Static Analysis:** Pure AST parsing without launching Analysis Services or requiring cloud capacity, credentials, or network telemetry.

---

## Installation

### Method 1: Portable Release (Recommended for Business Analysts)
*No administrator rights or compilation required. Works on locked-down corporate laptops.*

1. Download **`pbi-guard-windows.zip`** from [Releases](https://github.com/flaviu01/pbi-guard/releases).
2. Extract the archive and double-click **`install.cmd`**.
   * It installs `pyyaml`, copies the app to `%LOCALAPPDATA%\pbi-guard`, and registers the External Tool ribbon button.
3. Restart **Power BI Desktop**. PBI Guard will appear in the **External Tools** ribbon tab .
4. *(Optional)* You can safely delete the downloaded `.zip` and extracted folder.

To remove PBI Guard later, double-click **`uninstall.cmd`** .

---

### Method 2: Git & Developer Setup

Requires Python 3.10+ (available from python.org or the Microsoft Store with no admin rights):

```bash
# Clone the repository
git clone [https://github.com/flaviu01/pbi-guard.git](https://github.com/flaviu01/pbi-guard.git)
cd pbi-guard

# Install required dependencies
pip install pyyaml

# Register the ribbon button in Power BI Desktop
python main.py pbitool
```

---

## How It Works

```text
Power BI Desktop (Ctrl+S)
        │  (Writes .tmdl files to disk)
        ▼
PBI Guard Silent Watcher (<50ms AST Parse)
        │
        ├── Checks pbi-guard.lock.yml (Baseline contracts)
        └── Checks pbi_tests.yml      (Governance rules)
        │
   ┌────┴─────────────────────────────┐
   ▼                                  ▼
[All Passed]                  [Contract Breach]
Toast: "All contracts intact"   Toast: "PR Blocked: [Gross Margin %] diverged"
Web Dashboard: Green          Web Dashboard: Shows diff & offending line
```

---

## CLI Usage

PBI Guard auto-detects running Power BI models, cached `.pbip` workspaces, or local directories :

```bash
# Launch the real-time Web Dashboard & Test Studio (runs watcher)
python main.py web

# Manually verify model against lockfile and test assertions
python main.py check

# Freeze active model measures into a certified baseline lockfile (pbi-guard.lock.yml)
python main.py freeze

# Register / unregister External Tools ribbon button
python main.py pbitool
python main.py pbitool --uninstall
```

---

## Split Studio (Low-Code & Raw YAML)

When running `python main.py web` (or opening via Power BI's External Tools ribbon) , navigate to the **Governance & Test Studio** tab at `[http://127.0.0.1:8765](http://127.0.0.1:8765)` :

* **Low-Code Studio (Left):**
  * **Target Scope:** Target all measures (`*`), wildcard patterns (`*Margin*, *Ratio*`), or pick directly from auto-discovered model measures .
  * **Rule Types:** Configure DAX function policies (`dax_rule`), format masks (`measure_format`), and naming conventions (`measure_naming`) .
  * **Interactive Actions:** Edit, duplicate, or delete rules visually .
* **Raw YAML Editor (Right):**
  * Live two-way synchronization: modifications in the visual builder update the YAML; edits in the YAML update the visual cards .
  * Real-time YAML syntax validator prevents saving corrupted files .
  * **Ctrl+S / Cmd+S:** Hotkey to immediately save `pbi_tests.yml` and trigger re-verification across all model measures .

---

## Rule Configuration Reference (`pbi_tests.yml`)

```yaml
version: 1

assertions:
  # ==============================================================================
  # 1. DAX LOGIC CONTRACTS & POLICY RULES
  # ==============================================================================

  # Enforce safe division on all ratio and percentage measures
  - name: "DAX: Safe division required for margin and ratio metrics"
    type: dax_rule
    measures_matching: ["*Margin*", "*Ratio*", "*%*"]
    must_call: "DIVIDE"

  # Block unapproved fudge factors, arbitrary multipliers, or manual plugs
  - name: "Integrity: No hardcoded numeric scalar adjustments in financial KPIs"
    type: dax_rule
    measures_matching: ["*Sales*", "*Profit*", "*COGS*"]
    forbid_raw_numeric_literals: true
    allowed_literals: [0, 1]

  # Forbid costly or anti-pattern functions in base measures
  - name: "Performance: Forbid CALCULATE inside simple sum aggregations"
    type: dax_rule
    measures_matching: ["*Raw*", "*Base*"]
    forbid_calls: ["CALCULATE"]

  # Pin critical business formula character-for-character
  - name: "Contract: Gross Profit formula logic is pinned"
    type: dax_exact
    measure: "[Gross Profit]"
    expected: "[Total Sales] - [Total COGS]"

  # Enforce metric dependency hierarchy (DAG)
  - name: "Lineage: Net Margin must inherit from certified Gross Profit"
    type: dax_dependency_chain
    measure: "[Net Margin %]"
    must_depend_on:
      - "[Gross Profit]"
      - "[Total Sales]"

  # Block measures from referencing raw fact columns directly (enforce DRY base measures)
  - name: "Governance: Ratio metrics must not query raw fact columns directly"
    type: column_usage_rule
    measures_matching: ["*Margin*", "*Ratio*"]
    forbid_column_references:
      - "financials[Sales]"
      - "financials[COGS]"

  # ==============================================================================
  # 2. STORAGE & TABULAR ARCHITECTURE
  # ==============================================================================

  # Keep RAM uncompressed bloat down
  - name: "Arch: No calculated columns in transaction tables"
    type: no_calculated_columns
    table: "financials"

  # Prevent silent file size explosion from auto-generated calendars
  - name: "Arch: Block hidden auto date/time tables"
    type: no_auto_date_tables

  # Ensure upstream warehouse columns were not renamed or dropped during ETL
  - name: "Arch: Essential fact columns exist in schema"
    type: column_exists
    table: "financials"
    columns: ["Sales", "Profit", "COGS", "Date"]

  # Prevent numeric IDs, postal codes, or years from auto-summing in visuals
  - name: "Arch: Prevent default aggregation on Year attribute"
    type: column_rule
    table: "financials"
    column: "Year"
    summarize_by: "none"
    data_type: "int64"

  # ==============================================================================
  # 3. GOVERNANCE & PRESENTATION FORMATTING
  # ==============================================================================

  # Enforce standardized currency format strings on monetary cards
  - name: "Gov: Financial KPIs must specify explicit currency formatting"
    type: measure_format
    measures_matching: ["*Sales*", "*Revenue*", "*Profit*"]
    format: "$#,##0;($#,##0);-"

  # Enforce percentage format
  - name: "Gov: Margin metrics must specify percentage format"
    type: measure_format
    measures_matching: ["*%*", "*Margin*"]
    format: "0.0%"

  # Eliminate obsolete technical prefixes for clean self-service visual building
  - name: "Gov: Enforce clean measure naming conventions"
    type: measure_naming
    forbid_prefixes: ["m_", "meas_", "calc_"]
```

---

## Assertion Types Quick Reference

| Assertion Type | Purpose | Key Parameters |
| :--- | :--- | :--- |
| `dax_rule` | Enforces required or forbidden functions, operators, and bans magic numbers | `must_call`, `forbid_calls`, `forbid_raw_numeric_literals`, `allowed_literals` |
| `dax_exact` | Character-for-character formula lock, ignoring whitespace drift | `measure`, `expected` |
| `dax_dependency_chain` | Enforces calculation inheritance through DAG traversal | `measure`, `must_depend_on`, `forbid_dependencies` |
| `column_usage_rule` | Prevents measures from querying raw fact columns directly | `measures_matching`, `forbid_column_references` |
| `no_calculated_columns`| Blocks uncompressed in-memory calculated columns | `table` |
| `no_auto_date_tables`  | Blocks hidden auto date-time hierarchy tables | None |
| `column_exists`        | Verifies warehouse ETL schema presence | `table`, `columns` |
| `column_rule`          | Enforces `summarize_by: none` and data types on attributes | `table`, `column`, `summarize_by`, `data_type` |
| `measure_format`       | Enforces currency or percentage format strings on cards | `measures_matching`, `format` |
| `measure_naming`       | Bans developer prefixes (`m_`, `calc_`) on business measures | `forbid_prefixes`, `suffix` |

---

## Editions

* **Community (Open Source):** Free forever under the Apache 2.0 license. Includes unlimited local model inspection, the real-time background watcher, the Low-Code / YAML Studio, and External Tools ribbon integration .
* **Enterprise:** Tailored for teams operating Git pipelines. Adds Azure DevOps Pipeline tasks, GitHub Actions PR merge blockers, automated pull request inline formula diff bots, and centralized organization-wide rule sync.

---

## License

Apache License 2.0. Free for personal, commercial, and enterprise use.