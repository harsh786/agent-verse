# AgentVerse Workflow Automation Catalog

> **Status:** Brainstorming / Design  
> **Date:** 2026-08-15  
> **Purpose:** Comprehensive catalog of every workflow automation achievable on the AgentVerse platform using the user-defined workflow engine. Each automation lists its trigger, all steps with tool/AI assignments, inputs, outputs, and conditional branches. This document is the source of truth before implementation begins.

---

## Workflow Design Principles

| Principle | Description |
|---|---|
| **Predefined steps** | User defines steps at design time; LLM does NOT invent steps at runtime |
| **AI inside steps** | LLM, OCR, RAG used *within* individual steps; structure is fixed |
| **Step output chaining** | Every step's output is available to subsequent steps via `{{steps.N.output.field}}` |
| **Modality-agnostic** | Step 1 can ingest any input type; downstream steps are identical regardless |
| **Multi-trigger** | Any workflow can be triggered by webhook, schedule, natural language, or API |
| **HITL gates** | Any step can pause for human approval before proceeding |
| **Sub-workflow reuse** | A workflow can invoke another workflow as a step |

---

## Step Type Reference

| Type | Description | Example |
|---|---|---|
| `tool` | Execute a specific MCP tool | `ocr.extract_document`, `email.send` |
| `llm` | LLM call with a prompt template | Extract fields, summarize, decide |
| `rag` | Knowledge retrieval from a collection | Find matching policies |
| `conditional` | Branch based on output field value | `if risk_score > 0.8 → approve` |
| `hitl` | Human-in-the-loop gate | Manual review before sending |
| `sub_workflow` | Invoke another workflow as a step | KYC inside Merchant Onboarding |
| `http` | Raw HTTP call to external API | Fetch from third-party REST API |
| `parallel` | Run N steps simultaneously | Multiple data enrichment calls |
| `transform` | Map/format data without AI | JSON reshape, field rename |
| `wait` | Delay or wait for external event | Wait 24h, wait for webhook callback |

---

## Section 1 — Identity & Compliance Workflows

---

### 1.1 KYC Automation

**Purpose:** Automate Know Your Customer document verification for financial, legal, and onboarding use cases.  
**Trigger:** Document upload (API) | Nightly batch (schedule) | Webhook from web portal  
**Inputs:** `document_url` (or `document_base64`), `customer_id`, `document_type` (hint, optional)  
**Outputs:** `kyc_status` (approved/rejected/pending), `confidence_score`, `extracted_fields`, `audit_trail`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `ingest` | `tool` | `ocr.extract_document` | `document_url` | `raw_text`, `document_type`, `fields` (dict), `confidence` |
| 2 | `classify` | `llm` | Classifier prompt | `raw_text`, `document_type` hint | `document_type` (PAN/Aadhaar/Passport/DL/etc.) |
| 3 | `extract_fields` | `llm` + `rag` | Extraction prompt + `kyc-field-schemas` collection | `raw_text`, `document_type` | `name`, `dob`, `id_number`, `address`, `expiry`, `photo_url` |
| 4 | `validate_fields` | `llm` | Validation prompt + rules | `extracted_fields`, `document_type` | `field_validity` map, `missing_fields` list, `format_errors` list |
| 5 | `fraud_scan` | `llm` + `rag` | Fraud analysis prompt + `fraud-patterns` collection | `extracted_fields`, `customer_id`, `raw_text` | `risk_score` (0–1), `risk_factors` list, `fraud_signals` list |
| 6 | `sanctions_check` | `http` | Sanctions API (`/check`) | `name`, `dob`, `id_number` | `sanctions_match` bool, `match_details` |
| 7 | `decision` | `conditional` | — | `risk_score`, `field_validity`, `sanctions_match`, `confidence` | Branch: → `auto_approve` if score > 0.8 and valid and no sanction; → `human_review` if score 0.4–0.8; → `auto_reject` if score < 0.4 or sanction match |
| 8a | `auto_approve` | `tool` | `crm.update_status` | `customer_id`, status=`approved` | `updated: true` |
| 8b | `human_review` | `hitl` | Assigned role: `kyc_officer` | Full result + document preview | Human decision: `approved`/`rejected`/`request_more` |
| 8c | `auto_reject` | `tool` | `crm.update_status` | `customer_id`, status=`rejected` | `updated: true` |
| 9 | `notify` | `tool` | `email.send` or `sms.send` | `customer_id`, `decision`, `reason` | `sent: true`, `message_id` |
| 10 | `audit_log` | `tool` | `audit.record` | All step outputs, `workflow_run_id` | `audit_entry_id` |

**Parallel optimization:** Steps 5 (fraud scan) and 6 (sanctions check) can run in parallel after step 3.

---

### 1.2 Merchant Onboarding

**Purpose:** End-to-end onboarding of a new merchant including identity, business verification, risk scoring, and account provisioning.  
**Trigger:** API call with merchant application data | Form submission webhook  
**Inputs:** `merchant_id`, `business_name`, `gstin`, `pan`, `bank_account`, `document_urls` dict  
**Outputs:** `onboarding_status`, `merchant_account_id`, `assigned_limits`, `welcome_email_sent`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `kyc` | `sub_workflow` | Workflow: `kyc-automation` | Owner PAN + Aadhaar documents | `kyc_status`, `kyc_confidence`, `owner_details` |
| 2 | `business_verify` | `parallel` | — | — | Run steps 2a, 2b, 2c simultaneously |
| 2a | `gstin_check` | `http` | GST Verification API | `gstin` | `gstin_valid`, `business_name_match`, `filing_status` |
| 2b | `bank_verify` | `http` | Bank Account Penny Drop API | `account_number`, `ifsc` | `account_valid`, `name_match`, `penny_drop_amount` |
| 2c | `web_presence` | `llm` + `tool` | Web search + LLM analysis | `business_name`, `website_url` | `business_age_estimate`, `reviews_sentiment`, `social_presence_score` |
| 3 | `risk_score` | `llm` + `rag` | Risk scoring prompt + `merchant-risk-patterns` | All step outputs | `overall_risk_score`, `risk_tier` (low/medium/high), `risk_factors` |
| 4 | `compliance_check` | `rag` | `compliance-rules` collection | `business_category`, `transaction_volume`, `geography` | `compliance_requirements`, `blocked_categories` |
| 5 | `limit_assignment` | `llm` | Limit calculation prompt | `risk_tier`, `business_type`, `verified_revenue` | `daily_limit`, `monthly_limit`, `per_txn_limit` |
| 6 | `decision` | `conditional` | — | `risk_tier`, `kyc_status`, `gstin_valid` | Branch: → `auto_approve` if low risk + all verified; → `enhanced_review` if medium risk; → `reject` if high risk or any verification failed |
| 7a | `auto_approve` | `tool` | `payment_gateway.create_merchant` | `merchant_id`, `limits`, `verified_docs` | `merchant_account_id`, `api_keys` |
| 7b | `enhanced_review` | `hitl` | Role: `risk_officer` | Full risk report | Decision: proceed/reject/request_docs |
| 7c | `reject` | `tool` | `crm.update_status` | `merchant_id`, status=`rejected`, `reason` | `updated: true` |
| 8 | `provision` | `tool` | `payment_gateway.configure_merchant` | `merchant_account_id`, `limits`, `webhooks` | `configured: true`, `test_credentials` |
| 9 | `welcome` | `tool` | `email.send` + `tool: docs.generate` | `merchant_id`, `merchant_account_id`, `limits` | Welcome email + onboarding guide PDF sent |
| 10 | `audit_log` | `tool` | `audit.record` | Full workflow trace | `audit_entry_id` |

---

### 1.3 AML Screening (Anti-Money Laundering)

