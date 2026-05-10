# Career Taxonomy Design — Norwegian Career Intelligence & sokr.online

**Specification only:** no SQL, no import scripts, no application code. This document defines the **foundational taxonomy system** for the Norwegian Career Intelligence Dataset and **sokr.online**.

**Related documents:** [Norwegian Career Intelligence Dataset — design](education-demand-intelligence-design.md) (data layers, confidence, temporal model, RAG expectations) · [Scoring and signal model](scoring-and-signal-model.md) (strength, weights, prioritization) · [MVP intelligence schema](minimum-viable-intelligence-schema.md) (tables referencing this taxonomy).

---

## 1. Purpose of the taxonomy layer

### Why taxonomy is the core semantic layer

Norwegian career intelligence mixes **numbers** (SSB), **surveys** (Studiebarometeret), **employer language** (Spor 2), **job ads** (NAV), and **candidate language** (CVs, LinkedIn). Without a shared vocabulary, those streams cannot be joined, compared, or explained. The **taxonomy layer** is the agreed **semantic spine**: stable codes, human-readable labels, and rules for how raw text and statistics map onto the same concepts.

### Why taxonomy must be defined before SQL normalization

Physical tables (`job_ads`, `education_programs`, `gap_signals`, etc.) need **foreign keys or controlled codes** for roles, competencies, industries, evidence types, and selection steps. If SQL is written first, every schema change becomes a migration when the taxonomy shifts. Defining taxonomies **first** (even as v0 lists + governance rules) lets you:

- Name tables and enums once, aligned to meaning.  
- Avoid silent relabeling when marketing terms change (“DevOps engineer” vs “platform engineer”).  
- Keep **RAG chunks** and **analytics** aligned to the same IDs.

### Why the system depends on shared concepts across domains

| Domain | What taxonomy enables |
|--------|------------------------|
| **Education supply** | Map program outcomes and learning goals to **competencies** and **role families** employers care about. |
| **Employer demand** | Map trainee criteria and selection steps to **selection methods**, **competencies**, and **role families**. |
| **Job ads (NAV)** | Normalize noisy **titles** and text to **roles**, **industries**, **competencies**, and **employer types**. |
| **Candidate profiles** | Same codes for CV bullets, LinkedIn, and interview prep — comparable to market. |
| **Recommendations** | Trigger rules typed in a **recommendation taxonomy** (e.g. “improve evidence”) tied to **gaps** and **evidence types**. |
| **RAG retrieval** | Filters and citations use **taxonomy IDs + labels** so answers are explainable (“this maps to `role_family: consulting`”). |

### The bridge between structured data, signals, AI, and candidates

```text
Structured data (tables) ──► taxonomy codes on rows
Extracted signals (NLP, rules) ──► provisional codes + confidence
AI reasoning (LLM) ──► constrained to allowed labels + synonyms
Candidate recommendations ──► actions typed in recommendation taxonomy
```

The taxonomy is the **contract** between engineers, domain experts, and models: humans curate meaning; models propose mappings; governance resolves conflicts (see §11).

---

## 2. Core taxonomy principles

| Principle | Operational meaning |
|-----------|----------------------|
| **Evidence over claims** | Every classification should be traceable to **source + method**; “says they are strategic” ≠ “demonstrated strategy delivery” unless evidence type supports it. |
| **Explainable classifications** | Each code has a **definition**, **examples**, and **non-examples**; UI and RAG can show “why this tag.” |
| **Norwegian labor market first** | Primary labels **nb-NO**; English titles (common in NAV/tech) map via **synonym tables**, not duplicate competing trees. |
| **Human-readable labels** | Stable `slug` / `code` for machines; `label_nb` (and optional `label_en`) for people and prompts. |
| **Ambiguity and overlap** | Allow **multi-label** roles/competencies where needed; record **confidence** and **primary vs secondary** flags. |
| **Multiple career paths** | Role taxonomy supports **transitions** and **ladders**; competencies link to steps, not only to one job title. |
| **Evolving terminology** | **Synonyms** and **deprecated** codes with `replaced_by`; new codes append rather than silently rename. |
| **Confidence-aware classification** | Mappings carry categories (e.g. explicit title, inferred from ad text, LLM-suggested) aligned with the dataset confidence model. |
| **Temporal awareness** | Codes have **valid_from / valid_to**; market aggregates use **period**; “hot skill 2024” does not overwrite historical series. |
| **Hierarchical but flexible** | Trees for navigation and reporting; **cross-links** (role ↔ industry norms) as edges, not only parent/child. |

---

## 3. Role taxonomy

### Top-level role families

Use a **single top-level enum** (conceptually) for `role_family` with **subroles** as children. NAV titles and employer jargon normalize **here first**, then link to competencies and industries.

