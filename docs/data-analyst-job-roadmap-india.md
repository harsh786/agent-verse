# Data Analyst Roadmap: Fresher to Job-Ready (India)

> **Outcome:** Build the technical ability, business judgment, portfolio, and interview fluency required for an entry-level Data Analyst, Business Analyst, BI Analyst, MIS Analyst, or Product Analyst role in India.
>
> **How to use this guide:** Follow the phases in order. Do not move to the next phase until you meet its competency gate. This is deliberately a capability roadmap, not a daily-hours schedule.

## 1. The Target Role

A good data analyst turns an ambiguous business question into a trustworthy recommendation. The job is not only creating charts or writing code. The complete workflow is:

```text
Business question -> metric definition -> data extraction -> cleaning -> analysis
-> visualisation -> insight -> recommendation -> measurable business action
```

For fresher roles in India, employers commonly assess these capabilities:

| Area | What job-ready looks like |
|---|---|
| SQL | You can query multiple tables, join safely, aggregate correctly, use CTEs and window functions, and explain the result. |
| Excel | You can clean data, use formulas and lookups, create pivot-based reports, and build a readable dashboard. |
| Power BI | You can transform data, model relationships, write practical DAX measures, and build an interactive dashboard. |
| Python | You can use pandas in a notebook to clean, explore, merge, and chart tabular data. |
| Statistics | You can interpret distributions, correlation, sampling, confidence intervals, and A/B-test results without overclaiming. |
| Business sense | You choose useful KPIs, connect findings to revenue, cost, retention, or operations, and make a specific recommendation. |
| Communication | You can give a concise walkthrough of your analysis, assumptions, limitations, and impact. |
| Portfolio | You have public, reproducible, end-to-end projects with clear README files and dashboards. |

### Roles to target

Apply to roles that use similar foundations, even when the title differs:

- Data Analyst / Junior Data Analyst
- Business Analyst / Associate Business Analyst
- BI Analyst / Reporting Analyst
- MIS Executive / MIS Analyst
- Operations Analyst
- Marketing Analyst / Growth Analyst
- Product Analyst Intern / Associate Product Analyst
- Revenue Analyst / Sales Operations Analyst
- Risk Analyst / Data Operations Analyst

### Scope discipline

Become strong at analyst work before moving into data science or data engineering. During this roadmap, do **not** spend significant time on deep learning, advanced machine learning, Spark, Kubernetes, or complex cloud architecture. They are valuable later, but they do not replace SQL, dashboards, business metrics, and communication in an entry-level analyst interview.

## 2. Recommended Tool Stack

Install and use the following stack. It is sufficient for a strong fresher portfolio.

| Category | Primary tool | Why it matters | Minimum standard |
|---|---|---|---|
| Spreadsheet | Microsoft Excel | Still central to reporting and ad hoc analysis | Cleaning, formulas, PivotTables, charts, dashboard |
| Querying | PostgreSQL + DBeaver | Teaches portable, production-style SQL | Write, save, and validate complex SQL queries |
| BI | Power BI Desktop | Widely requested in Indian analyst and MIS roles | Power Query, star schema, DAX, dashboard UX |
| Secondary BI | Tableau Public | Useful for portfolio visibility and role flexibility | Publish one polished public dashboard |
| Programming | Python + JupyterLab or VS Code | Repeatable cleaning and exploratory analysis | pandas, NumPy basics, matplotlib/seaborn |
| Version control | Git + GitHub | Makes work reviewable and demonstrates professional practice | Repository, commits, README, `.gitignore` |
| Data source | Kaggle, data.gov.in, World Bank, public APIs | Provides realistic data for projects | Cite source, license, and limitations |
| Documentation | Markdown | Lets recruiters understand your work quickly | Business context, method, findings, recommendation |

### Required setup

1. Create a GitHub account with a professional username.
2. Install Git and configure your name and email.
3. Install PostgreSQL and DBeaver, then create a local database named `analytics_practice`.
4. Install Power BI Desktop.
5. Create a Tableau Public account.
6. Install Python 3, VS Code, and the Python extension, or install Anaconda if you prefer a bundled beginner setup.
7. Create a GitHub repository named `data-analyst-portfolio` and an organised local folder structure.

Suggested portfolio layout:

```text
data-analyst-portfolio/
  README.md
  projects/
    01-retail-sales-performance/
      README.md
      sql/
      notebooks/
      data/
      dashboard/
      images/
    02-customer-retention/
    03-marketing-campaign/
  sql-practice/
  excel-practice/
  power-bi-practice/
  resume/
```

Never upload sensitive data, passwords, access tokens, or proprietary employer data to GitHub.

## 3. Roadmap Overview

This sequence is designed for a two-month intensive sprint without prescribing daily study hours.