**Purpose:** Screen transactions against AML patterns, sanction lists, and behavioural anomalies.  
**Trigger:** Transaction event webhook | Daily batch on flagged accounts  
**Inputs:** `transaction_id`, `account_id`, `amount`, `beneficiary`, `purpose_code`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_context` | `http` | Core Banking API | `account_id` | `account_history`, `kyc_tier`, `previous_flags` |
| 2 | `sanctions_check` | `http` | OFAC / UN Sanctions API | `beneficiary_name`, `beneficiary_account` | `sanctions_match` bool, `match_score` |
| 3 | `pep_check` | `http` | PEP Database API | `beneficiary_name`, `country` | `is_pep` bool, `pep_category` |
| 4 | `pattern_analysis` | `llm` + `rag` | AML pattern prompt + `aml-typologies` collection | `transaction`, `account_history` | `typology_match` list, `anomaly_score`, `behavioral_flags` |
| 5 | `risk_classify` | `llm` | Risk classification prompt | All step outputs | `aml_risk_level` (low/medium/high/critical), `risk_rationale` |
| 6 | `decision` | `conditional` | — | `aml_risk_level`, `sanctions_match`, `is_pep` | Branch: → `clear` if low; → `enhanced_due_diligence` if medium; → `block_and_report` if high/critical or sanctions match |
| 7a | `clear` | `tool` | `transaction.approve` | `transaction_id` | `approved: true` |
| 7b | `enhanced_due_diligence` | `hitl` | Role: `compliance_officer` | Full risk report | Officer decision: clear/block/escalate |
| 7c | `block_and_report` | `tool` | `transaction.block` + `regulator.file_str` | `transaction_id`, risk report | `blocked: true`, `str_reference_number` |
| 8 | `audit_log` | `tool` | `audit.record` | Full trace, regulatorily formatted | `audit_entry_id` |

---

### 1.4 GDPR Data Subject Request (Right to Erasure)

**Purpose:** Process GDPR Article 17 deletion requests — locate all personal data, redact/delete it, and produce a deletion certificate.  
**Trigger:** Email / web form webhook | API from Data Protection Officer portal  
**Inputs:** `subject_email`, `subject_id`, `request_type` (delete/access/portability), `request_timestamp`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `verify_identity` | `llm` + `tool` | Identity verification + `crm.lookup` | `subject_email` | `verified: bool`, `subject_id`, `verification_method` |
| 2 | `locate_data` | `parallel` | — | `subject_id` | Run steps 2a–2e simultaneously |
| 2a | `locate_db` | `tool` | `db.search_user_records` | `subject_id` | `db_records_count`, `tables_affected` list |
| 2b | `locate_memory` | `tool` | `memory.search_by_tenant_user` | `subject_id` | `memory_entries` list |
| 2c | `locate_logs` | `tool` | `logs.search` | `subject_id` | `log_entries_count`, `log_sources` list |
| 2d | `locate_backups` | `tool` | `backup.index_search` | `subject_id` | `backup_references` list |
| 2e | `locate_third_party` | `llm` + `rag` | Processor mapping prompt + `data-processor-list` | `subject_id`, data categories | `third_party_processors` list requiring notification |
| 3 | `legal_review` | `hitl` | Role: `dpo` (Data Protection Officer) | Full data map, request details | Decision: proceed/deny with legal reason |
| 4 | `execute_deletion` | `parallel` | — | `subject_id` | Run steps 4a–4d simultaneously |
| 4a | `delete_db` | `tool` | `db.delete_user_records` | `subject_id`, `tables_affected` | `deleted_records_count` |
| 4b | `delete_memory` | `tool` | `memory.purge_by_user` | `subject_id` | `purged_entries_count` |
| 4c | `redact_logs` | `tool` | `logs.redact` | `subject_id`, `log_sources` | `redacted_count` |
| 4d | `notify_processors` | `tool` | `email.send_bulk` | `third_party_processors`, deletion notice | `notifications_sent` list |
| 5 | `generate_certificate` | `llm` + `tool` | Certificate template + `pdf.generate` | All deletion results, timestamp | `certificate_url`, `certificate_hash` |
| 6 | `notify_subject` | `tool` | `email.send` | `subject_email`, `certificate_url` | `sent: true` |
| 7 | `audit_log` | `tool` | `audit.record` (immutable, 7-year retention) | Full trace, certificate reference | `audit_entry_id` |

---

### 1.5 Employee Background Check

**Purpose:** Automate pre-hire background verification including identity, criminal, employment, and education checks.  
**Trigger:** HR system webhook on offer acceptance | API call from HR portal  
**Inputs:** `candidate_id`, `candidate_name`, `dob`, `pan`, `aadhaar`, `previous_employers` list, `education` list

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `document_collect` | `tool` | `portal.get_submitted_docs` | `candidate_id` | `documents` dict (ID proof, address proof, education certs) |
| 2 | `identity_verify` | `sub_workflow` | Workflow: `kyc-automation` | ID documents | `kyc_status`, `verified_identity` |
| 3 | `criminal_check` | `http` | Court Records API + Police Verification API | `name`, `dob`, `address_history` | `criminal_records` list, `pending_cases` list |
| 4 | `employment_verify` | `parallel` | — | `previous_employers` list | Run one step per employer simultaneously |
| 4.N | `verify_employer_N` | `http` + `llm` | HR verification API + LLM for unstructured responses | `employer_N`, `designation`, `duration` | `verified: bool`, `discrepancies` list |
| 5 | `education_verify` | `parallel` | — | `education` list | Run one step per institution simultaneously |
| 5.N | `verify_degree_N` | `http` | DigiLocker / University API | `institution_N`, `degree`, `year`, `roll_number` | `verified: bool`, `certificate_authentic: bool` |
| 6 | `reference_check` | `tool` + `llm` | `email.send` to references + LLM analyse responses | `references` list | `reference_scores`, `key_feedback` |
| 7 | `compile_report` | `llm` | Report generation prompt | All step outputs | `overall_status` (clear/caution/fail), `summary`, `discrepancies` list |
| 8 | `hr_review` | `hitl` | Role: `hr_manager` | Full report | Decision: hire/hold/reject |
| 9 | `notify_candidate` | `tool` | `email.send` | `candidate_id`, `decision`, `start_date` (if hired) | `sent: true` |
| 10 | `update_hrms` | `tool` | `hrms.update_candidate` | `candidate_id`, `bgv_status`, `report_reference` | `updated: true` |

---

## Section 2 — Financial Operations Workflows

---

### 2.1 Invoice Processing & Approval

**Purpose:** Extract, validate, match, and route invoices for payment from any input format.  
**Trigger:** Email attachment | File upload to shared drive | API webhook from supplier portal  
**Inputs:** `invoice_document` (PDF/image), `vendor_id` (optional)

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `ingest` | `tool` | `ocr.extract_document` | `invoice_document` | `raw_text`, `document_type: invoice`, `fields` |
| 2 | `extract_fields` | `llm` | Invoice extraction prompt | `raw_text` | `vendor_name`, `vendor_gst`, `invoice_number`, `invoice_date`, `line_items` list, `subtotal`, `tax_amount`, `total_amount`, `bank_details`, `due_date` |
| 3 | `vendor_lookup` | `tool` | `erp.get_vendor` | `vendor_name`, `vendor_gst` | `vendor_id`, `approved_vendor: bool`, `payment_terms`, `credit_limit` |
| 4 | `po_match` | `tool` | `erp.match_purchase_order` | `invoice_number`, `line_items`, `vendor_id` | `po_id`, `match_status` (full/partial/no_match), `variance_amount` |
| 5 | `validate` | `llm` | Validation prompt + `finance-rules` RAG | `extracted_fields`, `po_match`, `vendor_details` | `validation_status`, `errors` list, `warnings` list |
| 6 | `three_way_match` | `tool` | `erp.three_way_match` | `po_id`, `invoice_id`, `grn_id` | `match_result`, `discrepancies` |
| 7 | `route_approval` | `conditional` | — | `total_amount`, `match_result`, `variance_amount` | Branch: → `auto_approve` if amount < ₹10,000 and full match; → `manager_approve` if ₹10,000–₹1,00,000; → `finance_head_approve` if > ₹1,00,000 or partial match |
| 8a | `auto_approve` | `tool` | `erp.approve_invoice` | `invoice_id` | `approved: true` |
| 8b | `manager_approve` | `hitl` | Role: `department_manager` | Invoice summary, PO match | Decision: approve/reject/query |
| 8c | `finance_head_approve` | `hitl` | Role: `finance_head` | Full invoice analysis | Decision: approve/reject/negotiate |
| 9 | `schedule_payment` | `tool` | `erp.schedule_payment` | `invoice_id`, `due_date`, `bank_details` | `payment_scheduled_date`, `payment_reference` |
| 10 | `notify_vendor` | `tool` | `email.send` | `vendor_email`, `invoice_number`, `payment_date` | `sent: true` |
| 11 | `post_accounting` | `tool` | `erp.post_journal_entry` | `invoice_id`, `gl_accounts`, `cost_centers` | `journal_entry_id` |

---

### 2.2 Expense Report Processing

**Purpose:** Process employee expense claims from receipt collection through reimbursement.  
**Trigger:** Employee submission (API/app) | Email with receipts  
**Inputs:** `employee_id`, `expense_claim_id`, `receipts` list (images/PDFs), `expense_date_range`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `ingest_receipts` | `parallel` | `ocr.extract_document` (per receipt) | `receipts` list | `extracted_receipts` list: each with `merchant`, `date`, `amount`, `category`, `tax_amount` |
| 2 | `categorize` | `llm` | Expense categorization prompt | `extracted_receipts`, `employee_department` | `receipts_with_categories` list: each tagged with `gl_account`, `cost_center`, `expense_type` |
| 3 | `policy_check` | `llm` + `rag` | Policy validation + `expense-policy` collection | `receipts_with_categories`, `employee_grade`, `travel_destination` | `policy_violations` list, `approved_items`, `flagged_items`, `total_approved`, `total_flagged` |
| 4 | `duplicate_check` | `tool` | `erp.check_duplicate_expense` | `receipts` hashes, `employee_id` | `duplicates` list |
| 5 | `compute_totals` | `transform` | — | `approved_items` | `total_amount`, `tax_recoverable`, `reimbursable_amount` |
| 6 | `approval_route` | `conditional` | — | `reimbursable_amount`, `policy_violations` count | Branch: → `auto_approve` if amount < ₹5,000 and zero violations; → `manager_approve` if ₹5,000–₹50,000 or 1–2 minor violations; → `finance_review` if > ₹50,000 or critical violations |
| 7a | `auto_approve` | `tool` | `payroll.schedule_reimbursement` | `employee_id`, `reimbursable_amount` | `reimbursement_id`, `payment_date` |
| 7b | `manager_approve` | `hitl` | Role: `reporting_manager` | Expense summary, flagged items | Decision: approve/partial/reject |
| 7c | `finance_review` | `hitl` | Role: `finance_controller` | Full report with policy violations | Decision: approve/partial/reject/investigate |
| 8 | `process_payment` | `tool` | `payroll.process_reimbursement` | `employee_id`, `approved_amount` | `payment_reference`, `bank_credit_date` |
| 9 | `notify_employee` | `tool` | `email.send` | `employee_email`, `status`, `amount`, `rejection_reasons` (if any) | `sent: true` |

---

### 2.3 Loan Pre-screening

**Purpose:** Automate initial loan eligibility assessment before human underwriter review.  
**Trigger:** Loan application API | Web form submission  
**Inputs:** `applicant_id`, `loan_amount`, `loan_purpose`, `tenure_months`, `income_documents` list

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `identity_kyc` | `sub_workflow` | Workflow: `kyc-automation` | ID documents | `kyc_status`, `verified_name`, `verified_dob` |
| 2 | `income_extract` | `parallel` | — | `income_documents` | Run steps 2a, 2b, 2c in parallel |
| 2a | `salary_slip_parse` | `tool` + `llm` | `ocr.extract_document` + extraction prompt | Salary slip PDFs | `monthly_gross`, `monthly_net`, `employer_name`, `designation` |
| 2b | `bank_statement_parse` | `tool` + `llm` | `ocr.extract_document` + analysis prompt | 6-month bank statements | `avg_monthly_balance`, `avg_monthly_credits`, `emi_obligations`, `salary_credits_verified` |
| 2c | `itr_parse` | `tool` + `llm` | `ocr.extract_document` + extraction prompt | ITR documents | `annual_income`, `tax_paid`, `income_sources` |
| 3 | `credit_bureau` | `http` | CIBIL / Experian API | `pan`, `dob` | `credit_score`, `active_loans`, `overdue_amount`, `enquiries_6m` |
| 4 | `eligibility_calc` | `llm` | Eligibility calculation prompt | All income + credit data | `eligible_amount`, `suggested_tenure`, `emi_amount`, `dscr` (debt service coverage ratio) |
| 5 | `risk_classify` | `llm` + `rag` | Risk prompt + `loan-risk-policy` | All step outputs | `risk_grade` (A/B/C/D/E), `interest_rate_band`, `collateral_requirement` |
| 6 | `decision` | `conditional` | — | `credit_score`, `risk_grade`, `kyc_status` | Branch: → `pre_approved` if score > 700 and grade A/B; → `refer_underwriter` if score 600–700 or grade C; → `decline` if score < 600 or grade D/E |
| 7a | `pre_approved` | `tool` | `loan_system.create_offer` | `applicant_id`, `eligible_amount`, `rate`, `tenure` | `offer_id`, `offer_expiry` |
| 7b | `refer_underwriter` | `hitl` | Role: `underwriter` | Full assessment report | Underwriter decision + conditions |
| 7c | `decline` | `tool` | `loan_system.record_decline` | `applicant_id`, `decline_reasons` | `decline_reference` |
| 8 | `notify_applicant` | `tool` | `email.send` + `sms.send` | `applicant_id`, `decision`, `offer_details` (if pre-approved) | `sent: true` |

---

### 2.4 Bank Reconciliation

**Purpose:** Automatically match bank transactions against internal books and flag discrepancies.  
**Trigger:** Daily schedule (post-banking hours) | Manual trigger by finance team  
**Inputs:** `account_id`, `reconciliation_date`, `bank_statement_source`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_bank_statement` | `tool` | `bank.fetch_statement` or `ocr.extract_document` | `account_id`, `date` | `bank_transactions` list: `date`, `description`, `amount`, `balance` |
| 2 | `fetch_book_entries` | `tool` | `erp.get_journal_entries` | `account_id`, `date` | `book_entries` list: `date`, `description`, `amount`, `reference` |
| 3 | `auto_match` | `tool` | `reconciliation.auto_match` | `bank_transactions`, `book_entries` | `matched_pairs` list, `unmatched_bank` list, `unmatched_book` list |
| 4 | `fuzzy_match` | `llm` | Fuzzy matching prompt | `unmatched_bank`, `unmatched_book` | `likely_matches` list (with confidence), `remaining_unmatched_bank`, `remaining_unmatched_book` |
| 5 | `classify_discrepancies` | `llm` | Discrepancy classification prompt | Remaining unmatched items | `bank_only` items, `book_only` items, `timing_differences` list, `genuine_discrepancies` list |
| 6 | `decision` | `conditional` | — | `genuine_discrepancies` count, `total_variance` | Branch: → `auto_close` if zero genuine discrepancies; → `finance_review` if discrepancies exist |
| 7a | `auto_close` | `tool` | `erp.mark_reconciled` | `account_id`, `date`, `matched_pairs` | `reconciliation_id`, `status: complete` |
| 7b | `finance_review` | `hitl` | Role: `finance_controller` | Discrepancy report | Decision: approve items, create journal entries for timing differences |
| 8 | `post_adjustments` | `tool` | `erp.post_journal_entry` | Approved adjustments | `journal_entry_ids` list |
| 9 | `generate_report` | `tool` | `report.generate_pdf` | Full reconciliation summary | `report_url` |
| 10 | `notify_finance` | `tool` | `email.send` | Finance team, `report_url`, variance summary | `sent: true` |