**Guidance**

| Topic | Rule |
|-------|------|
| **Multi-role candidates** | Allow multiple `role_family` assignments with **weights** or **primary** + **secondary**; surface “T-shaped” profiles in UI. |
| **Hybrid roles** | Use **primary** family + **secondary** tag (e.g. `product` + `technology`) or a dedicated `hybrid_profile` note on the mapping row — avoid exploding combinatorial top-levels. |
| **Norwegian vs English titles** | Maintain **synonym table** (`title_variant` → `role_family` / `subrole`) for both languages; prefer employer-facing language in examples. |
| **NAV / job ad normalization** | Pipeline: clean title → token rules → synonym hit → else **low-confidence** bucket + human review queue; never force a single family on ambiguous “Koordinator” without context. |

### Role families (v0)

| `role_family` | Description | Example subroles | Example titles (NO/EN) | Related industries (examples) | Common transitions | Typical evidence signals |
|---------------|-------------|------------------|------------------------|--------------------------------|----------------------|---------------------------|
| **executive** | Enterprise-wide leadership, P&L, boards | C-suite, EVP, direktør | Konserndirektør, CEO, CFO | finance_banking, industrial, energy | consulting → executive; manager → executive | P&L scope, org size, board roles, tenure |
| **operations** | Core delivery, processes, supply execution | Drift, operasjoner, produksjon | Driftsleder, Head of Operations | manufacturing, logistics, energy | project_program → operations; engineering → operations | KPI ownership, process improvement, team size |
| **commercial** | Revenue-facing outside pure marketing | Sales, account management, BD | Key Account Manager, Selger | retail, SaaS, industrial | consulting → commercial; customer_success ↔ commercial | Quota, pipeline, contract value |
| **finance** | Accounting, controlling, treasury, IR | FP&A, regnskap, risk | Controller, Financial Analyst | finance_banking, industrial, public_sector | consulting → finance; audit → finance | Certifications (CPA/revisor), closing cycles |
| **consulting** | Client advisory, implementation partners | Strategy, mgmt, IT consulting | Management Consultant, Rådgiver | consulting, SaaS (services-heavy) | analyst → consultant → manager | Case performance, client logos, billable model |
| **engineering** | Physical/engineering disciplines (non-software) | Civil, mechanical, marine | Sivilingeniør, Prosjektingeniør | construction, maritime, energy | technology ↔ engineering (hybrid common) | PE licensure discourse, project references |
| **technology** | Software, data, platforms, IT operations | Backend, data, security, SRE | Utvikler, Tech Lead, Data Engineer | SaaS, fintech, telecom, govtech | engineering → technology; consulting → technology | Repos, stack, incidents led, architecture |
| **public_administration** | State/county policy and administration | Saksbehandling, forvaltning | Seniorrådgiver, Avdelingsdirektør | public_sector, municipality | consulting → public; specialist → leader | Legal framework knowledge, case volume |
| **healthcare** | Clinical and health services delivery | Nurse, physician, therapist | Sykepleier, Lege | healthcare, healthtech | education → healthcare; research → clinical | Licenses, shifts, patient-facing hours |
| **education** | Teaching, academic, L&D | Lecturer, skole, HR L&D | Førsteamanuensis, Lærer | education, edtech | research → education | Pedagogy, curriculum design, student outcomes |
| **product** | Product management, ownership, discovery | PM, PO, product lead | Produktleder, Product Owner | SaaS, fintech, ecommerce | consulting → product; engineering → product | Roadmaps, metrics, stakeholder management |
| **people_hr** | HR, people ops, TA, ER | HRBP, rekrutterer | HR Business Partner | most industries | consulting → HR; operations → HR | Policies owned, headcount, ER cases (careful PII) |
| **legal** | Lawyers, compliance, privacy | Corporate law, advokat | Advokat, Compliance Officer | legal, finance_banking, public_sector | consulting → legal | Degree, bar (where applicable), matter types |
| **supply_chain** | Procurement, planning, logistics strategy | Innkjøp, demand planning | Innkjøpsleder, Supply Chain Manager | manufacturing, retail, logistics | operations ↔ supply_chain | Cost savings, supplier count, inventory metrics |
| **project_program** | PMO, program/portfolio delivery | Prosjektleder, programleder | Prosjektleder, PMO Lead | construction, energy, consulting | engineering → PM; operations → PM | Budget, milestone, risk registers |
| **customer_success** | Adoption, renewals, post-sales account growth | CSM, implementation | Customer Success Manager | SaaS, healthtech | commercial ↔ customer_success | NRR, health scores, onboarding |
| **marketing_communications** | Brand, growth, content, PR | Performance, brand, kommunikasjon | Markedssjef, Growth Marketer | retail, SaaS, media | consulting → marketing | Campaign metrics, channel mix |
| **research_analysis** | R&D, market research, insight | Forsker, analytiker | Forsker, Insight Analyst | research_institute, pharma-adjacent, consulting | education → research | Publications, patents, study design |
| **sustainability_esg** | Climate, ESG reporting, HSE strategy | Bærekraft, miljø | Sustainability Manager, HSE-leder | energy, industrial, finance_banking | engineering → ESG; consulting → ESG | Reporting frameworks (CSRD etc.), audits |
| **board_governance** | Non-exec, board committees, ownership governance | Styreleder, styremedlem | Styremedlem | cross-industry | executive → board; founder → board | Board mandates, committee roles |

