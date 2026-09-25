# pbi-guard

Automated contract testing, architectural linting, and governance for Power BI semantic models (`.pbip` / TMDL).

Run DAX logic checks and tabular modeling assertions locally or inside CI/CD pipelines in milliseconds

---

## Why

* **Silent Regressions:** Modifying a base measure can silently alter downstream KPI calculations across reports.
* **Architectural Anti-Patterns:** Unvetted pull requests frequently introduce expensive calculated columns into multi-million-row fact tables or use unsafe `/` division instead of `DIVIDE()`.
* **Zero Automation:** Power BI validation has historically been manual: opening Desktop, building temporary matrix visuals, and cross-referencing numbers against Excel.

`pbi-guard` operates as a static analysis engine for plain-text TMDL files, allowing teams to test semantic models using familiar Power BI syntax (bracketed measure names, DAX function calls, and wildcard filters).

---

## Installation

Requires Python 3.9+.

```bash
git clone https://github.com/your-repo/pbi-guard.git
cd pbi-guard
pip install pyyaml
```

---

## Quickstart

### 1. Define your assertions (`pbi_tests.yml`)

Create a `pbi_tests.yml` configuration file in your project directory:

```yaml
version: 1

assertions:
  # Block expensive calculated columns in fact tables
  - name: "Arch: No calculated columns in financials table"
    type: no_calculated_columns
    table: "financials"

  # Prevent hidden date table bloat
  - name: "Arch: Block auto date/time hidden tables"
    type: no_auto_date_tables

  # Stop numeric keys/dates from auto-summing
  - name: "Arch: Prevent default aggregation on Year column"
    type: column_rule
    table: "financials"
    column: "Year"
    summarize_by: "none"

  # Ensure core business measures are never deleted or renamed
  - name: "DAX: Core sales measure must exist"
    type: measure_exists
    measure: "[Total Sales]"

  # Enforce safe division best practices
  - name: "DAX: Safe division required for all ratio and margin metrics"
    type: dax_rule
    measures_matching: ["*Margin*", "*Ratio*", "*%*"]
    must_call: "DIVIDE"

  # Block raw slash division anti-patterns
  - name: "DAX: Forbid raw slash division"
    type: dax_rule
    measure: "[Profit Margin %]"
    forbid_operator: "/"

  # Ensure measure references base calculations (DRY)
  - name: "DAX: Gross Profit references base measures"
    type: dax_rule
    measure: "[Gross Profit]"
    references: ["[Total Sales]", "[Total COGS]"]

  # Enforce UI formatting
  - name: "Gov: Financial measures must have currency format strings"
    type: measure_format
    measures: ["[Total Sales]", "[Total COGS]", "[Gross Profit]"]
    format: "currency"

  - name: "Gov: Percentage measures must specify percentage formatting"
    type: measure_format
    measures_matching: ["*%*", "*Margin*"]
    format: "percentage"

  - name: "Gov: Enforce clean naming conventions (no technical prefixes)"
    type: measure_naming
    forbid_prefixes: ["m_", "meas_", "calc_"]
```

### 2. Run the test suite

Point the CLI to your `.pbip` file, `.SemanticModel` directory, or TMDL `definition/` folder:

```bash
python main.py -c pbi_tests.yml -m "../path/to/Report.pbip"
```

### 3. Output

```text
Model Summary: 1 table(s), 4 measure(s) parsed.

[PASS] Arch: No calculated columns in financials table
[PASS] Arch: Block auto date/time hidden tables
[PASS] Arch: Prevent default aggregation on Year column
[PASS] DAX: Core sales measure must exist
[PASS] DAX: Safe division required for all ratio and margin metrics
[PASS] DAX: Forbid raw slash division
[PASS] DAX: Gross Profit references base measures
[PASS] Gov: Financial measures must have currency format strings
[PASS] Gov: Percentage measures must specify percentage formatting
[PASS] Gov: Enforce clean naming conventions (no technical prefixes)

Summary: 10/10 passed, 0 failed.
```

When a test fails, `pbi-guard` pinpoints the offending measure, prints the actual DAX code, and exits with code `1`, halting your CI build before bad models merge.

---

## Anatomy of an Assertion

Every test in `pbi_tests.yml` is composed of four main components:

* **`name` (string, required):** The descriptive name displayed in your terminal and CI logs. Use this to explain the rule, the team SLA, or the intent (e.g., `"Arch: No calculated columns in Fact"`, `"DAX: Gross Margin references [Total Sales]"`).
* **`type` (string, required):** The assertion type to evaluate (e.g., `dax_rule`, `measure_format`, `column_rule`).
* **Targeting Properties:** Defines what to inspect using exact names or simple wildcards (`*`):
  * `measure: "[Total Sales]"` or `measures: ["[Total Sales]", "[Total COGS]"]`
  * `measures_matching: ["*Margin*", "*Pct*"]`
  * `table: "financials"` or `tables: "*Fact*"`
  * `column: "Year"`
* **Validation Properties:** Declares the rules to enforce (`must_call`, `forbid_operator`, `references`, `format`, `summarize_by`, `hidden`, `forbid_prefixes`).

---

## Test Categories & Assertion Reference

### 1. Storage & Architectural Hygiene

Keeps uncompressed memory down and enforces star-schema best practices.

#### `no_calculated_columns`
* **Why use it:** Calculated columns compute a value for every single row and store uncompressed data in RAM. In tables with millions of rows, they bloat file size and exhaust memory.
* **Used for:** Enforcing that row-level transformations stay upstream in SQL, dbt, or lakehouses.
* **Parameters:**
  * `table` (string): Exact table name or wildcard pattern (`"FactSales"`, `"*Fact*"`).