| Phase | Focus | Primary outputs |
|---|---|---|
| 0 | Setup and analytical mindset | Tool setup, portfolio structure, KPI vocabulary |
| 1 | Excel and data quality | Clean workbook and spreadsheet dashboard |
| 2 | SQL foundations to advanced analysis | Query portfolio and SQL case study |
| 3 | Power BI and Tableau | Interactive Power BI report and Tableau Public dashboard |
| 4 | Python and pandas | Reproducible exploratory analysis notebook |
| 5 | Statistics and product/business analytics | A/B-test interpretation and metric framework |
| 6 | Capstone portfolio projects | Five polished case studies |
| 7 | Job search and interviews | Resume, LinkedIn, mock interviews, application system |

## 4. Phase 0: Think Like an Analyst

Before learning tools, learn to frame questions correctly.

### The analytical brief

For every analysis, write these seven items before touching the data:

1. **Decision:** What decision should this analysis help someone make?
2. **Stakeholder:** Who will use the answer?
3. **Question:** State one precise business question.
4. **Metric:** Define the primary metric and its calculation.
5. **Grain:** What does one row represent: customer, order, session, product, or day?
6. **Time period:** Which dates are included and why?
7. **Limitations:** What cannot be concluded from this data?

Example:

> The sales manager needs to decide which product categories to promote next quarter. Analyse completed orders from Q1-Q4, measure revenue as `SUM(quantity * unit_price * (1 - discount))`, compare year-over-year category growth, and flag categories where revenue growth may be driven by a small number of customers.

### Essential business metrics

Know the meaning, formula, and suitable use of each metric.

| Metric | Formula / definition | Typical use |
|---|---|---|
| Revenue | Sum of recognised sales value | Commercial performance |
| Gross profit | Revenue minus cost of goods sold | Profitability |
| Gross margin | Gross profit / revenue | Product or category economics |
| Average order value (AOV) | Revenue / orders | Basket and pricing analysis |
| Conversion rate | Conversions / eligible visitors or leads | Funnel performance |
| Retention rate | Customers active in later period / starting cohort | Customer health |
| Churn rate | Customers lost / customers at start | Attrition monitoring |
| Customer lifetime value (simple) | AOV x purchase frequency x customer lifespan | Customer economics |
| Customer acquisition cost | Marketing and sales spend / acquired customers | Acquisition efficiency |
| Return/refund rate | Returned orders / fulfilled orders | Quality and customer experience |
| On-time delivery rate | On-time deliveries / completed deliveries | Operations performance |

### Competency gate

You can receive an ambiguous prompt such as “sales are declining” and write a one-page analysis brief that defines the stakeholder, business question, metric, grain, scope, hypotheses, and risks.

## 5. Phase 1: Excel and Data Quality

Excel is not a beginner-only skill. Many analyst workflows begin or end in a spreadsheet, and hiring managers often test it directly.

### Learn in this order

#### 5.1 Data hygiene

- Understand rows, columns, tables, data types, filters, freeze panes, sorting, and named ranges.
- Find blank values, duplicate records, invalid dates, inconsistent category labels, and implausible numeric values.
- Use `TRIM`, `CLEAN`, `LOWER`, `UPPER`, `PROPER`, `SUBSTITUTE`, `TEXT`, `VALUE`, and date conversions.
- Preserve raw data. Perform transformations in separate columns or a separate cleaned-data sheet.

#### 5.2 Formulas analysts use

| Skill | Functions to practice |
|---|---|
| Conditional logic | `IF`, `IFS`, `AND`, `OR`, `IFERROR` |
| Aggregation | `SUM`, `AVERAGE`, `MEDIAN`, `MIN`, `MAX`, `COUNT`, `COUNTA` |
| Conditional aggregation | `SUMIFS`, `COUNTIFS`, `AVERAGEIFS` |
| Lookups | `XLOOKUP`, `INDEX` + `MATCH` |
| Dates | `DATE`, `YEAR`, `MONTH`, `EOMONTH`, `DATEDIF`, `NETWORKDAYS` |
| Text | `LEFT`, `RIGHT`, `MID`, `FIND`, `SEARCH`, `TEXTSPLIT` |
| Dynamic arrays | `FILTER`, `SORT`, `UNIQUE` |

#### 5.3 Analysis and reporting

- Create PivotTables and PivotCharts.
- Group dates by month, quarter, and year.
- Build calculated fields only when their definitions are documented.
- Use slicers and timelines thoughtfully.
- Choose charts that answer a question: lines for trends, bars for comparisons, scatter plots for relationships, and tables for exact values.
- Avoid pie charts with many categories, 3D charts, decorative colours, and overloaded dashboards.

### Excel portfolio deliverable

Build a **Retail Sales Performance Dashboard** from a transaction dataset.

Required sheets:

1. `Raw_Data`: unchanged source data.
2. `Cleaned_Data`: documented transformations and helper columns.
3. `Analysis`: PivotTables for monthly trend, category performance, region performance, and top products.
4. `Dashboard`: KPI cards, four useful charts, filters, and a written insight box.
5. `Data_Dictionary`: field meanings, calculations, and source.

### Excel competency gate

Given a messy sales file, you can clean it, calculate revenue, create a PivotTable report, identify the strongest and weakest segments, and explain three decisions the business should consider.

## 6. Phase 2: SQL, Your Highest-Value Skill