---

## 4. Competency taxonomy

### Categories (orthogonal to role family)

Competencies are **skills and knowledge areas**, not job titles. One competency row can attach to many roles; roles require **bundles** of competencies.

| Category | Definition | Examples | CVs | Job ads | Interviews | Recommendations | Education outcomes |
|----------|------------|----------|-----|---------|------------|-----------------|-------------------|
| **hard_skills** | Teachable, testable methods/tools | SQL, BIM, IFRS, kirurgisk teknikk | Cert lines, tool list | Must-have bullets | Live coding, case calc | “Strengthen technical profile” | Course codes, lab hours |
| **soft_skills** | Interpersonal and self-management | Samarbeid, initiativ, stressmestring | Team bullets | “Teamplayer”, kultur | Behavioral questions | “Storytelling”, “interview readiness” | Peer assessment, reflection |
| **leadership_skills** | Leading people/initiatives | Coaching, prioritering, konfliktløsning | Ledet team på X | People leadership | Leadership scenarios | “Strengthen leadership profile” | Group projects, verv |
| **strategic_skills** | Framing, prioritization, portfolio | Strategi, forretningsforståelse | “Defined 3y roadmap” | Strategy ownership | Case, exec interview | “Role alignment”, case prep | Strategy courses, thesis |
| **analytical_skills** | Data, logic, structured problem solving | Excel/Python, statistikk | Models built | Analytics req | Tests, case | “Quantified results” | Methods courses |
| **operational_skills** | Execution, routines, quality | Lean, ISO-prosesser, saksbehandling | SLA hits | Drift | Process drill | “Operational skills” | Praksis, verktøykurs |
| **technical_skills** | Deep domain technique (incl. IT) | Cloud, CAD, lab methods | Stack, certs | Tech stack | Tech interview | “Technical profile”, certs | Lab, project |
| **communication_skills** | Written/oral/stakeholder | Presentasjon, forhandling | Reports, talks | Kommunikasjon | Presentation step | “LinkedIn”, “visibility” | Presentasjoner |
| **domain_knowledge** | Sector/function context | Offentlig forvaltning, shipping | Sector projects | Industry exp | Domain questions | “Industry alignment” | Fagspesialisering |
| **regulatory_knowledge** | Laws, standards, compliance | GDPR, byggeregler, legemiddel | Compliance projects | Regulatory must | Compliance screen | Cert + evidence | Jus/HSE modules |
| **commercial_skills** | Pricing, contracts, margin | Tilbudsarbeid, forhandling | Won deals | Commercial acumen | Sales case | “Commercial alignment” | Bedriftsøkonomi |
| **digital_skills** | Digital collaboration, low-code, AI literacy | M365, RPA, prompt engineering | Digital tools | “Digital kompetanse” | Tooling conversation | “Digital skills” | Digital eksamen |

### Normalization, synonyms, overlap, emerging/declining

| Topic | Practice |
|-------|----------|
| **Competency normalization** | Canonical `competency_id` + `aliases` (nb/en/no slang) + optional ESCO/SFIA crosswalk **later**; v0 can be internal IDs with documented definitions. |
| **Synonym handling** | Table-driven: “PowerBI”, “Power BI”, “PBI” → one node; log **source** of first use. |
| **Overlapping competencies** | Allow **parent/child** (Python ⊂ programming) and **related_to** edges; scoring uses **dedupe rules** (don’t double-count children if parent scored). |
| **Emerging** | New nodes with `status=emerging`, lower default confidence until frequency or expert sign-off. |
| **Declining** | `status=declining` + `replaced_by`; historical ads keep old codes for time-series integrity. |

**Signals in text:** NAV and employer text map to competencies with **confidence tier** (explicit bullet > inferred keyword > LLM). Education maps via learning outcomes and course keywords with **weaker default** unless validated.

---

## 5. Evidence taxonomy

### Purpose

