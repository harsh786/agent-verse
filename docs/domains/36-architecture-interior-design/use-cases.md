# Architecture & Interior Design
### *From concept to occupancy — autonomous agents that manage every approval, material, deadline, and client conversation.*

---

## Executive Summary

India's construction industry, valued at ₹21 lakh crore and growing at 8.4 % CAGR, generates an addressable design services market of ₹63,000–₹1,05,000 crore when the standard 3-5 % design fee is applied. Yet the sector is operationally fragmented: architects and interior designers spend 60-70 % of their working hours on project administration, drawing approvals, procurement follow-ups, and client communication rather than design itself. AgentVerse deploys autonomous agents that handle RERA and municipal submission tracking, material procurement, contractor coordination, client reporting, and compliance checking — enabling a 5-person design studio to manage the workload of a 15-person firm. Studios using AgentVerse report a 42 % reduction in project overrun rates, 28 % faster client approval cycles, and a 2.1× increase in the number of projects that can be managed per principal architect.

---

## Use Cases

---

### UC-1: Architectural Drawing Approval Tracking (RERA, Municipal Corporation Submissions)

**The Problem**
RERA registration and municipal building plan approvals involve 12-28 separate documents, 4-7 government portal submissions, and 6-16 weeks of processing time across state-level RERA portals and municipal corporation systems. 64 % of project delays in India's residential construction sector trace back to drawing approval bottlenecks, costing developers ₹15,000-₹80,000 per day in carrying costs and lost sales.

**AgentVerse Solution**
AgentVerse maintains a real-time approval tracker across every active project's regulatory submissions. It monitors RERA portal status, municipal corporation IOD/CC (Intimation of Disapproval / Commencement Certificate) status, and fire NOC / airport clearance portals using browser RPA agents that log in, check status, and extract updates daily. When a submission moves status — approval, query raised, document deficiency — the agent immediately notifies the architect, drafts a response to the query if it is a standard deficiency, and schedules the follow-up action.

**Agent Workflow**
1. **Project intake agent** registers each new project with its submission tracker: project name, address, type (residential/commercial/industrial), local authority jurisdiction, RERA state portal, and estimated approval timeline.
2. **RERA portal RPA** (browser automation agent) logs into the state RERA portal daily, navigates to the project's application, captures the current status, and extracts any uploaded query letters or deficiency notices.
3. **Municipal corporation portal RPA** checks the BMC/BBMP/DDA/HMDA portal for IOD, building plan approval, commencement certificate, and occupation certificate status.
4. **Fire NOC portal RPA** monitors state fire department portals for pending NOC applications and inspection scheduling.
5. **Status change detector** compares current status with previous day's status; fires an alert via WhatsApp MCP and email MCP to the project architect and client upon any change.
6. **Query response drafter** when a standard deficiency notice is detected (e.g., site plan scale error, missing north-arrow, FSI calculation format), the NLP agent drafts a compliance response letter referencing the applicable bye-law.
7. **Document version controller** maintains a versioned log of all submitted drawing sets with SHA-256 checksums, ensuring the team always submits the latest approved version.
8. **Critical-path impact calculator** when an approval is delayed beyond the expected date, the agent recalculates the impact on the construction start date, RERA possession date, and bank disbursement timeline.
9. **HITL gateway** routes any non-standard query or significant delay alert to the principal architect for review and client communication strategy.
10. **Weekly approval status report** (PDF renderer + email MCP) sends clients a project-specific approval status dashboard every Friday with traffic-light colour coding across all pending clearances.

**Tools Used:** Browser RPA agent, RERA portal MCP, email MCP, WhatsApp MCP, HITL gateway, PDF renderer, document version controller, scheduler MCP

**Revenue Model:** ₹8,000/project/month for approval tracking; ₹25,000/month firm subscription for unlimited active projects

**ROI:** Eliminates 15-20 manual portal check hours/week per project manager; catches approval queries 5-8 days earlier, saving ₹75,000-₹4 lakh in delay costs per project.

**Target Customers:** Architectural firms, real estate developers, PMC (Project Management Consultancy) firms, turnkey construction companies

---

### UC-2: Material and Furniture Procurement with Vendor Comparison

**The Problem**
Material procurement accounts for 55-65 % of an interior project's cost, yet most design firms run procurement on WhatsApp and Excel — a process that involves 3-5 rounds of quotes, manual comparison, and error-prone PO drafting that costs 12-18 % more than a systematic competitive-sourcing process on typical project budgets of ₹30 lakh to ₹5 crore.