---

## Section 3 — Developer & Engineering Operations

---

### 3.1 SRE Incident Response Assistant

**Purpose:** Automatically diagnose production incidents, retrieve relevant runbooks, suggest remediation, and optionally execute fixes.  
**Trigger:** PagerDuty webhook | CloudWatch alarm | Prometheus alert | Datadog monitor  
**Inputs:** `alert_id`, `service_name`, `alert_message`, `severity`, `alert_timestamp`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `context_pull` | `parallel` | — | `service_name`, `alert_timestamp` | Run steps 1a–1e simultaneously |
| 1a | `fetch_logs` | `tool` | `logging.fetch` | `service_name`, last 30 min | `error_log_lines` list, `log_anomalies` |
| 1b | `fetch_metrics` | `tool` | `monitoring.get_metrics` | `service_name`, last 30 min | `latency_p99`, `error_rate`, `throughput`, `resource_usage` |
| 1c | `fetch_traces` | `tool` | `tracing.get_recent_traces` | `service_name`, filter: errors only | `error_traces` list, `slow_traces` list |
| 1d | `fetch_deploy_history` | `tool` | `deployment.get_recent` | `service_name`, last 48h | `recent_deploys` list: commit, author, timestamp, diff_url |
| 1e | `fetch_related_alerts` | `tool` | `monitoring.get_correlated_alerts` | `alert_id`, time window | `related_alerts` list |
| 2 | `retrieve_runbooks` | `rag` | `runbooks` + `past-incidents` collections | `alert_message`, `service_name`, `error_patterns` | `relevant_runbooks` list, `similar_incidents` list, `past_resolutions` |
| 3 | `root_cause_analysis` | `llm` | RCA prompt | All step 1 outputs + step 2 | `probable_root_causes` list (ranked), `evidence` per cause, `confidence_per_cause` |
| 4 | `remediation_plan` | `llm` | Remediation prompt | `root_causes`, `runbooks`, `past_resolutions` | `remediation_steps` list (ranked by confidence), `estimated_impact`, `rollback_steps` |
| 5 | `decision` | `conditional` | — | `severity`, `top_remediation.confidence`, `top_remediation.risk_level` | Branch: → `auto_remediate` if severity ≤ P3 and confidence > 0.85 and risk = low; → `manual_remediate` otherwise |
| 6a | `auto_remediate` | `tool` | MCP tools per step (restart service, scale, flush cache, etc.) | `remediation_steps` | `actions_taken` list, `success: bool` |
| 6b | `manual_remediate` | `hitl` | Role: `on_call_engineer` | Full RCA + remediation plan | Engineer selects and executes steps |
| 7 | `verify_resolution` | `tool` | `monitoring.check_metrics` + `monitoring.check_alerts` | `service_name`, post-fix window | `metrics_normalized: bool`, `alerts_resolved: bool` |
| 8 | `incident_report` | `llm` | Report generation prompt | Full incident timeline, actions, verification | `incident_summary`, `timeline`, `action_items`, `lessons_learned` |
| 9 | `post_to_channels` | `parallel` | — | — | |
| 9a | `update_pagerduty` | `tool` | `pagerduty.resolve_incident` | `alert_id`, `resolution_summary` | `resolved: true` |
| 9b | `post_slack` | `tool` | `slack.send_message` | `#incidents` channel, incident report | `message_sent` |
| 9c | `create_jira` | `tool` | `jira.create_issue` | Post-mortem type, action items | `jira_ticket_ids` list |
| 9d | `update_confluence` | `tool` | `confluence.create_page` | Incident report, action items | `page_url` |
| 10 | `schedule_postmortem` | `tool` | `calendar.schedule` | Engineering team, 24h after resolution | `meeting_id`, `meeting_url` |