This taxonomy types **artifacts that prove capability** for recommendations, gaps, and CV/RAG grounding. It complements the dataset-level “confidence category” — here we type **what** was observed.

### Evidence types

| Type | Description | Evidence strength | Reliability / weaknesses | Typical use | Extraction |
|------|-------------|--------------------|---------------------------|-------------|------------|
| **degree** | Completed formal level/field | High when verified | Diploma fraud rare but claims unverified on CV weak | Education gaps, bar roles | Registry / self-report |
| **certification** | Vendor/professional cert | High if verifiable | Expiry, brain dumps | Tech, finance, HSE | Cert ID, issuer |
| **project** | Scoped delivery with outcome | Medium–high | Scope inflation | All channels | Bullet parsing |
| **measurable_result** | Quantified impact | High | Context missing | CV, ads as “bonus” | NLP + validation |
| **leadership_role** | Formal lead responsibility | Medium–high | Title ≠ leadership | Senior moves | Title + duties |
| **volunteer_work** | Unpaid roles | Medium | Less standardized | Culture fit, narrative | Self-report |
| **board_role** | Styre/utvalg | High for listed mandates | Private boards opaque | Governance paths | CV, Brønnøysund |
| **side_project** | GitHub, app, hobby business | Medium | Quality varies | Tech, product | Links |
| **publication** | Paper, report, whitepaper | High in academia | Less in all roles | Research, policy | ORCID, links |
| **presentation** | Talks, webinars | Medium | Self-claimed | Sales, research | Program, video |
| **recommendation** | Reference letter / LinkedIn rec | Medium | Bias, soft | Hiring trust | Manual |
| **network_signal** | Alumni, intros, event presence | Low–medium | Privacy, causality weak | Networking strategy | Inferred, careful |
| **internship** | Structured internship | Medium–high | Short duration | Early career | Employer confirm |
| **trainee_program** | Formal graduate program | High | Competitive selection | Spor 2 alignment | Employer lists |
| **portfolio** | Design/code/writing samples | Medium–high | Subjective | Creative, tech | URL |
| **work_sample** | Take-home, blind sample | High for skill | NDA limits | Hiring, prep | Employer process |
| **award** | Prize, ranking | Medium | Vanity metrics | CV marketing | CV |
| **public_speaking** | Conference, media | Medium | Rare for juniors | Exec, marketing | Program |
| **mentoring** | Formal mentor roles | Medium | Hard to verify | Leadership story | CV, org chart |
| **community_contribution** | OSS, meetups, frivillig sektor | Medium | “Resume driven dev” risk | Tech culture | GitHub, boards |

### Cross-cutting rules

| Concept | Definition |
|---------|------------|
| **Explicit vs inferred** | Explicit = named artifact (degree, cert link). Inferred = “likely has X from employer title” — lower tier. |
| **Strong vs weak** | Strong = third-party verifiable or structured (trainee offer). Weak = self-report + network_signal. |
| **Evidence stacking** | Multiple weak pieces can support a **medium** narrative if **non-redundant** (project + measurable_result + peer validation). |
| **Evidence recency** | Apply **half-life rules** per type (tech project 3y vs board 10y). |
| **Evidence relevance** | Tag **target role_family** / **competency**; irrelevant gold stars don’t close gaps. |

---

## 6. Industry taxonomy

Norway-oriented **v0 sectors**. Map to NACE when possible **later**; v0 supports product filters and employer language. Each row: **description**, **typical role families**, **common competencies**, **common certifications**, **market characteristics**, **public/private**, **B2B/B2C/B2G**.

*(Abbreviated table — expand in implementation registry with official NACE bridges.)*