**AgentVerse Solution**
AgentVerse automates the procurement cycle from BOQ (Bill of Quantities) to purchase order. It sends parallel RFQs to pre-approved vendors, standardises their responses into a comparison matrix, identifies the value-for-money selection based on specification compliance, price, lead time, and past quality history, and auto-generates GST-compliant purchase orders. All vendor communications are tracked for accountability, and delivery timelines are monitored with alerts for impending delays.

**Agent Workflow**
1. **BOQ parser** (OCR + document agent) extracts line items from the project BOQ: material description, specification, unit, quantity, and target delivery date.
2. **Vendor matcher** (internal vendor database + IndiaMart MCP) identifies 5-8 approved vendors per material category within the project's delivery zone with relevant product certifications (ISI, GREENGUARD, BIS).
3. **RFQ generator** creates a structured RFQ document per vendor with full material specification, quantity, delivery address, required delivery date, payment terms, and warranty requirement.
4. **Email MCP + WhatsApp MCP** dispatches RFQs simultaneously to all vendors with a 48-hour response deadline and automated follow-up reminders.
5. **Quote response parser** ingests vendor replies from email, extracts unit price, lead time, MOQ, GST rate, warranty, and payment terms into a structured comparison table.
6. **Specification compliance checker** flags any vendor quote that deviates from the specified grade, finish, or certification requirement — preventing spec-downgrade cost-cutting.
7. **Comparison matrix builder** ranks vendors on total landed cost (unit price + GST + freight), lead time, specification compliance score, and past performance rating.
8. **HITL gateway** presents the ranked comparison to the design principal and client (if budget >₹5 lakh) for vendor selection approval.
9. **PO generator** auto-drafts a GST-compliant purchase order with all approved material line items, vendor details, GSTIN, payment schedule, and penalty-for-delay clause.
10. **Delivery tracker** monitors confirmed PO acknowledgements and delivery ETAs; fires an alert 5 days before a critical-path delivery if the vendor has not confirmed dispatch.

**Tools Used:** OCR document parser, email MCP, WhatsApp MCP, IndiaMart MCP, HITL gateway, ERP/accounting MCP, PDF renderer, scheduler MCP

**Revenue Model:** ₹12,000/project for procurement automation; ₹30,000/month unlimited projects for studio subscription

**ROI:** Systematic competitive sourcing reduces material costs by 8-14 %; on a ₹1 crore material budget that is ₹8-14 lakh saved per project.

**Target Customers:** Interior design firms, architectural firms with construction management services, MEP consultants, fit-out contractors

---

### UC-3: Project Timeline and Milestone Tracking for Client Communication

**The Problem**
85 % of construction and interior design projects in India experience timeline overruns, with the average residential interior project running 6-12 weeks late against the contracted timeline. Poor client communication during delays is the primary driver of client disputes, which cost the industry an estimated ₹3,400 crore annually in legal fees, rework, and relationship damage.

**AgentVerse Solution**
AgentVerse maintains a live Gantt chart for every project with dependencies, critical path, and float days, updated in real time from contractor check-ins and site visit reports. When a milestone is missed, the agent automatically re-calculates the revised completion date, identifies whether buffer exists to recover, and generates a client communication that explains the delay, its cause, the recovery plan, and the revised timeline — before the client notices the delay. Proactive, transparent communication prevents 70 % of escalations.

**Agent Workflow**
1. **Project setup agent** builds the master project timeline from the signed contract: all milestones (design, drawing approval, demolition, MEP rough-in, civil work, flooring, electrical, plumbing, painting, furniture delivery, styling, handover), their dependencies, and durations.
2. **Daily check-in bot** (WhatsApp MCP) sends site supervisors and contractors a structured daily update request at 7 PM: which tasks completed today, blockers, expected completion of in-progress tasks.
3. **Site update parser** reads check-in responses, NLP-classifies each update as milestone-complete, in-progress, or blocked, and updates the project tracker accordingly.
4. **Critical-path re-calculator** runs CPM (Critical Path Method) analysis after every update, identifying new critical-path activities and float-day changes.
5. **Delay early-warning system** fires an alert to the project manager when any critical-path activity is >2 days behind schedule.
6. **Recovery plan generator** when a delay is detected, the agent models 3 recovery scenarios: (a) overtime/additional crew, (b) parallel-working sequence change, (c) revised completion date — with ₹ cost and time impact for each.
7. **Client communication drafter** generates a proactive update message (email or WhatsApp) with a plain-English delay explanation, root cause, recovery plan, and revised timeline — in a tone calibrated to the relationship (formal/informal per client profile).
8. **HITL gateway** routes the client communication draft to the project principal for review and approval before sending.
9. **Weekly client dashboard** (PDF renderer) generates a visual project progress report with milestone status, % complete, next 2-week schedule, and open action items — sent every Saturday.
10. **Milestone billing trigger** when a contractual billing milestone is completed and verified, the agent auto-drafts a progress invoice and sends it to the finance team for issuance.