---

### 3.2 Production Bug Assistant

**Purpose:** Automatically triage GitHub issues, find root cause in codebase, generate a fix, and create a PR.  
**Trigger:** GitHub issue webhook (labelled `bug`) | Sentry alert webhook  
**Inputs:** `issue_id`, `issue_title`, `issue_body`, `repo_url`, `reporter`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `triage` | `llm` | Triage prompt | `issue_title`, `issue_body` | `bug_category`, `severity` (P1–P4), `affected_component`, `reproduction_steps`, `error_message` |
| 2 | `code_search` | `parallel` | — | `affected_component`, `error_message` | |
| 2a | `semantic_search` | `rag` | `codebase` collection (ingested repo) | `error_message`, `component` | `relevant_files` list, `relevant_functions` list |
| 2b | `grep_search` | `tool` | `code.search` | `error_message`, `stack_trace_fragments` | `exact_matches` list with file + line |
| 2c | `git_blame` | `tool` | `git.blame` | `relevant_files`, recently changed | `recent_changes` list: commit, author, diff |
| 3 | `root_cause` | `llm` | Root cause prompt | `triage`, `relevant_files`, `exact_matches`, `recent_changes` | `root_cause_explanation`, `fault_location` (file + line), `confidence`, `related_issues` |
| 4 | `impact_analysis` | `llm` + `rag` | Impact prompt + `test-coverage` collection | `fault_location`, `affected_functions` | `impact_scope`, `affected_test_files`, `api_contracts_affected`, `downstream_services` |
| 5 | `generate_fix` | `llm` | Fix generation prompt | `root_cause`, `fault_location`, `file_content` | `proposed_fix` (code diff), `fix_explanation`, `alternative_approaches` |
| 6 | `generate_tests` | `llm` | Test generation prompt | `proposed_fix`, `affected_functions`, `existing_test_patterns` | `new_test_cases` (code), `updated_test_cases` (code) |
| 7 | `create_pr` | `tool` | `github.create_pull_request` | `repo_url`, `proposed_fix`, `new_tests`, `pr_description` | `pr_url`, `pr_number` |
| 8 | `comment_issue` | `tool` | `github.create_comment` | `issue_id`, root cause summary, PR link | `comment_id` |
| 9 | `notify` | `tool` | `slack.send_message` | `#engineering`, PR link, severity, summary | `sent: true` |

---

### 3.3 Automated Code Review

**Purpose:** Automatically review pull requests for style, security, logic, and test coverage before human review.  
**Trigger:** GitHub pull_request webhook (opened / synchronize)  
**Inputs:** `pr_id`, `repo_url`, `diff_url`, `base_branch`, `head_branch`, `files_changed` list

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_diff` | `tool` | `github.get_pr_diff` | `pr_id` | `diff_content`, `files_changed`, `lines_added`, `lines_removed` |
| 2 | `parallel_analysis` | `parallel` | — | `diff_content`, `files_changed` | |
| 2a | `style_check` | `tool` | `linter.run` (ruff, eslint, etc.) | `files_changed` | `style_violations` list |
| 2b | `security_scan` | `tool` | `security_scanner.run` (bandit, semgrep) | `diff_content` | `security_findings` list with severity |
| 2c | `logic_review` | `llm` + `rag` | Code review prompt + `coding-standards` collection | `diff_content` | `logic_issues` list, `suggestions` list, `complexity_score` |
| 2d | `test_coverage_check` | `tool` | `coverage.analyze` | `files_changed`, `test_files` | `coverage_delta`, `untested_lines` list |
| 2e | `dependency_check` | `tool` | `dependency.audit` | `package_changes` | `new_vulnerabilities` list, `license_violations` |
| 3 | `compile_review` | `llm` | Review compilation prompt | All analysis outputs | `review_summary`, `must_fix` list, `suggestions` list, `praise` list, `overall_verdict` (approve/request-changes/comment) |
| 4 | `post_review` | `tool` | `github.create_review` | `pr_id`, `review_summary`, inline comments per finding | `review_id`, `review_url` |
| 5 | `check_pr_description` | `llm` | PR description quality prompt | `pr_description`, `diff_summary` | `description_quality_score`, `missing_sections` list |
| 6 | `notify` | `tool` | `slack.send_message` | PR author DM, review summary | `sent: true` |

---

### 3.4 Release Notes Generator

**Purpose:** Automatically generate structured release notes from git history when a new version tag is created.  
**Trigger:** GitHub tag push webhook | Manual trigger with version range  
**Inputs:** `repo_url`, `tag_name`, `previous_tag_name`, `release_channel` (internal/external)

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_commits` | `tool` | `git.get_commit_range` | `previous_tag`, `tag_name` | `commits` list: SHA, message, author, PR number, PR title |
| 2 | `fetch_pr_details` | `parallel` | `github.get_pull_request` per PR | `pr_numbers` list | `pr_details` list: description, labels, linked issues |
| 3 | `categorize_changes` | `llm` | Categorization prompt | `commits`, `pr_details` | `features` list, `bug_fixes` list, `breaking_changes` list, `deprecations` list, `performance` list, `security` list, `dependencies` list |
| 4 | `generate_release_notes` | `llm` | Release notes prompt | `categorized_changes`, `tag_name`, `release_channel` | `release_notes_markdown`, `release_summary`, `migration_guide` (if breaking changes) |
| 5 | `technical_review` | `hitl` | Role: `tech_lead` (optional, skip for patch releases) | Draft release notes | Approved / edited |
| 6 | `publish` | `parallel` | — | `release_notes_markdown` | |
| 6a | `publish_github` | `tool` | `github.create_release` | `tag_name`, `release_notes_markdown` | `release_url` |
| 6b | `post_slack` | `tool` | `slack.send_message` | `#releases`, `release_summary`, `release_url` | `sent: true` |
| 6c | `update_changelog` | `tool` | `git.commit_file` | `CHANGELOG.md` update | `commit_sha` |
| 6d | `update_confluence` | `tool` | `confluence.update_page` | Release notes page, content | `page_url` |