| Industry | Description | Typical role families | Common competencies | Common certs | Market characteristics | Public/private | B2x tendency |
|----------|-------------|----------------------|----------------------|--------------|------------------------|----------------|--------------|
| **energy** | Power, grid, utilities (broad) | operations, engineering, project_program | HSE, regulatory_knowledge | FSE, HMS | Capex cycles, regulation-heavy | mixed | B2B+B2G |
| **oil_gas** | Upstream/midstream/services | engineering, operations, commercial | Offshore safety, domain_knowledge | NORSOK-adjacent, compEX | Cyclical, cost focus | mostly private | B2B |
| **renewable_energy** | Wind, hydro, solar | engineering, project_program, sustainability_esg | Project delivery, regulatory | IEC/exam culture | Growth, subsidy sensitivity | mixed | B2B+B2G |
| **maritime** | Shipping, offshore, yards | engineering, operations, supply_chain | Technical_skills, regulatory | STCW (maritime roles) | International, cyclical | private | B2B |
| **aquaculture** | Fish farming, feed, tech | operations, engineering, sustainability_esg | Biology ops, HSE | Bransjesert | Biosecurity, export | private | B2B+B2C |
| **industrial** | Process industry, metals | engineering, operations | Lean, operational_skills | ISO, NDT | Capital intensive | private | B2B |
| **manufacturing** | Discrete manufacturing | operations, supply_chain, engineering | Lean, quality | ISO 9001 | Automation trend | private | B2B |
| **construction** | Bygg, anlegg | project_program, engineering, operations | HMS, contract | HMS-kort, ISO | Project-based, seasonal | mixed | B2B+B2G |
| **public_sector** | State agencies | public_administration, legal | regulatory_knowledge, analytical_skills | None universal | Stable, formal hiring | public | B2G |
| **municipality** | Kommuner | public_administration, healthcare, education | Service delivery, political context | Varied | Politically governed | public | B2C+B2G |
| **healthcare** | Hospitals, private care | healthcare, operations | Clinical, soft_skills | Autorisasjon | Workforce shortage | mixed | B2C+B2G |
| **education** | Schools, HE | education, people_hr | Pedagogy, communication | PPU etc. | Reform-driven | mostly public | B2C |
| **consulting** | Advisory firms | consulting, technology, strategy-adjacent | Analytical_skills, communication | None universal | Up/out or specialist | private | B2B |
| **finance_banking** | Banks, asset mgmt | finance, technology, risk | Regulatory, analytical | CFA, ACAMS pattern | Regulated, digital shift | private | B2B+B2C |
| **insurance** | P&C, life | finance, commercial, technology | Actuarial/analytical | Actuary path | Consolidation | private | B2C+B2B |
| **legal** | Law firms, legal dept | legal, consulting | Analytical, regulatory | Advokatbevilling | Partnership model | private | B2B |
| **retail** | Physical retail | commercial, operations, supply_chain | Commercial, operational | Food safety variants | Margin pressure | private | B2C |
| **ecommerce** | Netthandel | technology, marketing_communications, commercial | Digital_skills, data | Platform certs | Fast iteration | private | B2C |
| **logistics** | 3PL, freight | supply_chain, operations | Operational, analytical | ADR etc. | Cost + ESG | private | B2B |
| **transportation** | Aviation, rail, bus | operations, technology | Safety, scheduling | Driver/operator certs | Union + regulation | mixed | B2C+B2G |
| **telecom** | Telenor-style ecosystem | technology, commercial, project_program | Networks, security | Vendor certs | Capex + regulation | mixed | B2B+B2C |
| **SaaS** | Software product cos | technology, product, customer_success | Cloud, product practices | Cloud certs | NRR-driven | private | B2B |
| **govtech** | Public digital suppliers | technology, public_administration | Security, procurement | ISO 27001 pattern | Tender-heavy | mixed | B2G |
| **fintech** | Payments, lending tech | technology, finance, product | Security, regulatory | PCI-adjacent knowledge | Regulated | private | B2B+B2C |
| **healthtech** | Digital health | technology, healthcare, product | Regulatory_knowledge, domain | ISO 13485 pattern | Clinical validation | mixed | B2B+B2G |
| **edtech** | Learning platforms | technology, product, education | Pedagogy + product | Varied | Procurement cycles | mixed | B2B+B2G |
| **climatech** | Carbon, climate software/services | sustainability_esg, technology | Data, domain | Emerging stack | Policy-driven demand | mixed | B2B |
| **media** | Publishing, broadcast | marketing_communications, technology | Content, digital | Varied | Ad market volatility | private | B2B+B2C |
| **nonprofit** | NGOs, foundations | operations, fundraising-adjacent commercial | Mission, grants | Rare formal | Resource constrained | NGO | B2C+B2G grants |
| **defense** | Forsvarsindustri | engineering, technology, project_program | Security clearance context | Security cleared roles | Regulated, long cycles | mixed | B2G |
| **real_estate** | Eiendom, utvikling | commercial, finance, project_program | Deal, regulatory | Megler where relevant | Interest rate sensitive | mixed | B2B+B2C |
| **tourism** | Reiseliv | commercial, operations | Service, languages | None universal | Seasonal | private | B2C |
| **hospitality** | Hotell, restaurant | operations, commercial | Service excellence | HACCP pattern | Labor intensive | private | B2C |

---

## 7. Employer taxonomy

Employer **type** is orthogonal to **industry** (a scaleup can be fintech). Use for **hiring pattern priors** and **recommendation tone**, not stereotypes as facts.