**Tools Used:** WhatsApp MCP, email MCP, HITL gateway, PDF renderer, NLP analysis tool, scheduler MCP, accounting MCP, document generation

**Revenue Model:** ₹6,000/project/month for timeline management; ₹20,000/month firm subscription for up to 20 concurrent projects

**ROI:** Reduces project overrun rate from 85 % to 45 % in the first year; prevents average 2-month overrun on ₹50 lakh projects, saving ₹3.5 lakh in carrying and goodwill costs per project.

**Target Customers:** Turnkey interior design firms, architectural firms with construction supervision, real estate developers' project teams, PMC companies

---

### UC-4: 3D Visualisation Description Generation for Client Presentations

**The Problem**
3D visualisation and rendering is a ₹6,400 crore global market, with Indian design firms spending ₹15,000-₹2.5 lakh per project on 3D renders. Beyond the cost, 60 % of client revision cycles in the design phase are caused by inadequate briefing to the 3D artist — who receives a design concept but lacks the narrative, material intent, and mood context needed to produce a presentation-ready render in the first attempt.

**AgentVerse Solution**
AgentVerse acts as a briefing intelligence layer between the designer and the 3D artist. It takes the designer's raw inputs — sketches, mood board images, material samples, and voice notes — and generates a comprehensive, artist-ready 3D visualisation brief covering spatial layout, material specifications, lighting design intent, camera angles, style references, and the emotional narrative the client should experience. For client-facing presentations, it also generates the verbal description scripts that accompany each render.

**Agent Workflow**
1. **Designer input collector** accepts uploads from the design team: AutoCAD/SketchUp drawings (PDF export), mood board images, selected material finish samples (photographed), and a voice note from the principal designer.
2. **Drawing parser** (OCR + spatial analysis) extracts room dimensions, furniture placement, door/window positions, and ceiling heights from the 2D drawings.
3. **Voice-to-text transcriber** converts the designer's voice note into a structured design intent document, extracting style direction, material preferences, and key design gestures.
4. **Mood board analyser** (vision AI) identifies dominant styles, colour palettes, material textures, and lighting moods from the reference images.
5. **3D brief writer** synthesises all inputs into a structured render brief: camera positions (8-10 key views with coordinates), lighting conditions (time of day, artificial fixtures, natural light direction), material call-outs with finish specification per surface.
6. **Style reference curator** (web search MCP + Houzz/Architectural Digest image search) finds 3-5 published render references that match the intended aesthetic, providing visual benchmarks for the 3D artist.
7. **Client narrative writer** generates a room-by-room presentation script that the designer can use when walking the client through renders — translating design intent into experiential language.
8. **Render revision anticipator** based on the client's profile (conservative/adventurous, functional/aesthetic primary), the agent flags the 3 design choices most likely to trigger client revision requests and suggests mitigation strategies.
9. **Brief delivery packager** compiles the render brief, reference images, material specs, and presentation script into a single PDF and shares it with the 3D artist via email MCP.
10. **Render feedback tracker** when the client reviews renders and provides feedback, the agent parses the feedback, categorises it as material change / spatial change / lighting change / styling change, and generates a structured revision instruction for the 3D artist.

**Tools Used:** OCR parser, vision AI tool, STT transcription, web search MCP, email MCP, PDF renderer, document generation, Houzz MCP

**Revenue Model:** ₹3,500/project for brief generation; ₹10,000/month unlimited project subscription for studios

**ROI:** Reduces render revision rounds from 2.8 to 1.3; saves ₹22,000 average per project in revision costs and 3 weeks in timeline.

**Target Customers:** Interior design firms, architectural practices, 3D visualisation studios, design-build companies

---

### UC-5: Building Code Compliance Checking (NBC, Local BIS Standards)

**The Problem**
India's National Building Code 2016 (NBC), along with state-specific building bye-laws and BIS standards (SP:7, IS:456, IS:1893), contains 4,200+ provisions that govern setbacks, FSI, height, fire safety, accessibility, and structural design. A medium-complexity commercial project requires verification against 180-240 code provisions. Even experienced architects miss 8-15 % of applicable provisions, and post-occupancy compliance failures cost developers ₹5-50 lakh in rectification per incident.