---

### 3.5 Security Vulnerability Response

**Purpose:** Detect, assess, and remediate security vulnerabilities in codebase and dependencies.  
**Trigger:** Snyk/Dependabot webhook | Scheduled weekly scan | CVE database alert  
**Inputs:** `repo_url`, `vulnerability_id`, `package_name`, `severity`, `affected_versions`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_vulnerability_details` | `http` | CVE Database API / Snyk API | `vulnerability_id` | `cve_details`, `cvss_score`, `attack_vector`, `exploit_available` |
| 2 | `impact_analysis` | `parallel` | — | `package_name`, `repo_url` | |
| 2a | `check_usage` | `tool` + `rag` | `code.search` + `codebase` collection | `package_name` | `files_importing` list, `functions_calling` list, `exposure_surface` |
| 2b | `check_exploitability` | `llm` + `rag` | Exploitability prompt + `app-architecture` RAG | `cve_details`, `usage_context` | `exploitability_in_context`, `attack_paths` |
| 3 | `severity_assessment` | `llm` | Risk assessment prompt | `cvss_score`, `exploitability_in_context`, `app_exposure` | `adjusted_severity`, `business_impact`, `urgency` (critical/high/medium/low) |
| 4 | `find_fix` | `parallel` | — | `package_name`, `affected_versions` | |
| 4a | `check_patch` | `http` | Package registry API | `package_name` | `patched_versions` list, `patch_changelog` |
| 4b | `assess_patch` | `llm` | Patch assessment prompt | `patch_changelog`, `current_usage` | `breaking_changes` list, `migration_effort` estimate |
| 5 | `decision` | `conditional` | — | `urgency`, `breaking_changes` | Branch: → `auto_patch` if urgency=low/medium and no breaking changes; → `engineer_review` if urgency=high and breaking changes; → `emergency_response` if urgency=critical |
| 6a | `auto_patch` | `tool` | `github.create_pr` with version bump | `package_name`, `patched_version`, `migration_notes` | `pr_url`, `pr_number` |
| 6b | `engineer_review` | `hitl` | Role: `security_engineer` | Full vulnerability + patch assessment | Approved PR / custom fix |
| 6c | `emergency_response` | `hitl` | Role: `security_lead` + `cto` (2 approvers) | Full report | Emergency patch / temporary mitigation / public disclosure plan |
| 7 | `create_jira_tickets` | `tool` | `jira.create_issue` | Vulnerability details, fix plan, deadline | `jira_ticket_ids` list |
| 8 | `notify` | `parallel` | — | — | |
| 8a | `notify_security_channel` | `tool` | `slack.send_message` | `#security`, full report | `sent: true` |
| 8b | `notify_cve_tracker` | `tool` | `security_tracker.record` | CVE, status, fix ETA | `tracker_id` |

---

## Section 4 — Customer & Support Operations

---

### 4.1 Email Auto-Response

**Purpose:** Automatically classify, retrieve relevant knowledge, and draft responses to incoming customer emails.  
**Trigger:** New email webhook (IMAP/SMTP) | Zendesk new ticket event  
**Inputs:** `email_id`, `from_email`, `subject`, `body`, `attachments` list

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `parse_email` | `llm` | Email parse prompt | `subject`, `body` | `intent` (support/sales/billing/complaint/general), `entities` extracted, `urgency`, `language` |
| 2 | `customer_lookup` | `tool` | `crm.get_customer` | `from_email` | `customer_id`, `account_tier`, `open_tickets`, `purchase_history`, `previous_contacts` |
| 3 | `classify_route` | `conditional` | — | `intent`, `account_tier` | Branch: → `support_flow` if intent=support; → `billing_flow` if intent=billing; → `sales_flow` if intent=sales; → `complaint_flow` if intent=complaint |
| 4 | `knowledge_retrieve` | `rag` | Relevant collection per route | `intent`, `entities`, `email body` | `relevant_articles` list, `similar_past_tickets` list, `policy_sections` relevant |
| 5 | `memory_recall` | `tool` | `memory.recall_by_customer` | `customer_id` | `past_interactions`, `known_preferences`, `open_issues` |
| 6 | `draft_response` | `llm` | Response draft prompt (personalized) | `email_body`, `knowledge_retrieved`, `memory`, `customer_profile` | `draft_response`, `confidence_score`, `knowledge_citations` |
| 7 | `quality_check` | `llm` | Quality and safety prompt | `draft_response` | `tone_appropriate: bool`, `factually_grounded: bool`, `pii_not_leaked: bool`, `quality_score` |
| 8 | `decision` | `conditional` | — | `confidence_score`, `account_tier`, `intent` | Branch: → `auto_send` if confidence > 0.85 and tier=standard; → `agent_review` if confidence < 0.85 or tier=premium or intent=complaint |
| 9a | `auto_send` | `tool` | `email.send` | `email_id`, `draft_response` | `sent: true`, `message_id` |
| 9b | `agent_review` | `hitl` | Role: `support_agent` | Draft + citations, customer context | Agent edits + sends |
| 10 | `update_crm` | `tool` | `crm.log_interaction` | `customer_id`, `email_id`, `response_sent`, `intent`, `resolution` | `interaction_id` |
| 11 | `update_memory` | `tool` | `memory.write` | Customer preferences, resolution outcome | `memory_updated: true` |

---

### 4.2 Support Ticket Triage & Assignment

**Purpose:** Automatically classify, prioritize, and route incoming support tickets.  
**Trigger:** Zendesk/Freshdesk/Jira new issue webhook  
**Inputs:** `ticket_id`, `title`, `description`, `reporter_email`, `attachments`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `classify` | `llm` | Triage prompt | `title`, `description` | `category` (bug/feature/question/billing/access), `component`, `severity`, `reproduction_steps`, `error_message` |
| 2 | `customer_context` | `tool` | `crm.get_customer` + `support.get_history` | `reporter_email` | `account_tier`, `open_tickets`, `previous_tickets`, `entitlements` |
| 3 | `knowledge_match` | `rag` | `knowledge-base` + `past-tickets` collections | `description`, `error_message` | `known_solutions` list, `similar_resolved_tickets` |
| 4 | `priority_score` | `llm` | Priority calculation prompt | `severity`, `account_tier`, `known_solution_exists`, `previous_tickets_count` | `priority` (P1/P2/P3/P4), `sla_hours`, `escalation_required: bool` |
| 5 | `assign_team` | `llm` | Assignment prompt | `category`, `component`, `skills_matrix` | `assigned_team`, `assigned_agent` (optional), `assignment_reason` |
| 6 | `draft_acknowledgment` | `llm` | Ack email prompt | `ticket_id`, `category`, `sla_hours`, `known_solution` (if available) | `ack_email_draft` |
| 7 | `update_ticket` | `tool` | `support.update_ticket` | `ticket_id`, `priority`, `assigned_team`, `category`, `tags` | `updated: true` |
| 8 | `send_acknowledgment` | `tool` | `email.send` | `reporter_email`, `ack_email_draft`, `ticket_id` | `sent: true` |
| 9 | `escalation_check` | `conditional` | — | `priority`, `escalation_required`, `account_tier` | Branch: → `notify_manager` if P1 or escalation required; → complete |
| 10 | `notify_manager` | `tool` | `slack.send_message` | Support manager DM, ticket details | `sent: true` |

---

### 4.3 Customer Churn Prediction & Outreach