SQL is the most consistently tested technical skill for data analyst roles. Learn it as a way to reason about data, not as a list of commands.

### Core concepts

#### 6.1 Relational thinking

- Table, row, column, schema, primary key, foreign key, and relationship.
- Fact table: records measurable events, such as an order or transaction.
- Dimension table: descriptive attributes, such as customer, date, product, or region.
- Grain: one row’s real-world meaning. State it before joining or aggregating.
- Cardinality: one-to-one, one-to-many, and many-to-many relationships.

#### 6.2 Query foundation

Practice until automatic:

```sql
SELECT column_name
FROM table_name
WHERE condition
GROUP BY column_name
HAVING aggregate_condition
ORDER BY column_name
LIMIT 10;
```

Learn:

- `SELECT`, aliases, `DISTINCT`, `WHERE`, `ORDER BY`, `LIMIT`.
- `IN`, `BETWEEN`, `LIKE`, `ILIKE`, `IS NULL`, and `CASE WHEN`.
- Aggregates: `COUNT`, `COUNT(DISTINCT ...)`, `SUM`, `AVG`, `MIN`, `MAX`.
- `GROUP BY` and `HAVING`.
- Date extraction, arithmetic, truncation, and intervals.

#### 6.3 Joins and safe aggregation

Know `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL OUTER JOIN`, `CROSS JOIN`, and self-joins. Prefer `LEFT JOIN` when you must retain every record from a base population.

The common analyst mistake is an accidental many-to-many join that duplicates values. Before and after every join, check the expected row count and check whether a primary key remains unique.

```sql
-- Validate whether the order identifier is still unique after a join.
SELECT
  COUNT(*) AS rows_after_join,
  COUNT(DISTINCT order_id) AS distinct_orders
FROM joined_data;
```

#### 6.4 Intermediate and advanced SQL

- Subqueries and correlated subqueries.
- Common table expressions (CTEs) with `WITH`.
- `UNION ALL` versus `UNION`.
- Window functions: `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `LAG`, `LEAD`, `SUM(...) OVER`, and `AVG(...) OVER`.
- Window frames for running totals and moving averages.
- Cohort analysis, retention, month-over-month growth, top-N per category, and customer segmentation.
- Query readability: meaningful aliases, one CTE per logical transformation, and comments only where intent is non-obvious.

### SQL problem set

Solve each category using an open e-commerce dataset, such as Olist Brazilian E-Commerce or a retail transactions dataset.

1. Revenue, order count, and AOV by month.
2. Top five products by revenue, separately for every category.
3. Customers who purchased more than once and their repeat-purchase interval.
4. Monthly revenue with previous-month revenue and percentage growth.
5. First-purchase customer cohorts and monthly retention.
6. Orders delivered later than the estimated date, grouped by state and seller.
7. Products with rising revenue but falling margin, if cost data exists.
8. Customer segments based on recency, frequency, and monetary value (RFM).

### SQL quality checklist

Before considering a query complete, verify:

- Does every selected non-aggregated field appear in `GROUP BY`?
- Did a join multiply rows?
- Does `COUNT(*)` mean rows, or should it be `COUNT(DISTINCT order_id)`?
- Are `NULL` values handled intentionally?
- Is the date range explicit and inclusive/exclusive as intended?
- Do totals reconcile with a simpler control query?
- Can another analyst understand the query structure in one reading?

### SQL competency gate

You can solve a multi-table business problem using joins, CTEs, date logic, and at least one window function; validate the output; and explain the query in plain English.

## 7. Phase 3: Power BI and Tableau

Use **Power BI as the main BI tool**. It is highly visible in Indian analyst, reporting, MIS, and BI job descriptions. Learn Tableau to demonstrate tool flexibility and publish a public dashboard.

### 7.1 Power BI: data preparation

In Power Query, learn to:

- Connect to CSV, Excel, folder, SQL, and web data sources.
- Profile column quality, distribution, and errors.
- Set data types deliberately.
- Filter, replace values, split and merge columns, fill values, and remove duplicates.
- Pivot and unpivot data.
- Append tables and merge queries.
- Parameterise a file path when appropriate.
- Name each step clearly and avoid unnecessary manual transformations in the report view.

### 7.2 Power BI: data modelling

Build a star schema:

```text
DimDate     DimCustomer     DimProduct     DimRegion
     \             |             |            /
                      FactSales
```

- Use one-to-many relationships from dimensions to facts.
- Create a dedicated date table and mark it as a date table.
- Prefer single-direction filtering unless a documented need requires otherwise.
- Hide technical key columns from report consumers.
- Avoid a single giant flat table when you can model the data properly.

### 7.3 Power BI: DAX measures

Understand the difference between calculated columns and measures:

- A calculated column is computed row by row and stored in the model.
- A measure is calculated at query time based on filter context; use it for KPIs.

Create and explain measures such as:

```DAX
Total Revenue = SUMX(FactSales, FactSales[Quantity] * FactSales[Unit Price])

Total Orders = DISTINCTCOUNT(FactSales[Order ID])