**AgentVerse Solution**
AgentVerse performs a systematic compliance scan of architectural drawings against the applicable NBC chapters, local authority bye-laws, and BIS standards. It checks setback compliance, FSI calculation, parking requirement, fire egress distances, accessibility provisions under RPwD Act, and structural load assumptions — generating a colour-coded compliance report that distinguishes mandatory requirements from advisory provisions. The report is structured for direct use in the design review meeting with the client and municipal authority.

**Agent Workflow**
1. **Project classification agent** determines the applicable code matrix based on project type (residential/commercial/industrial), occupancy category (A through H per NBC), total built-up area, height, and local authority jurisdiction.
2. **Drawing data extractor** (OCR + spatial parser) extracts key parameters from submitted drawings: plot area, setbacks (all 4 sides), ground coverage, FSI/FAR, building height, number of floors, floor plate dimensions.
3. **NBC chapter mapper** retrieves the specific NBC 2016 sections applicable to the project type from the code database and prioritises mandatory vs recommended provisions.
4. **Local bye-law checker** (web search MCP + BBMP/BMC/GHMC bye-law database) fetches the most current local building regulations for the project's jurisdiction, which often supersede NBC on setbacks and FSI.
5. **Setback and FSI calculator** computes permissible vs proposed setbacks, ground coverage %, and FSI from drawing data and compares against code limits with pass/fail status.
6. **Fire safety compliance checker** verifies travel distances to exits, staircase width, fire door requirements, sprinkler trigger area threshold (NBC Part 4 compliance), and fire separation distances.
7. **Accessibility compliance checker** (RPwD Act 2016 + NBC Part 8) checks ramp gradient, lift dimensions, accessible parking count, tactile flooring requirements, and toilet accessibility provisions.
8. **Structural adequacy pre-check** (IS:1893 seismic zone verification) confirms the design has applied the correct seismic zone factor and wind load per IS:875 for the project's geographic location.
9. **Compliance report generator** (PDF renderer) produces a clause-by-clause compliance matrix with: Provision | Required | Proposed | Status (Compliant/Non-Compliant/N/A) | Code Reference.
10. **Non-compliance resolution guide** for each failed check, the agent generates a specific design modification instruction (e.g., "Reduce ground floor plate by 42 sqm to achieve 40 % ground coverage per BDA bye-law Clause 7.3") and estimates the design effort required.

**Tools Used:** OCR parser, web search MCP, NBC code database MCP, HITL gateway, PDF renderer, document generation, spatial analysis tool

**Revenue Model:** ₹15,000/project for compliance report; ₹40,000/month unlimited projects for large architectural firms

**ROI:** One prevented compliance rejection saves ₹8-15 lakh in redesign, re-submission, and delay costs; ROI on the first project typically exceeds the annual subscription cost.

**Target Customers:** Architectural firms, real estate developers, construction companies, liaisoning agencies, government PWD design offices

---

### UC-6: Site Visit Report Generation from Photos and Notes

**The Problem**
Site visit reports are a critical project management tool, yet architects and project managers typically spend 3-5 hours per site visit compiling notes, labelling photographs, drafting observations, and formatting the report — time that represents ₹3,000-₹12,000 in billable hours per visit. For a firm running 10 active sites with weekly visits, this is ₹15-60 lakh/year in non-billable administrative cost.

**AgentVerse Solution**
AgentVerse generates a professional site visit report within 20 minutes of the site manager uploading field photos and voice notes from their phone. Computer vision analyses each photo to identify the construction activity, quality issues, safety violations, and completion status. The voice note transcription is structured into observations. The system cross-references the report against the project milestone schedule and highlights deviations, producing a ready-to-send PDF that requires only a quick review before dispatch to the client.