**Purpose:** Identify at-risk customers and trigger personalized retention outreach before they churn.  
**Trigger:** Daily schedule (2 AM) | Real-time event (cancellation intent detected)  
**Inputs:** `tenant_cohort` (all active customers), `analysis_date`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_usage_data` | `tool` | `analytics.get_product_usage` | `tenant_cohort`, `analysis_date`, lookback 90 days | `usage_metrics` per tenant: DAU, feature adoption, error rates, support tickets |
| 2 | `compute_health_scores` | `llm` + `rag` | Health scoring prompt + `churn-signals` collection | `usage_metrics` | `health_score` per tenant (0–100), `churn_risk` (low/medium/high/critical), `risk_factors` |
| 3 | `segment_at_risk` | `transform` | Filter where `churn_risk` ≥ medium | `health_scores` | `at_risk_tenants` list |
| 4 | `enrich_accounts` | `parallel` | `crm.get_customer` per tenant | `at_risk_tenants` | `enriched_accounts`: account_manager, contract_value, renewal_date, previous_issues |
| 5 | `personalize_outreach` | `llm` | Personalization prompt (per tenant) | `tenant_details`, `risk_factors`, `usage_metrics` | `outreach_message` (personalized), `recommended_actions` list, `offer` (if applicable) |
| 6 | `approval` | `hitl` | Role: `account_manager` | Outreach message, customer context | Approved / edited |
| 7 | `send_outreach` | `parallel` | — | `approved_messages` | |
| 7a | `send_email` | `tool` | `email.send` | `customer_email`, `personalized_message` | `sent: true` |
| 7b | `create_csm_task` | `tool` | `crm.create_task` | Account manager, follow-up task | `task_id` |
| 8 | `update_crm` | `tool` | `crm.update_health_score` | `tenant_id`, `health_score`, `risk_level`, `outreach_sent` | `updated: true` |
| 9 | `analytics_log` | `tool` | `analytics.record_event` | Churn intervention event per tenant | `events_logged` |

---

### 4.4 Contract Generation

**Purpose:** Automatically generate contracts from templates, populate with CRM data, and route for signature.  
**Trigger:** CRM opportunity stage change webhook | Sales team API call  
**Inputs:** `opportunity_id`, `contract_type` (MSA/SLA/NDA/SOW), `customer_id`, `deal_terms`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_deal_data` | `tool` | `crm.get_opportunity` | `opportunity_id` | `deal_value`, `products`, `terms`, `start_date`, `renewal_type`, `special_conditions` |
| 2 | `fetch_customer_data` | `tool` | `crm.get_customer` | `customer_id` | `company_name`, `registered_address`, `gst`, `contact_name`, `contact_email` |
| 3 | `select_template` | `rag` | `contract-templates` collection | `contract_type`, `deal_value`, `jurisdiction` | `template_id`, `template_content`, `required_clauses` |
| 4 | `populate_contract` | `llm` | Contract population prompt | `template_content`, `deal_data`, `customer_data` | `draft_contract` (full text), `populated_fields` map |
| 5 | `compliance_check` | `llm` + `rag` | Compliance prompt + `legal-requirements` collection | `draft_contract`, `jurisdiction`, `industry` | `compliance_issues` list, `missing_clauses` list, `risk_clauses` list |
| 6 | `legal_review` | `hitl` | Role: `legal_counsel` | Draft contract + compliance report | Approved / red-lined version |
| 7 | `generate_pdf` | `tool` | `pdf.generate` | `approved_contract` | `contract_pdf_url` |
| 8 | `send_for_signature` | `tool` | `esign.send` (DocuSign/SignDesk) | `contract_pdf_url`, `signatories` list | `esign_envelope_id`, `signing_url_customer`, `signing_url_company` |
| 9 | `update_crm` | `tool` | `crm.update_opportunity` | `opportunity_id`, status=`contract_sent`, `contract_url` | `updated: true` |
| 10 | `notify_sales` | `tool` | `slack.send_message` | Account executive DM, contract sent confirmation | `sent: true` |
| 11 | `archive` | `tool` | `storage.upload` + `crm.attach_document` | `contract_pdf_url`, `opportunity_id` | `document_id` |

---

## Section 5 — Document & Knowledge Processing

---

### 5.1 Legal Contract Analysis

**Purpose:** Extract, classify, and risk-assess clauses in incoming legal documents.  
**Trigger:** File upload | Email with attachment | API  
**Inputs:** `document_url`, `analysis_type` (full/clause-specific), `client_id`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `ingest` | `tool` | `ocr.extract_document` | `document_url` | `raw_text`, `page_count` |
| 2 | `segment_clauses` | `llm` | Clause segmentation prompt | `raw_text` | `clauses` list: each with `clause_number`, `heading`, `content`, `page_reference` |
| 3 | `classify_clauses` | `llm` + `rag` | Classification prompt + `legal-taxonomy` collection | `clauses` | `classified_clauses`: each tagged with `clause_type`, `standard_deviation_score` |
| 4 | `risk_assess` | `llm` + `rag` | Risk assessment prompt + `risk-clauses-library` collection | `classified_clauses` | `risk_findings` list: each with `clause_number`, `risk_level`, `risk_explanation`, `standard_language`, `recommendation` |
| 5 | `extract_obligations` | `llm` | Obligation extraction prompt | `clauses` | `obligations` list: `party`, `obligation`, `deadline`, `consequence_of_breach` |
| 6 | `extract_financials` | `llm` | Financial extraction prompt | `clauses` | `payment_terms`, `penalties`, `caps_on_liability`, `indemnification_limits` |
| 7 | `compliance_check` | `llm` + `rag` | Jurisdiction compliance + `applicable-laws` collection | `classified_clauses`, `jurisdiction` | `non_compliant_clauses` list, `required_additions` list |
| 8 | `generate_summary` | `llm` | Summary generation prompt | All analysis outputs | `executive_summary`, `key_risks` list, `recommended_negotiation_points`, `redline_suggestions` |
| 9 | `generate_report` | `tool` | `report.generate_pdf` | Full analysis, summary, redlines | `report_url` |
| 10 | `notify` | `tool` | `email.send` | `client_id`, `report_url`, summary | `sent: true` |

---

### 5.2 Meeting Minutes Generator

**Purpose:** Convert meeting recordings or transcripts into structured minutes with action items and follow-ups.  
**Trigger:** Calendar meeting end event | Manual upload of recording/transcript  
**Inputs:** `meeting_id`, `audio_url` (or `transcript_text`), `attendees` list, `agenda`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `transcribe` | `tool` | `audio.transcribe` (Whisper) | `audio_url` | `transcript_with_timestamps`, `speaker_segments` list |
| 2 | `diarize` | `llm` | Speaker assignment prompt | `transcript_with_timestamps`, `attendees` list | `transcript_with_speakers`: each segment assigned to attendee |
| 3 | `extract_decisions` | `llm` | Decision extraction prompt | `transcript_with_speakers`, `agenda` | `decisions_made` list: each with `decision`, `rationale`, `agreed_by` |
| 4 | `extract_action_items` | `llm` | Action item extraction prompt | `transcript_with_speakers` | `action_items` list: each with `task`, `owner`, `due_date`, `priority` |
| 5 | `extract_discussions` | `llm` | Discussion summary prompt | `transcript_with_speakers`, `agenda` | `agenda_item_summaries` list |
| 6 | `extract_blockers` | `llm` | Blocker extraction prompt | `transcript_with_speakers` | `blockers` list: each with `issue`, `owner`, `escalation_needed` |
| 7 | `compile_minutes` | `llm` | Minutes compilation prompt | All extractions, `attendees`, `meeting_date`, `duration` | `meeting_minutes_markdown` |
| 8 | `create_tasks` | `parallel` | — | `action_items` | |
| 8a | `create_jira_tasks` | `tool` | `jira.create_issue` (per action item) | `action_items` | `jira_ticket_ids` list |
| 8b | `send_calendar_reminders` | `tool` | `calendar.create_event` (per action item due date) | `action_items` | `event_ids` list |
| 9 | `publish` | `parallel` | — | `meeting_minutes_markdown` | |
| 9a | `post_confluence` | `tool` | `confluence.create_page` | `meeting_minutes_markdown` | `page_url` |
| 9b | `send_email` | `tool` | `email.send_bulk` | `attendees` list, `meeting_minutes_markdown` | `sent: true` |
| 9c | `post_slack` | `tool` | `slack.send_message` | Project channel, summary + Confluence link | `sent: true` |

---

### 5.3 Research Paper Digest