| Type | Hiring patterns | Expected candidate signals | Typical selection methods | Expected documentation | Cultural tendencies | Pace / risk | Hierarchy |
|------|-----------------|---------------------------|---------------------------|------------------------|---------------------|-------------|-------------|
| **startup** | Fast, informal, generalists | Broad ownership, hustle | hiring_manager_interview, culture_interview, work_trial | CV + story + portfolio | High ambiguity, high trust | Fast, high variance | Flat |
| **scaleup** | Structured but growing TA | Metrics, scale experience | panel_interview, case_interview (some), recruiter_interview | CV + metrics + process | Process emerging | Fast-medium | Mix |
| **SME** | Owner/manager-led | Versatility, local network | hiring_manager_interview, reference_checks | Practical CV | Pragmatic | Medium | Often flat |
| **enterprise** | Process-heavy, many gates | Compliance, matrix | assessment_center, panel_interview, background_checks | Formal CV, compliance | Bureaucracy, clarity | Slower, lower variance | Layered |
| **consulting** | Case-heavy, up-or-out signals | Analytical + presence | case_interview, logic_test, peer_interview | CV + case performance | Feedback-rich | Intense | Steep pyramid |
| **public** | Law-of-hiring, transparency | Formalkompetanse, equality | panel_interview, presentation, knowledge tests | Diplomas, course hours | Rule-bound | Stable | Clear grades |
| **municipality** | Local service mission | Public service motivation | panel_interview, presentation | Education + practice | Political visibility | Stable | Hierarchical |
| **state_owned** | Hybrid market/regulation | Commercial + public norms | Mixed enterprise/public | Formal + metrics | Hybrid culture | Medium | Mixed |
| **NGO** | Mission fit, grants | Values, versatility | culture_interview, presentation | Motivation letter | Consensus | Resource tight | Flat-mid |
| **PE_backed** | Value creation pressure | EBITDA narrative | hiring_manager + case | Metrics-heavy CV | Performance | Fast | Variable |
| **family_owned** | Trust, longevity | Loyalty, breadth | hiring_manager_interview, reference_checks | Relationships | Informal norms | Medium | Personal |
| **cooperative** | Member logic in some orgs | Stakeholder orientation | panel_interview, culture | Community | Participatory | Medium | Flatter |
| **research_institute** | Research grants | Publications, methods | presentation, technical_assignment | CV + papers | Academic-adjacent | Project-based | Expert-led |
| **university** | Academic/admin | Teaching/research split | presentation, panel_interview | Degrees, teaching portfolio | Deliberate | Slow hiring | Academic hierarchy |
| **agency** | Staffing/interim | Adaptability | recruiter_interview, screening | CV speed, skills list | Client-driven | Fast rotation | Agency model |

---

## 8. Selection-method taxonomy

Methods type **Spor 2** process steps and **interview prep** content.

| Method | Description | What employers evaluate | Common failure points | Evidence to provide | Prep recommendations | Confidence notes |
|--------|-------------|-------------------------|----------------------|---------------------|------------------------|------------------|
| **screening** | CV/keyword filter | Fit vs minimum bar | Keyword mismatch | Clear skills, quantified results | Tailor CV to role taxonomy | High volume, low nuance |
| **recruiter_interview** | TA screen | Motivation, basics, logistics | Vague stories | Clear narrative, availability | STAR stories, salary homework | Standardized |
| **hiring_manager_interview** | Line manager depth | Role fit, experience depth | Too generic | Role-aligned wins | Map to job bullets | Core signal |
| **case_interview** | Structured business case | Structure, numeracy, judgment | Math panic, weak hypothesis | Practice cases, mental math | Case frameworks + Norwegian context | High validity in consulting |
| **technical_assignment** | Take-home or live code | Skill depth | Scope creep, no README | Clean repo, tests | Time-box, document assumptions | Strong for tech |
| **logic_test** | Deductive/numeric tests | Speed, accuracy | Practice gap | Practice tests | Timed drills | Culture-dependent validity |
| **personality_test** | Psychometric | Culture fit (claimed) | “Game the test” | Consistency | Answer honestly; know rights | Legal/ethics vary |
| **assessment_center** | Multi-exercise day | Combo skills | Fatigue, group dominance | Group balance, notes | Simulate group tasks | High cost for candidate |
| **presentation** | Prepared talk | Communication, structure | Overrunning | Clear storyline | Rehearse with timer | Observable |
| **panel_interview** | Multiple interviewers | Consistency under pressure | Addressing one person only | Eye contact, structure | Panel Q prep | Common in public |
| **peer_interview** | Colleague fit | Collaboration | Too casual or too stiff | Team examples | Read team profiles | Subjective |
| **culture_interview** | Values alignment | Norms, motivation | Clichés | Real examples | Research values page | Subjective |
| **reference_checks** | Third-party refs | Past performance | Bad ref surprise | Align with ref | Brief refs | GDPR-sensitive |
| **background_checks** | Criminal/credit/education verify | Risk | Discrepancies | Accurate CV | Disclose early | High reliability when official |
| **work_trial** | Paid/unpaid short trial | Real work fit | Underselling | Execution + attitude | Clarify expectations | Strong but access uneven |
| **portfolio_review** | Walkthrough of work | Taste, depth | Disorganized portfolio | Curated cases | Story per artifact | Strong for design/PM |