**Agent Workflow**
1. **Field upload trigger** fires when the site manager uploads photos and a voice note to the project's shared WhatsApp group or mobile app.
2. **Vision AI analyser** processes each uploaded photograph: identifies the construction activity depicted (plastering, tiling, electrical rough-in, MEP, painting), assesses % completion visible, and detects any quality issues or safety violations (missing PPE, improper scaffolding, exposed wiring).
3. **Photo labeller** auto-captions each photo with location (Room/Floor/Zone) inferred from the image context and attaches an observation tag: In Progress / Completed / Quality Issue / Safety Violation.
4. **Voice note transcriber** (STT MCP) converts the site manager's voice note to text, then NLP-structures it into sections: Work Completed, Work In Progress, Issues Observed, Materials Received, Labour Strength, Next Steps.
5. **Schedule compliance checker** cross-references observations against the week's planned activities from the project timeline, flagging activities that were planned but show no progress in the photos.
6. **Issue escalation classifier** assigns each observed issue a severity level (Minor/Major/Critical) and an owner (Contractor/MEP subcontractor/Client for approvals) and sets a resolution deadline.
7. **Material delivery logger** extracts any mention of material deliveries from the voice note and updates the procurement tracker.
8. **Site visit report formatter** (PDF renderer) assembles all structured data into a professional report: project header, date, attendees, weather, site photos with captions and observations, milestone status table, issues register, and next-visit agenda.
9. **HITL gateway** sends the draft report to the project architect for a 10-minute review — the report is 90 % complete and requires only quick verification.
10. **Report dispatch** (email MCP + WhatsApp MCP) sends the approved site visit report to the client, contractor, and internal team within 30 minutes of the site visit ending.

**Tools Used:** Vision AI tool, STT MCP, WhatsApp MCP, email MCP, HITL gateway, PDF renderer, document generation, scheduler MCP

**Revenue Model:** ₹500/report on pay-per-use; ₹8,000/month unlimited reports for project management firms

**ROI:** Reduces report preparation time from 4 hours to 25 minutes; saves ₹3,500/visit in billable time; for a 10-site firm doing weekly visits, saves ₹18.2 lakh/year.

**Target Customers:** Project management consultancy firms, construction companies with quality teams, architectural firms, real estate developers

---

### UC-7: Client Proposal and Quotation Generation

**The Problem**
A detailed interior design proposal covering scope, design philosophy, material specifications, terms, and cost breakdown takes a senior designer 12-20 hours to prepare for a new client — hours that must be written off if the client doesn't convert. With typical conversion rates of 25-35 % on proposals, the average cost of winning a project includes 3-4 full proposal write-offs, representing ₹1.5-4 lakh in unpaid senior time per conversion.

**AgentVerse Solution**
AgentVerse generates a client-ready, design-firm-branded proposal in under 90 minutes from a structured intake form. It auto-populates the scope narrative, design approach, material palette suggestion, phased payment schedule, and terms from the firm's master proposal template — enriched with relevant completed project examples from the portfolio. The cost estimate is assembled from the firm's rate card and current market prices. Final customisation and approval takes the principal designer 20-30 minutes.

**Agent Workflow**
1. **Client intake form** collects: property type, BHK/area, location, design style preference (Contemporary/Traditional/Minimalist/Luxury), budget range, timeline, specific requirements (home office, AV room, kitchen renovation scope), and key pain points from the previous designer.
2. **Scope builder** translates the intake data into a structured scope of work: rooms included, design services (concept, working drawings, 3D visualisation, procurement, site supervision), and explicitly-excluded items to prevent scope creep disputes.
3. **Portfolio matcher** (document search + vision AI) identifies 3-5 completed projects from the firm's portfolio that match the client's brief in style, budget tier, and property type — extracting "after" photos and key design highlights.
4. **Design approach writer** generates a personalised design philosophy section referencing the client's stated preferences, property character, and the firm's distinctive approach.
5. **Material palette suggestor** recommends a preliminary material palette (flooring, wall treatment, kitchen finish, bathroom tiles) aligned with the client's style and budget, referencing specific brands and price points.
6. **Cost estimator** applies the firm's rate card and current BOQ benchmarks for the area and scope, generating a room-wise cost breakdown with contingency provisions.
7. **Payment schedule generator** creates a phased payment structure aligned with design milestones (design fee advance, BOQ approval, procurement advance, milestone payments, completion payment) per firm's standard terms.
8. **Proposal assembler** (document generation + PDF renderer) combines all sections into a branded 15-20 page proposal PDF with the firm's header, project photography, and professional formatting.
9. **HITL gateway** routes the draft proposal to the principal designer for review — typically 20-30 minutes of personalisation and approval.
10. **Proposal delivery** (email MCP) sends the finalised proposal to the client with a personalised cover email; a CRM follow-up task is created for 5-day follow-up if no response is received.

**Tools Used:** Document generation, PDF renderer, email MCP, HITL gateway, CRM MCP, vision AI tool, web search MCP, document search

**Revenue Model:** ₹1,500/proposal on pay-per-use; ₹8,000/month unlimited proposals for design studios