Average Order Value = DIVIDE([Total Revenue], [Total Orders])

Revenue Previous Month =
CALCULATE([Total Revenue], DATEADD(DimDate[Date], -1, MONTH))

Revenue MoM % =
DIVIDE([Total Revenue] - [Revenue Previous Month], [Revenue Previous Month])
```

Then learn `CALCULATE`, filter context, row context, `FILTER`, `ALL`, `REMOVEFILTERS`, `RELATED`, `DIVIDE`, and basic time intelligence. Do not memorise DAX; validate every measure against a small manual calculation.

### 7.4 Dashboard design principles

Every dashboard should answer a business decision quickly:

- Place the purpose, date coverage, and KPI definitions where users can see them.
- Use a clear hierarchy: headline KPIs, trend, drivers, then detail.
- Use one visual per question.
- Label units, currencies, percentages, and comparisons.
- Use accessible contrast and do not depend only on colour to communicate meaning.
- Reserve red and green for negative/positive signals and use them consistently.
- Include tooltips, drill-through, or a detail table only when they add a real analysis path.
- Finish with a short “Key findings and actions” section, not only charts.

### 7.5 Tableau essentials

Learn dimensions versus measures, marks, filters, calculated fields, table calculations, parameters, dashboards, and actions. Build one Tableau Public dashboard that tells a coherent story. Do not create a near-identical copy of your Power BI report: use a different business question or dataset.

### BI competency gate

You can receive raw multi-table data, transform it, build a star schema, create validated measures, deliver an interactive report, and explain the top three business actions it supports.

## 8. Phase 4: Python for Analysts

Python makes analysis reproducible and scalable beyond spreadsheets. For an analyst role, master tabular data workflows before algorithms.

### The Python environment

- Use virtual environments when working locally.
- Work in Jupyter notebooks for exploratory analysis and in `.py` files for reusable scripts.
- Use a clear notebook structure: title, business question, imports, data loading, cleaning, analysis, visualisation, findings, limitations, next steps.
- Restart and run all cells before publishing. Hidden dependencies from out-of-order cells are not reproducible.

### Libraries to know

| Library | Use |
|---|---|
| `pandas` | Tables, cleaning, joins, grouping, dates, exports |
| `numpy` | Numeric arrays and vectorised calculations |
| `matplotlib` | Foundational chart control |
| `seaborn` | Statistical charts and concise exploratory visuals |
| `openpyxl` | Reading or writing Excel workbooks when required |
| `sqlalchemy` or a database driver | Connecting Python to SQL databases later |

### pandas skills

```python
import pandas as pd

sales = pd.read_csv("data/raw/sales.csv", parse_dates=["order_date"])

# Inspect before changing anything.
sales.info()
sales.isna().sum()

# Create a documented derived metric.
sales["revenue"] = sales["quantity"] * sales["unit_price"] * (1 - sales["discount"])

monthly_revenue = (
    sales.groupby(sales["order_date"].dt.to_period("M"), as_index=False)["revenue"]
    .sum()
    .rename(columns={"order_date": "month"})
)
```

Practice:

- Loading CSV, Excel, JSON, and SQL query results.
- Inspecting with `head`, `shape`, `info`, `describe`, `nunique`, `value_counts`, and missing-value summaries.
- Selecting with `loc`, boolean filters, `query`, and `isin`.
- Creating columns with vectorised expressions and `assign`.
- Cleaning with `astype`, `to_datetime`, `str` methods, `replace`, `fillna`, `dropna`, and `drop_duplicates`.
- Combining data with `merge`, `concat`, and validation of merge keys.
- Aggregating with `groupby`, `agg`, `pivot_table`, and `transform`.
- Reshaping with `melt`, `pivot`, and `stack`/`unstack` when needed.
- Writing clean outputs to CSV or Excel.

Avoid slow row-by-row `apply` when a vectorised pandas operation can express the same logic.

### Python visualisation

Create clear, labelled charts that answer a question:

- Line charts: time trends.
- Bar charts: category comparison.
- Histograms and box plots: distributions and outliers.
- Scatter plots: relationships, never proof of causation.
- Heat maps: compact comparisons such as cohorts or correlations.

Every chart needs a descriptive title, meaningful axes, readable labels, and a statement of what it shows.

### Python competency gate

You can turn a raw public dataset into a clean analytical table, perform grouped analysis and joins, create three decision-useful charts, and publish a notebook that runs from top to bottom without errors.

## 9. Phase 5: Statistics and Business Analytics

Statistics helps you distinguish a meaningful signal from noise. Learn concepts deeply enough to make sound analyst decisions, not merely to repeat formulas.

### 9.1 Descriptive statistics

- Population versus sample.
- Mean, median, mode, range, variance, standard deviation, and percentiles.
- Skewness and outliers: why median may represent a typical customer better than mean.
- Distribution shape and the danger of summarising every dataset with a single average.
- Correlation versus causation and confounding variables.

### 9.2 Probability and inference

- Random sampling and sampling bias.
- Confidence intervals: a plausible range for an estimated population value under the sampling method.
- Null and alternative hypotheses.
- Type I error (false positive) and Type II error (false negative).
- p-value: evidence against the null under the model assumptions; it is not the probability the null is true.
- Statistical significance versus practical business significance.

### 9.3 A/B testing

For a basic controlled experiment:

1. Define the decision, primary metric, guardrail metrics, audience, and test duration before launching.
2. State `H0` and `H1`.
3. Check random assignment, sample-ratio mismatch, data quality, and novelty effects.
4. Compare both absolute and relative impact.
5. Calculate or obtain a confidence interval and test result.
6. Decide based on both statistical evidence and business impact.
7. State limitations: seasonality, multiple testing, incomplete tracking, or insufficient sample size.

Example interpretation:

> Variant B increased checkout conversion by 0.7 percentage points. The confidence interval excludes zero, so the result is statistically credible under the test assumptions. Before rollout, verify that refund rate and average order value did not decline, and estimate incremental monthly profit rather than relying only on the conversion percentage.

### 9.4 Core analytical patterns

- **Funnel analysis:** Identify the stage with the largest absolute and relative drop-off.
- **Cohort analysis:** Group users by their first activity period and track retention over time.
- **Segmentation:** Compare meaningful groups such as geography, customer type, channel, or tenure.
- **Pareto analysis:** Determine whether a minority of products, customers, or issues drive most outcomes.
- **Variance analysis:** Explain actual versus budget, target, prior period, or forecast.
- **Root-cause analysis:** Examine drivers and hypotheses; do not claim causality without suitable evidence.

### Statistics competency gate

You can explain whether an observed metric change is meaningful, name the assumptions behind the conclusion, and translate the result into a cautious business recommendation.

## 10. Portfolio: Five End-to-End Projects

Your portfolio must show analytical thinking, not just tool screenshots. Every project should have a dedicated GitHub folder and a README that lets a recruiter understand the work in under two minutes.

### Portfolio README template

```markdown
# Project Title