* **Example:**
```yaml
- name: "Arch: No calculated columns in Fact tables"
  type: no_calculated_columns
  table: "*Fact*"
```

#### `no_auto_date_tables`
* **Why use it:** Power BI Desktop creates hidden local date hierarchy tables for every date/time column by default, unnecessarily inflating file size and metadata.
* **Used for:** Verifying that Auto Date/Time has been unchecked in file options across all team members.
* **Parameters:** None.
* **Example:**
```yaml
- name: "Arch: Block auto date/time hidden tables"
  type: no_auto_date_tables
```

#### `column_rule`
* **Why use it:** Prevents visual clutter and accidental aggregation on numeric IDs, surrogate keys, or year columns.
* **Used for:** Setting `summarize_by: none` and hiding foreign key columns (`hidden: true`).
* **Parameters:**
  * `table` (string): Target table name or wildcard.
  * `column` (string): Target column name or wildcard.
  * `summarize_by` (string, optional): Expected default summarization (`none`, `sum`, etc.).
  * `hidden` (boolean, optional): Whether the column must be hidden (`true` or `false`).
* **Example:**
```yaml
- name: "Arch: Prevent default aggregation on Year column"
  type: column_rule
  table: "financials"
  column: "Year"
  summarize_by: "none"
```

---

### 2. DAX Logic Contracts & Best Practices

Catches calculation errors, unsafe math, and architectural code smells before pull requests merge.

#### `measure_exists`
* **Why use it:** Refactoring or accidentally deleting a base measure breaks downstream reports and visual calculations.
* **Used for:** Guaranteeing that critical business KPIs (`[Total Sales]`, `[Net Revenue]`) remain defined in the model.
* **Parameters:**
  * `measure` (string): Exact measure name, with or without brackets (`"[Total Sales]"` or `"Total Sales"`).
* **Example:**
```yaml
- name: "DAX: Core sales measure must exist"
  type: measure_exists
  measure: "[Total Sales]"
```

#### `dax_rule`
* **Why use it:** Enforces formula contracts, mandates safe division, blocks unsafe math, and ensures complex KPIs reuse vetted base measures (DRY principle). Comments (`//`, `--`, `/* */`) and string literals are automatically stripped so they never trigger false positives.
* **Used for:**
  * `must_call`: Mandating functions like `DIVIDE` or `KEEPFILTERS`.
  * `forbid_operator`: Banning raw `/` division.
  * `references`: Ensuring a metric references base measures rather than rewriting raw `SUM()` logic.
* **Parameters:**
  * `measure` (string, optional): Specific measure name.
  * `measures_matching` (list of strings, optional): Wildcard list of measures to validate.
  * `must_call` (string or list of strings, optional): Function name(s) that must be invoked.
  * `forbid_operator` (string, optional): Disallowed operator (e.g., `"/"`).
  * `references` (list of strings, optional): Measure references that must be present in the DAX expression.
* **Example:**
```yaml
- name: "DAX: Safe division and measure reuse"
  type: dax_rule
  measure: "[Profit Margin %]"
  must_call: "DIVIDE"
  forbid_operator: "/"
  references: ["[Total Sales]", "[Total COGS]"]
```

---

### 3. Governance, Formatting & Naming

Keeps models clean, intuitive, and professional for self-service business users.

#### `measure_format`
* **Why use it:** Unformatted numbers display as raw decimals on cards and tables.
* **Used for:** Enforcing that financial metrics specify currency formatting and ratios use percentage format strings.
* **Parameters:**
  * `measure` or `measures` (string or list): Specific measure name(s).
  * `measures_matching` (list of strings): Wildcard list of measures to validate.
  * `format` (string): Predefined preset (`"currency"`, `"percentage"`) or custom string.
* **Example:**
```yaml
- name: "Gov: Percentage measures must specify percentage formatting"
  type: measure_format
  measures_matching: ["*%*", "*Margin*", "*Ratio*"]
  format: "percentage"
```

#### `measure_naming`
* **Why use it:** Inconsistent naming and technical prefixes (e.g., `m_Sales`, `calc_Profit`) clutter visual builders and confuse business users.
* **Used for:** Banning obsolete prefix conventions across all measure names.
* **Parameters:**
  * `forbid_prefixes` (list of strings): List of prefixes that measures must not start with.
* **Example:**
```yaml
- name: "Gov: Enforce clean naming conventions"
  type: measure_naming
  forbid_prefixes: ["m_", "meas_", "calc_"]
```

---

## Supported Assertion Reference

| Assertion Type | Scope | Target Selectors | Validation Parameters | Primary Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `no_calculated_columns` | Tables | `table` | — | Blocks calculated columns in memory-intensive tables. |
| `no_auto_date_tables` | Model | — | — | Flags hidden local date table bloat. |
| `column_rule` | Columns | `table`, `column` | `summarize_by`, `hidden` | Enforces column properties (aggregation, visibility). |
| `measure_exists` | Measures | `measure` | — | Guarantees required enterprise measures are present. |
| `dax_rule` | DAX | `measure`, `measures_matching` | `must_call`, `forbid_operator`, `references` | Enforces safe DAX functions and metric dependencies. |
| `measure_format` | Measures | `measures`, `measures_matching` | `format` (`currency`, `percentage`) | Enforces standard number and percentage formatting. |
| `measure_naming` | Measures | — | `forbid_prefixes` | Disallows technical prefixes across measure names. |

---