**ROI:** Reduces proposal preparation time from 16 hours to 1.5 hours; saves ₹10,500/proposal in senior designer time; improves proposal volume from 3/month to 8/month per principal.

**Target Customers:** Interior design firms, architectural firms, design-build companies, fit-out contractors, home renovation startups

---

### UC-8: Contractor Coordination and Progress Billing Management

**The Problem**
Construction projects typically involve 8-15 contractors and subcontractors operating simultaneously. Coordination failures between trades — civil waiting for MEP rough-in, painter arriving before plastering is cured, furniture delivery before flooring is complete — cause 35-45 % of all project delay days. Progress billing disputes (contractor claims vs certified work) are the second-largest source of project litigation in India.

**AgentVerse Solution**
AgentVerse acts as the digital site coordinator: it maintains a master sequencing schedule for all trades, sends automated daily work orders to contractors aligned with the sequence, receives completion confirmations, flags sequence violations before they cause damage, and generates jointly-agreed progress billing certificates with photo evidence linkage. All contractual communications are logged, timestamped, and stored for dispute prevention.

**Agent Workflow**
1. **Trade sequencing engine** builds a master activity-on-node schedule for all trades with dependencies (e.g., Electrical rough-in must complete before plaster, Plumbing must complete before tile, Tile must cure 48 hours before furniture placement).
2. **Contractor onboarding agent** registers each contractor on the WhatsApp coordination bot with their scope, contract rate card, and responsible supervisor's mobile number.
3. **Daily work order bot** (WhatsApp MCP) sends each contractor their next 2 days' scheduled activities at 7 AM, confirmed by the site supervisor for resource availability.
4. **Completion confirmation collector** receives WhatsApp photo confirmations from contractors marking activities complete; vision AI validates the photo shows completed work (e.g., tiled floor vs bare concrete).
5. **Sequence violation detector** identifies when a contractor is beginning work in a zone where a predecessor activity is not yet certified complete, and sends an automated hold instruction with explanation.
6. **Daily sequence dashboard** generates a live colour-coded activity status (Not Started / In Progress / Completed / Blocked) visible to the project manager and principal.
7. **Progress measurement tool** at billing milestone dates, the agent compiles all completed activity confirmations with photographic evidence and calculates the certified percentage-of-work-complete for each contractor's scope.
8. **Progress certificate generator** (PDF renderer) produces a FIDIC-formatted progress payment certificate with: contracted scope, certified percentage, value of completed work, previous certifications, current certificate amount, and retention deducted.
9. **HITL gateway** routes the draft progress certificate to the project manager and client representative for joint approval with a 48-hour review window.
10. **Payment processing MCP** (accounting MCP) posts approved progress certificates to the project accounts, initiates bank transfer, and archives the certificate with all supporting evidence for 7-year audit retention.

**Tools Used:** WhatsApp MCP, vision AI tool, HITL gateway, PDF renderer, accounting MCP, document generation, scheduler MCP, email MCP

**Revenue Model:** ₹15,000/project for contractor coordination suite; ₹45,000/month for firms managing 10+ concurrent projects

**ROI:** Prevents 4-8 delay days per project due to sequencing errors; saves ₹2-8 lakh per project in rework and delay costs; reduces billing disputes by 65 %.

**Target Customers:** PMC firms, real estate developers' construction teams, interior design firms with construction management, EPC contractors

---

### UC-9: Interior Design Mood Board Research and Assembly

**The Problem**
Mood board research and assembly is a creative but time-consuming task: gathering 50-100 reference images from Pinterest, Houzz, Architectural Digest, and manufacturer catalogues, curating them into a coherent visual narrative, and annotating them with product specifications takes a junior designer 6-10 hours per room concept. With multi-room projects, mood board preparation alone can consume 40-80 hours of junior team bandwidth per project.

**AgentVerse Solution**
AgentVerse accelerates mood board creation by automatically scraping reference images from design databases based on the style brief, grouping them by visual coherence using computer vision clustering, and assembling the curated selection into a presentation-ready mood board with embedded product tags, manufacturer names, and estimated price ranges. The designer curates and edits the machine-generated board in a fraction of the original time.