---

## 9. Recommendation taxonomy

Typed **actions** for sokr.online (maps to product modules and RAG “next steps”).

| Recommendation | When to trigger | Gaps addressed | Evidence required | Concrete actions | Confidence |
|----------------|-----------------|----------------|-------------------|------------------|------------|
| **improve_evidence** | Weak proof for true skills | evidence_gap, candidate-role | Projects, metrics, links | Add quantified bullets, ship portfolio | Must avoid fabricated metrics |
| **improve_positioning** | Skills exist but narrative unclear | overlap low despite skill | Role/industry alignment | Rewrite summary for target `role_family` | Use explicit job language |
| **strengthen_technical_profile** | Tech depth doubt | technical_skills gap | Certs, repos, assignments | Cert + open-source fixme | Prefer verifiable |
| **add_quantified_results** | Vague achievements | commercial/analytical display | Numbers in CV | Add %, kr, volume, time | Stats must match evidence tier |
| **strengthen_network_reach** | Isolated job seeker | network_signal weak | Events, alumni | 5 coffee chats, join meetup | Low causal confidence — disclose |
| **improve_interview_readiness** | Process known, prep low | selection-method mismatch | Mock interviews | Drill case/tech for mapped methods | Tie to employer taxonomy |
| **build_portfolio** | Role needs artifacts | portfolio/work_sample gap | URLs, samples | 2 case writeups + code sample | Quality > quantity |
| **improve_linkedin_credibility** | Low trust online | visibility, positioning | Headline aligned to role taxonomy | Keywords + featured section | Avoid buzzword stuffing |
| **strengthen_case_documentation** | Consulting path | case_interview risk | Written case logs | 3 practice cases debriefed | Consulting employers |
| **improve_role_alignment** | Wrong role_family emphasis | trajectory / role mismatch | Step-wise evidence | Pivot bullets to target family | Use transition table §3 |
| **improve_industry_alignment** | Domain doubt | industry_gap | Domain projects | Read sector report + 2 bullets | Cite sources in RAG |
| **gain_certification** | Explicit market requirement | certification_gap | Exam plan | Pick cert from ad frequency analysis | Time + cost transparent |
| **improve_visibility** | Good candidate, low discovery | opportunity | Speaking, OSS, posts | 1 public artifact/month | Long feedback loop |
| **strengthen_leadership_profile** | Next step needs people leadership | leadership_skills | Team size, mandates | Document people outcomes | Avoid inflated titles |
| **improve_storytelling** | Flat interviews | soft_skills + communication | STAR stories | Story bank by competency | Coachable |
| **improve_application_strategy** | Spray-and-pray | low conversion | Tailored apps | Employer-type specific kit | Use employer + selection taxonomies |

---

## 10. Taxonomy relationships

### How taxonomies connect (conceptual graph)

```text
role_family ──requires──► competency (many-to-many, weighted)
role_family ──common_in──► industry (many-to-many)
role_family ──transition_to──► role_family (directed, weighted, time-stamped)
employer_type ──biases──► selection_method (frequency priors, not laws)
evidence_type ──supports──► competency (mapping: what proves what)
selection_method ──surfaces──► competency (what gets tested)
recommendation ──closes──► gap_type (from dataset spec) using evidence + competency actions
candidate_stage ──scopes──► norms for overlap/gap (junior vs executive)
```

### Design choices

| Topic | Strategy |
|-------|----------|
| **Many-to-many** | Junction tables (conceptually): `role_competency`, `role_industry`, `employer_type_selection_method`, etc. |
| **Normalization** | One canonical node per meaning; all strings resolve through **synonym → id**. |
| **Hierarchy** | `parent_id` within role subroles, competency trees, industry rollups; **do not** duplicate meaning across levels without `is_aggregate` rules. |
| **Synonyms** | External aliases only point to **one** canonical id; conflicts go to **governance queue** (§11). |
| **Multilingual** | `label_nb` primary; `label_en` optional; NAV English titles hit **synonym table** before embedding. |
| **Confidence-aware** | Every junction can carry `confidence_category`, `valid_from`, `source` (align with main dataset spec). |

---

## 11. Taxonomy governance