## Business problem
Who needs to decide what, and why?

## Dataset
Source, time range, grain, size, license, and known limitations.

## Questions answered
List 3-5 decision-focused questions.

## Method
Cleaning, modelling, SQL/Python/BI work, and validation checks.

## Key findings
Use specific numbers and charts or dashboard images.

## Recommendations
State an owner, action, expected impact, and how success will be measured.

## Limitations and next steps
State what the data cannot prove and what you would investigate next.

## Tools
Excel, PostgreSQL, Power BI, Python/pandas, Tableau, etc.
```

### Project 1: Retail Sales and Profitability Dashboard

**Goal:** Help a retail manager identify where to grow revenue without harming profitability.

**Suggested data:** Sample Superstore, Global Superstore, AdventureWorks, or a public retail sales dataset.

**Skills demonstrated:** Excel, SQL, Power BI, KPI design, profitability analysis, dashboard UX.

**Business questions:**

1. How are revenue, profit, and margin changing by month and quarter?
2. Which categories, subcategories, regions, and customer segments drive profit or loss?
3. Which high-revenue products have poor margins?
4. Do large discounts correlate with lower profit?
5. What actions should the sales manager take next quarter?

**Execution steps:**

1. Create a data dictionary and determine the grain of the sales table.
2. Clean names, dates, nulls, duplicates, discount values, and negative or invalid quantities.
3. Calculate revenue, profit, profit margin, AOV, and discount bands.
4. Use SQL to calculate monthly performance, category rankings, product profitability, and region comparisons.
5. Model the data in Power BI with a calendar table.
6. Build a report with executive summary, trend analysis, product/category detail, and regional drill-through.
7. Validate Power BI totals against SQL control queries.
8. Write three prioritised recommendations, each tied to a metric.

**Required deliverables:** SQL file, Excel workbook, `.pbix` file, PDF/exported dashboard images, README, and a three-minute walkthrough recording or presentation.

**What makes it strong:** You do not only identify the top-selling category; you expose the revenue-versus-margin trade-off and recommend a measurable action such as discount guardrails for a loss-making subcategory.

### Project 2: Customer Retention and Cohort Analysis

**Goal:** Help a subscription or marketplace team understand repeat behaviour and prioritise retention.

**Suggested data:** Olist E-Commerce dataset, Online Retail dataset, or a public subscription dataset.

**Skills demonstrated:** Advanced SQL, CTEs, window functions, cohorts, segmentation, Python/pandas.

**Business questions:**

1. What percentage of customers make a second purchase?
2. How does retention vary by acquisition month, channel, category, or geography?
3. Which cohort has the strongest and weakest retention?
4. What are the behaviours of high-value repeat customers?
5. Where should the retention team investigate first?

**Execution steps:**

1. Define “active customer,” “repeat customer,” and the cohort date explicitly.
2. Build a customer first-purchase table using `MIN(order_date)`.
3. Calculate months since cohort and create a cohort retention matrix in SQL.
4. Use `LAG` or window functions to measure time between purchases.
5. Build RFM segments with documented cutoffs.
6. Recreate or validate the cohort calculation in pandas.
7. Visualise retention heat maps and repeat-purchase distributions.
8. Describe hypotheses, not unsupported causes, for poor-performing cohorts.

**Required deliverables:** SQL cohort queries, reproducible Python notebook, retention heat map, executive summary, README.

**What makes it strong:** You explain denominator logic. Retention is only meaningful when the cohort population and period definitions are precise.

### Project 3: Marketing Campaign Performance and A/B Test

**Goal:** Help a growth manager decide which marketing channel or creative to scale.

**Suggested data:** Public digital marketing campaign data from Kaggle; if A/B test data is unavailable, use a realistic synthetic dataset and label it transparently as synthetic.

**Skills demonstrated:** Funnel metrics, attribution caveats, Excel/Python analysis, statistics, Power BI or Tableau storytelling.

**Business questions:**

1. Which channels deliver the best quality leads, not only the most clicks?
2. What is conversion rate and cost per acquisition by channel, campaign, device, and audience?
3. Did variant B materially improve conversion compared with variant A?
4. Are there guardrail issues such as lower average order value or higher refunds?
5. What budget reallocation should be tested next?

**Execution steps:**

1. Define impressions, clicks, sessions, leads, conversions, revenue, spend, CPA, ROAS, and conversion rate.
2. Verify the funnel denominator for every rate.
3. Clean duplicate campaign records and standardise channel naming.
4. Calculate funnel performance and cost efficiency by segment.
5. Check whether the experiment groups are balanced and whether the dataset has obvious tracking gaps.
6. Perform a two-proportion conversion comparison using a documented method or statistical package.
7. Report confidence interval, practical impact, and caveats.
8. Build a funnel and channel-efficiency dashboard.

**Required deliverables:** Analysis notebook, campaign dashboard, A/B-test memo, README with metric definitions.

**What makes it strong:** You separate “highest traffic” from “highest business value,” avoid treating correlation as causation, and make a reversible test recommendation.

### Project 4: Operations and Delivery Performance

**Goal:** Help an operations leader reduce late deliveries and improve customer experience.

**Suggested data:** Olist E-Commerce, NYC taxi trip data, airline on-time performance data, or public logistics data.

**Skills demonstrated:** SQL joins, date calculations, operational KPIs, root-cause investigation, dashboard design.

**Business questions:**

1. What is the on-time delivery rate by month, region, seller, and product category?
2. Where is delay worst, and how much volume is affected?
3. Is the delay concentrated before shipment, during transit, or in a particular geography?
4. Which customer groups are most exposed to delayed orders?
5. What operational action should be prioritised?

**Execution steps:**

1. Define delivered, late, cancelled, promised date, and on-time delivery rate.
2. Build a single analysis table from orders, customers, sellers, products, and delivery events.
3. Validate that joins do not multiply order records.
4. Calculate order processing time, estimated transit time, actual transit time, and delay days.
5. Segment results by state, seller, category, and time period.
6. Investigate both delay rate and delayed-order volume; they can point to different priorities.
7. Build a drill-down dashboard for operations managers.
8. Present an action plan with a measurable service-level target.

**Required deliverables:** SQL analysis, Power BI report, data-quality validation notes, one-page operations recommendation.

**What makes it strong:** You distinguish a high delay percentage in a tiny segment from the largest absolute source of delayed orders.

### Project 5: Product Analytics Capstone

**Goal:** Recommend an improvement to a digital product’s activation or conversion funnel.

**Suggested data:** Public event-level product analytics dataset, Google Analytics sample data, or transparently labelled synthetic event data.

**Skills demonstrated:** Event data, funnels, segmentation, cohorts, KPI framework, SQL, Python, Tableau/Power BI, executive storytelling.

**Business questions:**

1. What is the activation funnel from sign-up to first value event?
2. Which step causes the largest drop-off for each user segment?
3. Do activated users retain better than non-activated users?
4. Which acquisition, device, geography, or user-type segments need attention?
5. Which product experiment should be run first and what is its success metric?

**Execution steps:**

1. Create an event taxonomy: event name, user ID, timestamp, session ID, platform, and properties.
2. Define the funnel in a fixed order and decide whether steps must occur in one session or within a time window.
3. De-duplicate events and handle repeated events deliberately.
4. Write SQL that calculates user-level funnel progression.
5. Build an activation cohort and retention analysis.
6. Use Python for segmentation and charts; use BI for stakeholder-facing exploration.
7. Design an experiment proposal with hypothesis, primary metric, guardrails, and target segment.
8. Present a product narrative: problem, evidence, recommendation, measurement plan, and risk.

**Required deliverables:** Data model diagram, SQL query pack, Python notebook, public dashboard, product recommendation deck, README.

**What makes it strong:** It shows the complete analyst workflow, including metric definition and an experiment plan, rather than only an attractive dashboard.

## 11. Portfolio Quality Standard

### A recruiter should see this immediately

- A short business problem, not a vague “data analysis project.”
- Named data source, date range, grain, and limitations.
- Screenshots or links to polished dashboards.
- SQL and Python artefacts that are organised and readable.
- Specific findings with numbers.
- Recommendations with a responsible team, expected outcome, and success metric.

### Dashboard acceptance checklist

- Every KPI has a definition.
- The dashboard has a clear audience and decision.
- Filters work and have sensible defaults.
- Totals reconcile to source or SQL checks.
- Titles answer what is being shown, not only name the chart type.
- Text is readable at normal viewing size.
- Colours are consistent and accessible.
- The report includes date coverage and data refresh/source notes.
- A stakeholder can identify the top insight in 30 seconds.

### Analysis acceptance checklist

- Raw data is preserved.
- Cleaning decisions are documented.
- Key calculations are reproducible.
- Joins and aggregations have validation checks.
- Claims distinguish observation, interpretation, and recommendation.
- Causation is not claimed from descriptive data alone.
- Limitations and possible bias are stated.

## 12. Interview Preparation

### 12.1 SQL interview topics

Prepare to write and explain:

- `WHERE` versus `HAVING`.
- `INNER JOIN` versus `LEFT JOIN`.
- `COUNT(*)` versus `COUNT(column)` versus `COUNT(DISTINCT column)`.
- `UNION` versus `UNION ALL`.
- CTE versus subquery.
- `ROW_NUMBER`, `RANK`, and `DENSE_RANK`.
- Finding duplicate rows.
- Second-highest value and top-N per group.
- Running totals and moving averages.
- Month-over-month comparison using `LAG`.
- Cohort retention calculation.
- Diagnosing duplicated values after a join.

When coding, narrate your assumptions: table grain, key fields, time range, null handling, and how you would validate the result.

### 12.2 Excel and Power BI interview topics

Be able to demonstrate or explain:

- `XLOOKUP`, `INDEX`/`MATCH`, `SUMIFS`, `COUNTIFS`, and PivotTables.
- How you clean a dataset before building a report.
- Power Query versus DAX.
- Calculated column versus measure.
- Star schema and relationship direction.
- Filter context and `CALCULATE`.
- How you would improve a slow or confusing dashboard.
- How you validate dashboard numbers.

### 12.3 Python interview topics

- `Series` versus `DataFrame`.
- `merge` types and duplicate-key risk.
- `groupby`, `agg`, `transform`, and `pivot_table`.
- Missing value decisions.
- Vectorised operations versus row-wise `apply`.
- How to load data from SQL or CSV and validate it.
- How you make a notebook reproducible.

### 12.4 Business case interview framework

When asked a case such as “Why did revenue drop?” answer in this sequence:

1. Clarify the metric definition, scope, and comparison period.
2. Break revenue into drivers, for example traffic x conversion x average order value.
3. Segment by time, geography, channel, product, customer type, and funnel stage.
4. Check data quality, tracking changes, and seasonality before claiming a business cause.
5. Prioritise the largest or highest-impact driver.
6. Recommend an action and a metric to monitor its result.

### 12.5 Portfolio walkthrough script

Use this structure in interviews:

1. **Context:** “I analysed delivery performance to help an operations manager reduce late orders.”
2. **Data:** “The grain was one order, joined to customer, seller, and product tables.”
3. **Method:** “I cleaned date fields, validated joins, used SQL for delivery metrics, and built a Power BI drill-down report.”
4. **Insight:** “Late deliveries were concentrated in two states and one seller group, accounting for X% of all late orders.”
5. **Action:** “I recommended a seller-level SLA review and a weekly on-time delivery monitor.”
6. **Limitation:** “The data showed where delay occurred but not the operational root cause; I would next request warehouse scan timestamps.”

Keep the first answer under two minutes. Be ready to go deeper into SQL, modelling, and validation if asked.

## 13. Resume, LinkedIn, and Application Strategy (India)

### Resume structure

Keep the resume to one page as a fresher unless you have substantial relevant experience.

1. Name, city, phone, professional email, LinkedIn, GitHub, and portfolio links.
2. Targeted headline: `Aspiring Data Analyst | SQL | Power BI | Excel | Python | Tableau`.
3. Two-to-three-line summary focused on demonstrable projects, not generic self-description.
4. Skills grouped by category.
5. Projects, with the strongest two or three first.
6. Education, relevant coursework, certifications, internships, or achievements.

### Strong project bullet formula

Use **action + method/tool + result/insight + business purpose**.

Weak:

> Created a dashboard using Power BI.

Strong:

> Built a Power BI sales and profitability dashboard from 9,000+ retail transactions using Power Query, star-schema modelling, and DAX; identified loss-making discount bands and proposed category-level discount guardrails.

Never invent commercial impact. If the project uses public data, state “identified” or “recommended,” not “increased company revenue.”

### ATS keywords

Use keywords only when you can defend them in an interview:

```text
SQL, PostgreSQL, Excel, PivotTables, Power BI, Power Query, DAX, Tableau,
Python, pandas, NumPy, data cleaning, data visualisation, dashboarding,
data modelling, ETL, KPI reporting, business analysis, cohort analysis,
funnel analysis, A/B testing, statistics, Git, GitHub
```

### LinkedIn profile

- Use a clear headshot and a concise, skill-specific headline.
- Add GitHub, Tableau Public, and Power BI portfolio links in Featured.
- Write an About section that names your role target, tools, and one or two project outcomes.
- Post brief project case studies: business question, one visual, insight, action, link.
- Connect thoughtfully with analysts, alumni, recruiters, and managers in target companies. Personalise outreach; do not send mass requests.

### Application operating system

Maintain a spreadsheet with company, role, job link, date applied, skills requested, referral/contact, resume version, status, follow-up date, and notes. Tailor your resume headline and project ordering to every relevant role. Apply consistently across LinkedIn, Naukri, Indeed, Wellfound, Internshala for internships, company career pages, and alumni/referral channels.

Focus on roles whose core requirements are SQL, Excel, Power BI/Tableau, reporting, KPI analysis, and communication. Do not self-reject because a listing asks for one or two years of experience; apply when you meet the core technical requirements and can demonstrate strong projects.

## 14. Practice Resources

Use authoritative documentation as your primary reference when stuck, and use practice platforms for repetition.

| Skill | Practice direction |
|---|---|
| SQL | SQLBolt for fundamentals; DataLemur, StrataScratch, and LeetCode database problems for interview practice; PostgreSQL documentation for behaviour details |
| Excel | Microsoft Learn, public sales datasets, and recreating reports from raw data |
| Power BI | Microsoft Learn modules; rebuild a dashboard from a public dataset rather than following clicks blindly |
| Tableau | Tableau Public visualisation gallery for critique and inspiration; Tableau learning resources for calculation concepts |
| Python | Official Python tutorial; pandas documentation; public datasets in Jupyter notebooks |
| Statistics | Khan Academy, OpenIntro Statistics, and experiment-analysis articles; always solve examples using data |
| Projects | Kaggle, data.gov.in, World Bank Open Data, data portals, and clearly cited public APIs |

Do not collect courses indefinitely. For every learning resource, produce an artefact: a query, workbook, notebook, dashboard, or documented explanation.

## 15. Common Mistakes That Block Freshers

| Mistake | Better approach |
|---|---|
| Learning only through videos | Rebuild analyses from raw data without copying the instructor. |
| Creating dashboards before validating data | Reconcile totals with control queries and document cleaning. |
| Using every chart type | Select the simplest chart that answers the business question. |
| Treating SQL output as automatically correct | Check grain, joins, nulls, totals, and denominators. |
| Listing tools without projects | Show proof through GitHub, Tableau Public, and dashboard screenshots. |
| Building generic “Netflix dashboard” clones | Frame a unique business question and make evidence-based recommendations. |
| Overstating results | Separate public-data findings from real-world business outcomes. |
| Ignoring communication | Practice explaining every project clearly to a non-technical stakeholder. |
| Learning ML before SQL | Prioritise the skills most frequently tested for analyst roles. |
| Applying with one static resume | Tailor headline, keywords, and project order to the role. |

## 16. Final Job-Readiness Assessment

You are ready to intensify applications when you can complete all of the following without a tutorial:

- [ ] Write multi-table SQL with joins, CTEs, window functions, and validation checks.
- [ ] Explain grain, primary keys, foreign keys, and why joins may duplicate results.
- [ ] Clean and analyse a raw spreadsheet using formulas, PivotTables, and charts.
- [ ] Build a Power BI report using Power Query, a star schema, validated DAX measures, and useful interactions.
- [ ] Publish one polished Tableau Public dashboard.
- [ ] Build a reproducible pandas notebook with cleaning, joins, aggregation, and charts.
- [ ] Define revenue, conversion, retention, churn, AOV, and at least one operations KPI correctly.
- [ ] Explain statistical significance, confidence intervals, and practical significance in plain language.
- [ ] Present five well-documented projects, including at least two strong end-to-end case studies.
- [ ] Answer a portfolio walkthrough with business context, method, finding, recommendation, and limitation.
- [ ] Have a one-page ATS-friendly resume, completed LinkedIn profile, GitHub portfolio, and application tracker.
- [ ] Complete mock SQL, dashboard, business case, and HR interviews.

## 17. Beyond the First Job

After landing a role or mastering this foundation, deepen skills based on the work you enjoy:

| Direction | Next skills |
|---|---|
| BI Analyst | Advanced DAX, semantic models, data governance, Power BI Service, Tableau calculations |
| Product Analyst | Event instrumentation, experimentation, Amplitude/Mixpanel, advanced cohorts and retention |
| Analytics Engineer | dbt, data warehouses, dimensional modelling, testing, version-controlled transformations |
| Data Scientist | Probability, regression, machine learning, model evaluation, deployment fundamentals |
| Data Engineer | Advanced SQL, orchestration, cloud warehouses, pipelines, distributed systems |

The durable advantage is not any single tool. It is the ability to define the right question, build trustworthy evidence, communicate uncertainty, and help a business make a better decision.