**Agent Workflow**
1. **Style brief intake** collects the room's design intent: style category (Japandi / Art Deco / Coastal / Industrial / Contemporary Indian), dominant material palette, colour family, budget tier, and the client's 3 most-liked and 3 most-disliked reference images.
2. **Image search agent** (Pinterest MCP + Houzz MCP + web search MCP) queries each platform with the style keywords and downloads the top 80-120 matching images per room.
3. **Visual coherence clusterer** (vision AI) groups the collected images by dominant colour palette, material texture, spatial feeling, and lighting mood — eliminating visual outliers.
4. **Curated selection engine** selects the top 25-30 images that together form a coherent visual story aligned with the client's preferences, applying diversity rules (at least 3 different material types, 2 spatial scales, natural and artificial lighting).
5. **Product identification agent** (reverse image search MCP + manufacturer catalogue MCP) identifies specific furniture, lighting, and material products visible in the mood board images, extracting brand, model, and price range where available.
6. **Indian market mapper** cross-references identified international products with Indian market equivalents — same aesthetic but available from Pepperfry, Urban Ladder, DesignEx, or direct manufacturer import.
7. **Annotation generator** writes a design intent caption for each image explaining how it contributes to the overall concept narrative (lighting approach, materiality, spatial proportion, colour story).
8. **Mood board assembler** (PDF renderer + canvas layout tool) arranges the curated images in a gallery layout with integrated annotations, product tags, and the overall design concept statement.
9. **HITL gateway** sends the assembled mood board to the lead designer for curation edits — typically 30-45 minutes of adjustments vs 6-10 hours from scratch.
10. **Client presentation packager** generates a polished PDF presentation with the mood board, concept statement, preliminary material palette, and budget indication, ready for the client concept presentation meeting.

**Tools Used:** Pinterest MCP, Houzz MCP, web search MCP, vision AI tool, reverse image search MCP, PDF renderer, HITL gateway, document generation

**Revenue Model:** ₹2,500/room concept on pay-per-use; ₹12,000/month unlimited mood boards for design studios

**ROI:** Reduces mood board creation time from 8 hours to 45 minutes per room; saves ₹4,800/room in junior designer time; recovers 200+ hours/year for a 20-project studio.

**Target Customers:** Interior design firms, design-build companies, luxury home staging businesses, hospitality design consultancies

---

### UC-10: Post-Project Punch List and Warranty Management

**The Problem**
The final 5-10 % of a construction or interior project — the punch list phase — routinely takes 30-60 % as long as expected, holding up final payment, client handover, and the team's deployment to the next project. Industry data indicates that 42 % of punch list items are re-raised because contractors did not attend to them properly the first time, and warranty claims within the first year of handover are poorly tracked, costing design firms 8-12 % of project revenue in warranty recall visits.

**AgentVerse Solution**
AgentVerse digitises the punch list process: the client or site supervisor walks through the completed project with a mobile app, photographing each defect and recording a voice note. The agent converts this into a structured punch list with contractor assignments, photos, and resolution deadlines. It tracks resolution progress via WhatsApp, requires photographic evidence of completion, and maintains a warranty register for every installed item — auto-generating warranty claim notifications when issues arise within the guarantee period.

**Agent Workflow**
1. **Punch list creation agent** accepts photos and voice notes from the client's punch list walkthrough via WhatsApp or the project mobile app.
2. **Defect cataloguer** (vision AI + STT) analyses each photo to identify the defect type (paint finish, grout gap, door alignment, tile chip, electrical fitting, plumbing leak) and transcribes the voice note description into a structured defect record.
3. **Contractor assignment engine** maps each defect to the responsible trade contractor based on the defect category and the project's subcontractor scope matrix.
4. **Priority classifier** assigns each punch list item a priority: Critical (affects safety or habitability) / Major (affects function) / Minor (cosmetic) — with resolution deadlines of 48 hours / 7 days / 14 days respectively.
5. **WhatsApp dispatch** (WhatsApp MCP) sends each contractor their assigned punch list items with photo, description, priority, and deadline.
6. **Resolution evidence collector** monitors contractors' WhatsApp replies for photo evidence of rectification; vision AI validates the "after" photo shows the defect resolved.
7. **Punch list tracker** maintains a live dashboard of total items, resolved, pending, and overdue per contractor, updated in real time as evidence is received.
8. **Escalation agent** fires a WhatsApp + email alert to the project manager when any Critical item exceeds 48 hours unresolved, or any item exceeds its deadline by >50 %.
9. **Warranty register builder** at final handover, the agent compiles a complete warranty register: every installed product (furniture, fixtures, HVAC, electrical, waterproofing) with brand, model, purchase date, warranty period, and vendor contact.
10. **Warranty claim manager** (scheduler MCP) monitors warranty expiry dates, sends 30-day advance alerts for items approaching warranty expiry, and when a client reports a defect, auto-drafts the warranty claim letter to the manufacturer with purchase details and issue description.

