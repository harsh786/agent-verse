# Karishma Product Integration Lead Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an ATS-compatible two-page Word resume that presents Karishma Singh as a product-focused merchant integration leader with consolidated, comprehensive skills and technologies.

**Architecture:** Create one two-page DOCX document with a single readable text flow, real heading styles, and no tables or floating content. Put the comprehensive skills and technologies section below the summary, create a product portfolio modeled on Harsh's key-programs structure, and remove duplicate skills content while retaining the complete PayU and Wipro history, credentials, and accomplishments supplied by the user.

**Tech Stack:** DOCX generation utility available in the workspace; standard Microsoft Word-compatible document structure.

---

### Task 1: Regenerate the Two-Page ATS Resume Document

**Files:**
- Create: `Karishma_Singh_Product_Integration_Lead_Resume.docx`
- Reference: `docs/superpowers/specs/2026-07-18-karishma-product-integration-lead-resume-design.md`

- [x] **Step 1: Draft fact-checked resume content**

Use the following summary and impact snapshot:

```text
Product-focused Merchant Integration Lead with 10+ years of experience delivering enterprise payment integrations, merchant onboarding, API and SDK implementation, and production go-lives. Experienced in launching card, UPI, subscription, cross-border, DCC, split-settlement, Native OTP, and passkey-enabled payment experiences. Partners with merchants, payment aggregators, product, engineering, QA, operations, and business teams to take integrations from solution design through UAT, deployment, and post-launch support.

IMPACT SNAPSHOT: 10+ years in payments | 9 named merchants | 4 payment partners | 10 payment capabilities | 5 team members led | 3 integration channels
```

The snapshot counts are derived only from the supplied resumes: nine named merchants including VIP Bags, four named payment partners, ten named payment capabilities, five team members, and web/mobile app/SDK channels. Include only supplied merchants, partners, roles, dates, products, tools, education, certifications, and achievements. Do not use scale, revenue, success-rate, TPS, or other impact metrics from Harsh Kumar's resume.

- [x] **Step 2: Generate the DOCX with ATS-safe structure**

Create a single-column document containing the sections below in this order:

```text
Header: Karishma Singh | Product Integration Lead - Payments & Merchant Solutions
Professional Summary
Impact Snapshot
Core Competencies
Professional Experience
Product Portfolio
Technical Skills
Education
Certifications
Achievements
```

Use a one-column, two-page layout with 10.5-point body text, 1.1 line spacing, 8-point space after bullets, and 14-point space before major sections. Use real Word heading styles, standard body text, bullet lists, and a simple contact line. Do not use tables, icons, text boxes, images, headers/footers for essential content, or multi-column layouts.

- [x] **Step 3: Inspect the generated document**

Run a DOCX text-extraction check and confirm it contains these exact terms:

```text
Pine Labs
PayU Payments Pvt. Ltd.
Flipkart
Juspay
Native OTP
Passkey
Magento
Core Java Programming with MySQL
9 named merchants
10 payment capabilities
```

Expected: all terms are present in the extracted text and the document opens as a valid DOCX.

- [x] **Step 4: Review content fidelity**

Verify the document includes:

```text
All three employers; all Pine Labs titles and dates; the supplied merchant and partner names; payment products; Shopify, WooCommerce, Magento, and OpenCart support; team leadership; technical skills; B.Tech; both certifications; and both achievements.
```

Expected: the resume is product-led, visibly less dense than the first version, includes the factual impact snapshot, and does not omit supplied material that is relevant to the target role.

### Task 2: Group the Skills Section

**Files:**
- Modify: `Karishma_Singh_Product_Integration_Lead_Resume.rtf`
- Modify: `Karishma_Singh_Product_Integration_Lead_Resume.docx`

- [x] **Step 1: Replace the flat skills list with grouped categories**

Use these ATS-readable categories and terms:

```text
Integration Channels: Web, Android, iOS, Flutter, Native SDKs
Commerce Platforms: Shopify, WooCommerce, Magento, OpenCart, WordPress, PrestaShop
Observability and Cloud: Grafana, Kibana, APM, AWS
Engineering and Delivery: Core Java, PHP (Basic), Oracle 11g, MySQL, Postman, GitHub, Jira, Confluence, Salesforce
```

- [x] **Step 2: Regenerate the Word document**

Retain the two-page layout, font size, spacing, and native page break before `KEY PROGRAMS / PRODUCTS`.

- [x] **Step 3: Verify skills content**

Confirm DOCX text extraction contains each of these terms:

```text
Android
iOS
Flutter
Native SDKs
WooCommerce
Shopify
OpenCart
Magento
Grafana
APM
AWS
```

### Task 3: Consolidate Skills and Technologies

**Files:**
- Modify: `Karishma_Singh_Product_Integration_Lead_Resume.rtf`
- Modify: `Karishma_Singh_Product_Integration_Lead_Resume.docx`

- [x] **Step 1: Replace the top technologies block**

Remove `IMPACT SNAPSHOT` and replace the top `TECHNOLOGIES` block with `SKILLS & TECHNOLOGIES`. Move the complete grouped skills content to this section.

- [x] **Step 2: Remove lower duplicate skills block**

Remove the lower `TECHNICAL SKILLS` heading and all grouped skill rows from page two. Preserve education, certifications, and achievements.

- [x] **Step 3: Regenerate and validate the document**

Confirm the document contains one `SKILLS & TECHNOLOGIES` heading, no `IMPACT SNAPSHOT` heading, and no `TECHNICAL SKILLS` heading. Preserve the native page break before `KEY PROGRAMS / PRODUCTS`.