**Purpose:** Ingest academic papers and extract key insights, compare with existing knowledge, and generate digestible summaries.  
**Trigger:** File upload batch | URL list | RSS feed from arXiv/PubMed  
**Inputs:** `paper_urls` list (or `paper_files` list), `research_domain`, `knowledge_collection_id`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `ingest` | `parallel` | `ocr.extract_document` per paper | `paper_urls` | `papers` list: each with `raw_text`, `metadata` |
| 2 | `extract_structure` | `llm` (per paper) | Academic structure prompt | `raw_text` | `title`, `authors`, `abstract`, `methodology`, `findings`, `limitations`, `references`, `keywords` |
| 3 | `embed_and_index` | `tool` | `knowledge.ingest` | Structured content | `chunk_ids` added to `knowledge_collection_id` |
| 4 | `compare_existing` | `rag` | `knowledge_collection_id` | `findings`, `methodology` | `related_prior_work`, `contradicting_findings`, `confirming_findings`, `novelty_score` |
| 5 | `generate_digest` | `llm` | Digest generation prompt | `extracted_structure`, `comparison_results` | `lay_summary` (non-expert), `technical_summary`, `key_takeaways` list, `practical_implications` |
| 6 | `cross_paper_synthesis` | `llm` | Synthesis prompt (run after all papers) | All `digests` | `theme_clusters`, `consensus_findings`, `emerging_trends`, `research_gaps` |
| 7 | `publish` | `parallel` | — | Synthesis report | |
| 7a | `update_confluence` | `tool` | `confluence.create_page` | Research digest | `page_url` |
| 7b | `notify_team` | `tool` | `slack.send_message` | Research channel, key findings + link | `sent: true` |

---

## Section 6 — HR & People Operations

---

### 6.1 Job Application Screening

**Purpose:** Automatically screen job applications, score candidates, and schedule interviews for shortlisted ones.  
**Trigger:** New application webhook (ATS) | Email with resume  
**Inputs:** `job_id`, `candidate_id`, `resume_url`, `cover_letter`, `application_timestamp`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `parse_resume` | `tool` + `llm` | `ocr.extract_document` + extraction prompt | `resume_url` | `name`, `email`, `phone`, `education` list, `experience` list, `skills` list, `certifications` list |
| 2 | `fetch_job_requirements` | `tool` + `rag` | `ats.get_job` + `job-requirements` collection | `job_id` | `required_skills`, `preferred_skills`, `min_experience_years`, `education_requirement`, `role_summary` |
| 3 | `skills_match` | `llm` | Skills matching prompt | `parsed_resume`, `job_requirements` | `matched_skills` list, `missing_skills` list, `skills_match_score` (0–100) |
| 4 | `experience_assess` | `llm` | Experience assessment prompt | `experience` list, `role_summary`, `required_experience` | `relevant_experience_years`, `experience_quality_score`, `relevant_projects` list |
| 5 | `score_candidate` | `llm` | Scoring prompt | All assessment outputs, `cover_letter` | `overall_score`, `recommendation` (reject/maybe/shortlist/fast-track), `strengths` list, `concerns` list |
| 6 | `bias_check` | `llm` | Bias detection prompt | `score_rationale`, `recommendation` | `bias_flags` list, `decision_factors_are_job_related: bool` |
| 7 | `decision` | `conditional` | — | `overall_score`, `bias_flags` | Branch: → `auto_reject` if score < 40; → `maybe_pool` if score 40–60; → `schedule_screening` if score > 60; → `human_review` if bias_flags detected |
| 8a | `auto_reject` | `tool` | `email.send` | `candidate_email`, rejection email | `sent: true` |
| 8b | `schedule_screening` | `tool` | `calendar.schedule` | `candidate_email`, screening call, recruiter availability | `meeting_id`, `invite_sent: true` |
| 8c | `human_review` | `hitl` | Role: `recruiter` + `hr_manager` | Application + bias flag report | Human decision |
| 9 | `update_ats` | `tool` | `ats.update_candidate` | `candidate_id`, `status`, `score`, `notes` | `updated: true` |

---

### 6.2 Employee Offboarding