| Area | Rule |
|------|------|
| **Versioning** | Semantic version on the whole bundle (e.g. `taxonomy-2026.1`); breaking changes bump major. |
| **Updates** | Changelog entry: who, why, affected codes; automated tests for “no orphan mappings.” |
| **Deprecation** | `deprecated_at`, `replaced_by_id`; old data **keeps** old codes for history. |
| **Emerging terminology** | Provisional codes with `status=provisional` until frequency or SME sign-off. |
| **Human review** | Queue for low-confidence auto-tags; SME batch review for new industries/skills clusters. |
| **AI-assisted classification** | LLM proposes `candidate_mapping` rows; **human** or **rule** promotes to production. |
| **Conflict resolution** | Two SMEs disagree → product owner + data owner decision logged. |
| **Auditability** | Every code change has ticket + timestamp; optional git-native YAML registry. |
| **Traceability** | Synonym “learned from NAV 2026-Q1” stored as provenance on alias row. |

---

## 12. Taxonomy usage in sokr.online

| Capability | Taxonomy role |
|------------|----------------|
| **Profile understanding** | Map user text to `role_family`, `competency`, `evidence_type` with confidence. |
| **Package builder** | Bundle artifacts by **recommendation** + **selection_method** for target employer type. |
| **CV generation** | Sections keyed to **evidence types** + **competencies**; wording from **role** templates. |
| **LinkedIn optimization** | Headline from **role_family** + **industry** synonyms; skills order by market frequency. |
| **Gap analysis** | Gaps expressed in **competency** + **evidence** dimensions, not vague text. |
| **Overlap analysis** | Overlap in shared **competency** and **role_family** adjacency. |
| **Recommendation engine** | Rules fire on **typed gaps** → **recommendation taxonomy** actions. |
| **Interview preparation** | Employer **type** + **selection_method** checklist. |
| **Networking strategy** | Lower confidence; tie to **evidence** cautions. |
| **RAG retrieval** | Filter chunks by `role_family_id`, `industry_id`, `period`; cite taxonomy labels in answers. |
| **Career trajectory** | Transitions on **role_family** graph + **stage**-scoped competency weights. |

---

## 13. Future expansion

| Direction | Notes |
|-----------|--------|
| **Sweden / Denmark** | Add `label_sv`, `label_da`; separate **labor law** and **cert** mappings; keep **Norway v1** frozen for regression. |
| **Multilingual** | More synonym rows; optional parallel embeddings per language. |
| **Embeddings / vector search** | Embeddings on **definitions + examples** per node; retrieval = filter by taxonomy then vector within subtree. |
| **Ontology graph** | RDF/OWL optional; start with relational + explicit `related_to` edges. |
| **Labor market forecasting** | Time-series on coded skills/industries; taxonomy stability is prerequisite. |
| **Transition prediction** | Probabilistic edges between `role_family` nodes; governed like any model output. |
| **Benchmarking** | Compare user profile vector to cohort percentiles **per industry/role**. |
| **Recruiter intelligence** | Separate ethical layer; anonymized aggregates only unless contracted. |
| **Learning recommendations** | Map **competency gaps** to courses via third-party catalogs with shared competency codes. |

---

## 14. Open questions

1. **NACE alignment:** full official mapping now vs phased (industry v0 internal slugs first)?  
2. **ESCO / SFIA:** import subsets vs build minimal Norwegian-first tree?  
3. **Role family count:** keep ~20 top-levels or split technology (data vs security vs SWE)?  
4. **Competency granularity:** how deep before diminishing returns (e.g. “Python” vs “pandas”)?  
5. **Peer labeling:** allow employers to suggest new synonyms — moderation workflow?  
6. **Sensitive attributes:** how taxonomy interacts with protected characteristics (avoid proxies in “culture” tags)?  
7. **Candidate PII in evidence:** what evidence types are user-only vs ingestible at scale?  
8. **Norwegian legal terms:** integrate offisiell norsk for public-sector roles?  
9. **Version pinning:** does sokr.online pin `taxonomy_version` per workspace for reproducible RAG?  
10. **Ownership:** single “taxonomy curator” role vs committee per domain (health vs tech)?

---

### Architecture summary

The proposed architecture is a **governed, versioned bundle** of linked taxonomies: **roles** (with transitions and NAV title normalization), **competencies** (multi-category, synonym-rich, time-aware), **evidence types** (proof for recommendations), **industries** (Norway-first, NACE-ready), **employer types** (hiring and culture priors), **selection methods** (interview prep bridge), and **recommendation types** (product actions). Relationships are **many-to-many** with **confidence and time** on edges. This layer sits **above** raw ingestion and **before** SQL normalization so identifiers, RAG metadata, and analytics stay aligned, explainable, and extensible for embeddings and international expansion later.