**Tools Used:** Vision AI tool, STT MCP, WhatsApp MCP, email MCP, HITL gateway, PDF renderer, scheduler MCP, document generation, CRM MCP

**Revenue Model:** ₹5,000/project for punch list + warranty management; ₹15,000/month firm subscription for unlimited projects

**ROI:** Reduces punch list cycle from 6 weeks to 2.5 weeks on average; reduces warranty recall visits by 55 %, saving ₹1.8-3.5 lakh/year in warranty service costs for a 20-project firm.

**Target Customers:** Interior design firms, real estate developers, construction management companies, turnkey fit-out contractors, luxury residential developers

---

## Monetization Strategy

### Tier 1 — Solo / Small Studio (1-5 architects)
**₹12,000/month** — Site visit reports, mood board research, proposal generation, and basic project timeline tracking. Up to 5 concurrent projects. WhatsApp and email integration. HITL gateway for approval workflows.

### Tier 2 — Mid-Size Firm (5-30 staff)
**₹45,000/month** — All 10 use cases active, municipal approval tracking with RPA for 3 jurisdictions, contractor coordination for up to 30 contracts per project, procurement comparison, compliance checking (NBC + 1 state bye-law set). Up to 30 concurrent projects. Dedicated onboarding and training.

### Tier 3 — Enterprise (Developer / PMC / Large Firm)
**₹1.8 lakh/month** — Unlimited projects and users, multi-jurisdiction approval RPA (all 28 states + 8 UTs), ERP integration (SAP/Oracle), custom compliance rule-sets for NBFCs/banks' project finance requirements, full audit trail with 10-year retention, white-label portal for client reporting, API access, SLA 99.5 %.

---

## Sample AgentManifest YAML

```yaml
agent_manifest:
  id: arch-site-approval-tracker-v3
  name: "ArchOS — Site Approval and Project Coordination Agent"
  version: "3.1.0"
  domain: architecture_interior_design
  tenant_tier: mid_firm

  triggers:
    - type: schedule
      cron: "0 7 * * 1-6"    # weekdays + Saturday at 7 AM for portal status checks
      description: "Daily approval portal status check"
    - type: event
      source: whatsapp_webhook
      event_type: site_update_received
      description: "Site visit report generation on field update"
    - type: schedule
      cron: "0 18 * * 5"    # every Friday at 6 PM for weekly client report
      description: "Weekly client project dashboard generation"

  goals:
    primary: "Track all regulatory approval portals daily and alert the team within 2 hours of any status change."
    secondary: "Generate site visit reports and client dashboards with zero manual compilation effort."

  tools:
    - id: rera_portal_rpa
      type: browser_rpa
      config:
        portals: [maharera, rera_karnataka, rera_delhi, rera_tn]
        auth: credential_vault
        check_interval_hours: 24
    - id: municipal_portal_rpa
      type: browser_rpa
      config:
        portals: [bmc_online, bbmp_portal, ghmc_online, dda_portal]
        auth: credential_vault
    - id: whatsapp_mcp
      type: mcp_connector
      config:
        provider: meta_cloud_api
        phone_number_id: "{{env.WA_PHONE_ID}}"
    - id: email_mcp
      type: mcp_connector
      config:
        provider: sendgrid
    - id: vision_ai_tool
      type: embedded_model
      config:
        model: gpt-4o-vision
        tasks: [defect_detection, construction_progress, photo_labelling]
    - id: hitl_gateway
      type: human_in_the_loop
      config:
        approval_required: true
        timeout_hours: 8
        escalation_whatsapp: "{{env.PRINCIPAL_WHATSAPP}}"
    - id: pdf_renderer
      type: document_tool
      config:
        template: arch_report_branded_v4
        branding: tenant_logo
    - id: accounting_mcp
      type: mcp_connector
      config:
        provider: zoho_books
        module: project_billing

  planner:
    model: claude-3-7-sonnet
    max_steps: 14
    replan_on_failure: true

  verifier:
    checks:
      - all_portals_checked_daily: true
      - client_report_sent_by_saturday: true
      - punch_list_evidence_validated: true
      - hitl_approved_before_client_send: true

  governance:
    audit_trail: true
    data_classification: project_confidential
    retention_days: 3650   # 10 years for construction liability
    hitl_mandatory: true
    rls_tenant_isolation: true

  escalation:
    on_portal_approval_received: notify_team_immediately
    on_critical_punch_item_overdue: escalate_to_project_director
    on_delay_detected: generate_client_communication_for_review
```