**Purpose:** Automate the complete offboarding process ensuring all access is revoked, knowledge is captured, and final payroll is processed.  
**Trigger:** HR system resignation acceptance event | Last working day calendar trigger  
**Inputs:** `employee_id`, `last_working_date`, `department`, `manager_id`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_access_inventory` | `tool` | `iam.get_employee_access` | `employee_id` | `systems_access` list, `tool_licenses` list, `cloud_permissions` list, `physical_access` list |
| 2 | `knowledge_transfer_plan` | `llm` | KT plan prompt | `employee_id`, `department`, `responsibilities` | `knowledge_transfer_items` list, `documentation_gaps` list, `suggested_successor` |
| 3 | `create_kt_tasks` | `tool` | `jira.create_issue` (per KT item) | `knowledge_transfer_items` | `jira_ticket_ids` list |
| 4 | `exit_interview` | `tool` | `survey.send` | `employee_email`, exit survey link | `survey_id` |
| 5 | `final_payroll` | `tool` | `payroll.compute_final` | `employee_id`, `last_working_date` | `final_salary`, `leave_encashment`, `gratuity`, `deductions` |
| 6 | `revoke_access` | `parallel` (on last working day trigger) | — | `systems_access` list | |
| 6a | `revoke_iam` | `tool` | `iam.disable_user` | `employee_id` | `iam_disabled: true` |
| 6b | `revoke_email` | `tool` | `gsuite.suspend_account` | `employee_email` | `email_suspended: true` |
| 6c | `revoke_cloud` | `tool` | `cloud.revoke_permissions` | `cloud_permissions` | `permissions_revoked: true` |
| 6d | `revoke_physical` | `tool` | `access_control.deactivate_card` | `employee_id` | `card_deactivated: true` |
| 6e | `revoke_licenses` | `tool` | `license_manager.release` | `tool_licenses` | `licenses_released` list |
| 7 | `final_settlement` | `tool` | `payroll.process_final` | `final_payroll`, `employee_bank_details` | `payment_reference`, `bank_credit_date` |
| 8 | `experience_letter` | `tool` + `llm` | `pdf.generate` + content prompt | `employee_id`, `tenure`, `designation` | `letter_url` |
| 9 | `notify_employee` | `tool` | `email.send` | `employee_personal_email`, `experience_letter`, `payment_confirmation` | `sent: true` |
| 10 | `update_hrms` | `tool` | `hrms.update_employee` | `employee_id`, status=`offboarded`, exit date | `updated: true` |

---

## Section 7 — Industry-Specific Workflows

---

### 7.1 Healthcare: Patient Onboarding

**Purpose:** Digital patient registration, insurance verification, and appointment scheduling.  
**Trigger:** Patient registration form submission | Hospital management system event  
**Inputs:** `patient_id`, `personal_details`, `insurance_card_url`, `referral_letter_url` (optional)

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `identity_verify` | `sub_workflow` | Workflow: `kyc-automation` | Aadhaar / PAN | `kyc_status`, `verified_name`, `verified_dob` |
| 2 | `parse_insurance_card` | `tool` + `llm` | `ocr.extract_document` + extraction prompt | `insurance_card_url` | `insurance_provider`, `policy_number`, `policy_type`, `coverage_details`, `expiry_date` |
| 3 | `insurance_verify` | `http` | Insurance Verification API | `policy_number`, `patient_dob` | `coverage_active: bool`, `copay_amount`, `deductible_remaining`, `pre_auth_required_for` list |
| 4 | `eligibility_check` | `llm` + `rag` | Eligibility prompt + `insurance-coverage-rules` | `coverage_details`, `appointment_type` | `covered: bool`, `coverage_percentage`, `patient_liability`, `pre_authorization_required` |
| 5 | `schedule_appointment` | `tool` | `hospital_system.find_slot` | `department`, `doctor_preference`, `urgency`, `patient_availability` | `available_slots` list |
| 6 | `confirm_appointment` | `hitl` (patient confirmation via SMS/email) | Patient confirms slot | `available_slots` | `selected_slot`, `appointment_id` |
| 7 | `pre_auth_request` | `conditional` | — | `pre_authorization_required` | Branch: → `send_pre_auth` if required; → `skip` |
| 7a | `send_pre_auth` | `tool` | `insurance.request_pre_auth` | `policy_number`, `procedure_codes`, `clinical_notes` | `pre_auth_reference`, `pre_auth_status` |
| 8 | `create_patient_record` | `tool` | `hms.create_patient` | `patient_details`, `insurance_details`, `appointment_id` | `patient_mrn` |
| 9 | `send_confirmations` | `parallel` | — | — | |
| 9a | `appointment_confirmation` | `tool` | `sms.send` + `email.send` | `patient_contact`, appointment details | `sent: true` |
| 9b | `preparation_instructions` | `tool` + `rag` | `email.send` + `patient-prep-guidelines` collection | `appointment_type` | Instructions sent |

---

### 7.2 E-commerce: Return Processing

**Purpose:** End-to-end return request handling from customer initiation through refund or replacement.  
**Trigger:** Return request API / app | Email with return intent  
**Inputs:** `order_id`, `customer_id`, `return_reason`, `product_condition` (customer stated), `evidence_images` list (optional)

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `fetch_order` | `tool` | `oms.get_order` | `order_id`, `customer_id` | `order_details`, `purchase_date`, `product_details`, `seller_id`, `return_window_expires` |
| 2 | `eligibility_check` | `llm` + `rag` | Eligibility prompt + `return-policy` collection | `return_reason`, `product_details`, `purchase_date`, `return_window_expires` | `eligible: bool`, `return_type` (refund/replacement/repair), `ineligible_reason` (if any) |
| 3 | `evidence_analysis` | `tool` + `llm` (if images provided) | `vision_parser.describe` + analysis prompt | `evidence_images` | `damage_confirmed: bool`, `damage_type`, `damage_severity`, `matches_stated_reason: bool` |
| 4 | `fraud_check` | `llm` + `rag` | Fraud detection prompt + `return-fraud-patterns` collection | `customer_id`, `order_details`, `return_history`, `evidence_analysis` | `fraud_risk_score`, `fraud_signals` list |
| 5 | `decision` | `conditional` | — | `eligible`, `fraud_risk_score`, `return_reason` | Branch: → `auto_approve` if eligible and fraud_risk < 0.3; → `agent_review` if fraud_risk 0.3–0.6; → `reject_flag` if not eligible or fraud_risk > 0.6 |
| 6a | `auto_approve` | `tool` | `oms.approve_return` + `logistics.schedule_pickup` | `order_id`, `return_type` | `return_id`, `pickup_date`, `shipping_label_url` |
| 6b | `agent_review` | `hitl` | Role: `support_agent` | Full context + fraud flags | Agent decision: approve/reject/request more info |
| 6c | `reject_flag` | `tool` | `oms.reject_return` | `order_id`, `reason` | `rejection_reference` |
| 7 | `notify_customer` | `tool` | `email.send` + `sms.send` | `customer_contact`, decision, pickup details / rejection reason | `sent: true` |
| 8 | `process_refund` | `conditional` | — | `return_type`, `item_received_condition` (post-return inspection) | Branch: → `full_refund` if refund approved and item condition ok; → `partial_refund` if item damaged; → `replacement_ship` if replacement requested |
| 8a | `full_refund` | `tool` | `payment.refund` | `order_id`, `full_amount` | `refund_id`, `refund_eta` |
| 8b | `partial_refund` | `tool` | `payment.refund` | `order_id`, `partial_amount`, `deduction_reason` | `refund_id`, `deduction_explanation` |
| 8c | `replacement_ship` | `tool` | `oms.create_replacement_order` + `logistics.schedule_delivery` | `product_id`, `customer_address` | `replacement_order_id`, `delivery_date` |
| 9 | `update_inventory` | `tool` | `inventory.return_item` | `product_id`, `condition`, `return_id` | `inventory_updated: true` |

---

### 7.3 Marketing: Campaign Automation

**Purpose:** End-to-end marketing campaign creation, approval, and deployment across channels.  
**Trigger:** Natural language ("Run Q4 campaign for enterprise customers") | Calendar schedule | Product launch event  
**Inputs:** `campaign_brief`, `target_segment`, `channels` list (email/SMS/push/social), `budget`, `launch_date`

| Step | ID | Type | Tool / Model | Input | Output |
|---|---|---|---|---|---|
| 1 | `audience_segment` | `tool` + `llm` | `crm.query_segment` + analysis prompt | `target_segment`, `filters` | `audience_list`, `audience_size`, `segment_characteristics` |
| 2 | `fetch_brand_guidelines` | `rag` | `brand-guidelines` collection | `campaign_brief` | `tone_guidelines`, `color_palette`, `approved_phrases`, `prohibited_phrases` |
| 3 | `generate_content` | `parallel` | — | `campaign_brief`, `audience_characteristics`, `brand_guidelines` | |
| 3a | `email_copy` | `llm` | Email copy prompt | All context | `subject_lines` (5 variants), `preview_text`, `email_body`, `cta_text` |
| 3b | `sms_copy` | `llm` | SMS copy prompt | All context | `sms_message` (160 chars), `unsubscribe_footer` |
| 3c | `social_copy` | `llm` | Social media prompt | All context | `linkedin_post`, `twitter_thread`, `instagram_caption` |
| 3d | `image_prompts` | `llm` | Image brief prompt | `campaign_brief`, `brand_guidelines` | `image_briefs` list for design team |
| 4 | `ab_test_setup` | `llm` | A/B test planning prompt | `email_copy`, `audience_size` | `test_variants` (subject line A vs B), `test_split`, `success_metric`, `test_duration` |
| 5 | `compliance_check` | `llm` + `rag` | Compliance prompt + `marketing-regulations` collection | `all_content`, `channels`, `target_geography` | `gdpr_compliant: bool`, `can_spam_compliant: bool`, `prohibited_claims` found, `required_disclosures` |
| 6 | `budget_validate` | `tool` | `finance.check_budget` | `campaign_id`, `budget_requested` | `budget_approved: bool`, `available_budget`, `cost_estimate` |
| 7 | `approval` | `hitl` | Role: `marketing_director` | Full campaign plan, all content, compliance report | Approved / edited |
| 8 | `schedule_deploy` | `parallel` | — | `launch_date`, `approved_content` | |
| 8a | `schedule_email` | `tool` | `email_platform.schedule_campaign` | `audience_list`, `email_content`, `ab_variants`, `launch_date` | `campaign_id_email`, `scheduled: true` |
| 8b | `schedule_sms` | `tool` | `sms_platform.schedule_campaign` | `audience_list`, `sms_content`, `launch_date` | `campaign_id_sms`, `scheduled: true` |
| 8c | `schedule_social` | `tool` | `social_manager.schedule_posts` | `social_content`, `channels`, `launch_date` | `post_ids` list |
| 9 | `monitor_setup` | `tool` | `analytics.create_campaign_dashboard` | `campaign_ids`, `success_metrics` | `dashboard_url` |
| 10 | `schedule_report` | `tool` | `workflow.schedule` | Report workflow, 48h after launch | `scheduled_report_run_id` |

---

## Appendix: Workflow Input/Output Contract

Every workflow in AgentVerse follows this standard contract:

```yaml
# Workflow standard contract
workflow_run:
  id: string                    # UUID
  workflow_id: string           # Template ID
  tenant_id: string             # Multi-tenant isolation
  trigger_type: webhook|schedule|api|nl
  trigger_payload: dict         # Raw trigger input
  status: pending|running|waiting_hitl|complete|failed
  started_at: datetime
  completed_at: datetime | null
  steps:
    - step_id: string
      step_name: string
      status: pending|running|complete|failed|skipped
      started_at: datetime
      completed_at: datetime | null
      input: dict               # {{inputs}} + {{steps.N.output}}
      output: dict              # Typed output per step spec
      tool_used: string | null
      llm_tokens_used: int | null
      cost_usd: float | null
      error: string | null
  outputs: dict                 # Final workflow-level outputs
  audit_trail:
    - timestamp: datetime
      event: string             # step_start|step_complete|hitl_sent|hitl_responded|tool_called
      actor: string             # system|human_actor_id
      data: dict
```

---

## Appendix: Sub-workflow Reuse Map

```
kyc-automation
  └── used by: merchant-onboarding
               loan-prescreening
               employee-background-check
               patient-onboarding

kyc-automation
  └── sub-step of: aml-screening (via customer profile)

invoice-processing
  └── used by: (future) vendor-payment-automation

sre-incident-response
  └── can trigger: release-notes-generator (post-resolution)
                  security-vulnerability-response (if security incident)
```
