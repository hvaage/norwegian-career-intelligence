# Scoring and Signal Model — Norwegian Career Intelligence & sokr.online

**Specification only:** no SQL, no import scripts, no application code. This document defines the **signal system**, **scoring logic**, **weighting**, **confidence handling**, **recommendation prioritization**, and **RAG retrieval priorities** for the Norwegian Career Intelligence Dataset and **sokr.online**.

**Related documents:** [Norwegian Career Intelligence — dataset design](education-demand-intelligence-design.md) · [Career taxonomy design](career-taxonomy-design.md) · [MVP intelligence schema](minimum-viable-intelligence-schema.md) (signal, analysis, and RAG tables).

---

## 1. Purpose of the scoring and signal layer

### Why signals are the core analytical unit

Raw rows (ads, survey tables, CV text) are **not** what the product reasons about directly. A **signal** is a **typed, scored interpretation**: “this profile shows competency X at strength S with confidence C from source Y for period P.” Signals are what feed **gaps**, **overlaps**, **readiness**, **recommendations**, and **RAG ranking** — so analytics stay comparable across NAV, Spor 2 employer pages, and candidate language.

### Why recommendations must be explainable

Candidates and regulators will ask **why** a suggestion appeared. Every recommendation should trace to **signals + rules + thresholds**, not to a black box. Explainability is operational: support tickets, A/B tests, and human review all need “because gap G at severity H from evidence E.”

### Why scoring must support multiple product surfaces

| Use case | What scoring provides |
|----------|----------------------|
| **Candidate analysis** | Aggregate signal picture by competency, role, trajectory |
| **Role matching** | Overlap scores vs target role family / market profile |
| **Gap analysis** | Typed gaps with severity and confidence |
| **Overlap analysis** | Partial matches, transfer paths |
| **Recommendation generation** | Priority queue from gap + overlap + readiness |
| **RAG prioritization** | Which chunks and facts surface first in context |
| **Trajectory analysis** | Next-step readiness and trajectory_blocker signals |

### Raw data vs interpreted signals

| Raw data | Interpreted signal |
|----------|-------------------|
| NAV JSON line for one ad | “Explicit requirement: Python” on `job_ad` with `confidence=explicit_requirement`, `strength=4` |
| Studiebarometeret row | “Supply signal: low satisfaction dimension D” with statistical backing |
| CV bullet text | “Evidence_signal: measurable_result” if quantified; else weaker |

### Evidence vs assumptions

- **Evidence:** observable artifact (cert, degree registry, quoted criterion, statistic).  
- **Assumption:** model fills a gap (“probably has leadership because title says Manager”) — must carry **lower confidence** and often **lower strength**.

### Confidence-aware scoring

A **large inferred gap** must not outweigh a **small explicit gap** in hiring reality. Confidence gates **severity caps**, **RAG inclusion**, and **recommendation tone** (suggest vs must-fix).

---

## 2. Signal types

Signal **type** describes *what kind of market or personal information* the value represents. It is orthogonal to **strength** (§3) and **confidence** (§4).

| Type | Definition | Examples | Typical sources | Confidence considerations | Use in recommendations | Use in RAG | False positives |
|------|------------|----------|-----------------|----------------------------|-------------------------|------------|-----------------|
| **hard_signal** | Discrete, checkable fact or explicit rule | “MSc required”, orgnr present, cert ID | NAV text, trainee PDF, Brønnøysund | High when quoted/structured | Drives blockers, certs | Cite as fact | OCR/parse errors |
| **soft_signal** | Interpretive fit, culture, motivation | “Team player”, “fast-paced” | Ads, culture interviews, reviews | Medium–low | Wording, story coaching | Lower rank | Stereotypes from reviews |
| **market_signal** | Aggregate labor-market behavior | Skill frequency in ads Q3 | NAV + SSB | Statistical confidence | Prioritize learning targets | “Market says…” | Sample bias (one industry) |
| **trajectory_signal** | Position on or toward a path | “Ready for step PM→Sr PM” | Outcomes data + profile | Often inferred | Sequencing, trajectory_blocker | Path narratives | Wrong path chosen |
| **selection_signal** | Hiring step or test type | Case interview stage | Spor 2, Universum process data | Explicit when from employer | Interview prep pack | Step-specific chunks | Outdated process description |
| **evidence_signal** | Proof artifact attached to person | Portfolio URL, cert | CV, LinkedIn, application | Varies by evidence type (§9) | improve_evidence triggers | Cite user-supplied | Fake links |
| **network_signal** | Referral, alumni, visibility proxies | “Intro from employee” | LinkedIn, self-report, events | Usually weak | Networking nudges | Careful wording | Vanity metrics |
| **risk_signal** | Mismatch or fragility flag | Public→private culture risk | Employer taxonomy + profile | Medium | Tone + expectation setting | Disclosure in advice | Overgeneralization |

### Examples by channel

| Channel | Example signal | Type | Notes |
|---------|----------------|------|--------|
| **Job ad (NAV)** | “3+ years Kotlin” | hard_signal + market_signal component when aggregated | Parse explicit vs fluff |
| **Candidate profile** | “Led team of 8” | evidence_signal + hard_signal | Verify in interview |
| **Trainee program** | “Minimum B grade average” | hard_signal, selection_signal | High priority in RAG |
| **Interview review** | “Heavy case math” | soft_signal + selection_signal | review_based confidence |
| **Education outcomes** | “80% employment within 6 mo” | market_signal / verified_statistical | Institution+year specific |
| **LinkedIn** | “Open to work” | soft_signal | candidate_claim tier |
| **CV** | “Increased revenue 12%” | evidence_signal | strength high if verifiable |
| **Application** | Tailored cover paragraph | soft_signal | quality subjective |

---

## 3. Signal strength model

Strength answers: **how strongly does this single observation support the claim?** (Not the same as sample-size confidence across many rows — that lives in **confidence** and **market_signal** aggregation.)

| Strength | Meaning |
|----------|---------|
| **1** | Weak indication (tone, hobby mention, single vague word) |
| **2** | Inferred (title-based, co-occurrence, model guess) |
| **3** | Repeated pattern (same signal across ≥2 independent contexts, e.g. two projects) |
| **4** | Explicit evidence (clear bullet, quoted criterion, named artifact) |
| **5** | Verified / statistically strong (official stat, verified cert, registry-backed degree) |

### Rules of thumb

| Situation | Strength behavior |
|-----------|-------------------|
| **When strength increases** | Add independent corroboration (second project, employer confirmation, official statistic); move vague → explicit wording. |
| **When strength decays** | Time passes (recency half-life per signal type); market terminology shifts; employer withdraws criterion. |
| **Repeated evidence** | Same bullet copy-pasted ≠ repeat; **independent** occurrences (two roles, exam + work) bump 2→3+. |
| **Confidence vs strength** | “Explicit in one shaky source” → high strength, **low confidence** if source is review-only. “Inferred from 10k ads” → lower per-row strength, **higher** statistical confidence at aggregate. |
| **Temporary vs durable** | “Trending keyword 2026” = temporary market_signal; “degree in law” = durable hard_signal with slower decay. |

### Contrasts (copy for product copy tests)

| Weak (1–2) | Strong (4–5) |
|------------|----------------|
| “Interested in leadership” | “Managed team of 15 for 2 years; performance cycles owned” |
| “Familiar with Excel” | “Advanced financial modeling certification + 4 models in audit client work” |
| “Likely relevant” (embedding neighbor) | “Explicit employer requirement in trainee criteria PDF” |

---

## 4. Confidence model

Confidence answers: **how trustworthy is the mapping or measurement?** Aligns with dataset spec categories; extended here for **scoring behavior**.

| Category | Definition | Reliability | Strengths | Weaknesses | Good for | RAG treatment | Recommendation treatment |
|----------|------------|-------------|-----------|------------|----------|---------------|--------------------------|
| **verified_statistical** | Official or audited aggregate | High | Reproducible, comparable | Lag, coarse grain | Market priors, “Norway says…” | **Boost** rank; always cite period | Benchmark gaps, learning market |
| **explicit_requirement** | Stated in ad/contract-like text | High for that employer | Actionable | May not generalize | Role-specific CV | High rank with employer scope | Tailoring, blockers |
| **explicit_selection_criterion** | Stated hiring rule/step | High | Interview prep | Can change yearly | Prep packs | High rank, date-stamp | “Must practice case” |
| **inferred_pattern** | Model/rules from co-occurrence | Medium | Fills sparse data | Can encode bias | Suggestions | Label as inferred | Softer language |
| **llm_extracted** | LLM proposed tag | Low–medium | Scale | Hallucination risk | Draft only | Down-rank; require citation to span | Never sole blocker |
| **review_based** | Glassdoor/Indeed themes | Low–medium | Rich texture | Selection bias | Culture prep | Tag source; aggregate | Avoid stereotypes |
| **candidate_claim** | Self-report, LinkedIn | Variable | Fresh, specific | Inflated | Discovery | Treat as claim | “Verify / quantify” |
| **weak_signal** | Thin n, old window, ambiguous | Low | Exploration | Misleading | Internal research only | Exclude or warn | Do not push as must-do |

### Cross-cutting mechanisms

| Mechanism | Meaning |
|-----------|---------|
| **Source reliability** | Registry field (see dataset design); multiplies or caps effective confidence. |
| **Confidence inheritance** | Child signal inherits **min(parent confidence)** unless upgraded by stronger evidence. |
| **Confidence degradation** | Stale data, superseded taxonomy mapping → downgrade category one step with audit log. |
| **Human verification** | Manual promotion of `llm_extracted` → `explicit_*` or `verified_*` for curated rows. |
| **AI extraction risk** | Cap contribution to **gap severity**; require second source for “critical.” |
| **Hallucination prevention** | RAG must not synthesize numbers; retrieval prefers chunks with **dataset_id** and **stat period**. |

### Quick examples

| Example | Typical confidence |
|---------|-------------------|
| NAV + SSB employment rate by field | verified_statistical |
| Trainee PDF: “minimum grade C” | explicit_selection_criterion |
| NIFU narrative report table | verified_statistical (if table) / inferred_pattern (if interpreted) |
| Glassdoor “tough case interviews” | review_based |
| LinkedIn headline “Strateg” | candidate_claim |
| AI-extracted “stakeholder management” from CV | llm_extracted |

---

## 5. Gap scoring model

A **gap** compares **target** (role, market, trajectory step) **signals** minus **candidate** **signals** (evidence + competency), adjusted by **confidence caps**.

### Gap types

| Gap | Definition | Input signals | Example calculation (illustrative) | Severity logic | Confidence handling | Recommendation implication |
|-----|------------|---------------|-----------------------------------|----------------|----------------------|----------------------------|
| **role_gap** | Missing role-family fit vs target | Role overlap vector | `target_role_profile − candidate_role_vector` | High if explicit title mismatch | Cap if profile inferred | improve_role_alignment |
| **evidence_gap** | Skill claimed or needed but **not proven** | evidence_signal weights (§9) | Need “measurable_result” for commercial claim; missing | Soft unless employer demands proof | Strong if trainee states portfolio | improve_evidence, build_portfolio |
| **certification_gap** | Market/employer requires cert C | hard_signal from ads + trainee | Freq(C in target market) high − candidate has C | Hard if explicit; soft if only pattern | Pattern = inferred_pattern | gain_certification |
| **process_gap** | Selection tests X; candidate never shows X | selection_signal vs evidence | Case stage present − case practice evidence | High for consulting | Explicit process > review | strengthen_case_documentation |
| **network_gap** | Referral-driven market; weak network_signal | market_signal + network_signal | Sector referral importance high − network index | Usually soft blocker | review_based market only → cap severity | strengthen_network_reach |
| **industry_gap** | Domain knowledge thin vs target industry | industry overlap + domain_knowledge | Low industry_overlap + low domain competencies | Medium | Statistical industry priors help | improve_industry_alignment |
| **trajectory_gap** | Missing enabler for **next** step | trajectory_signal | Next-step competency bundle − current | Can be critical for promotion path | Trajectory data often inferred | learning + evidence stack |
| **competency_gap** | Low competency match vs profile | competency vectors | Required bundle − demonstrated | Core vs nice-to-have weights | Inferred requirements down-weighted | strengthen_technical_profile, etc. |
| **experience_gap** | Tenure/seniority short vs norm | market_signal + hard_signal years | Norm years for role − candidate years | Often soft unless explicit “5+ yrs” | NAV explicit years = harder | narrative + role targeting |

### Concepts

| Concept | Meaning |
|---------|---------|
| **Hard blocker** | Explicit unmet requirement (legal bar, degree gate, must-have cert). Severity floor high. |
| **Soft blocker** | Lower hire probability without fix (culture fit narrative, network). |
| **Missing evidence vs missing competency** | Candidate **has** skill but no proof → evidence_gap; **lacks** skill → competency_gap. |
| **Explicit vs inferred gap** | Explicit from trainee/ad text vs inferred from market frequency — different confidence caps. |
| **Critical gap** | Blocks readiness stage progression (§8); few per profile, reviewed rules. |
| **Hidden gap** | High market_importance but low candidate documentation (see §10 white spots). |
| **Market-specific gap** | e.g. Norwegian public-law formality for public_sector targets. |

### Practical vignettes

| Scenario | Dominant gaps |
|----------|----------------|
| **Graduate → consulting** | process_gap (case), evidence_gap (quantified school projects), trajectory_gap (first step) |
| **Engineer → product** | competency_gap (discovery, stakeholders), role_gap, evidence_gap (product outcomes) |
| **Public → startup** | risk_signal + culture prep; evidence_gap (pace/metrics), network_gap |
| **Missing portfolio** | evidence_gap for design/PM/tech public profiles |
| **Missing network reach** | network_gap in referral-heavy niche (finance, PE) — never sole “blame” |

---

## 6. Overlap scoring model

Overlap is **similarity or containment** between two signal sets (candidate vs role, candidate vs market, A vs B industries), in **[0,1]** or percentile, always with **confidence metadata**.

| Overlap | Definition | Calculation logic (conceptual) | Weighting | Confidence effects | Example |
|---------|------------|----------------------------------|------------|---------------------|---------|
| **competency_overlap** | Jaccard or weighted cosine on competency nodes | Weight core competencies higher | Role-specific weight table | Down-weight llm_extracted nodes | PM shares “analytics” with consultant |
| **role_overlap** | Similarity of role family vectors / graph distance | Short path = higher overlap | Adjacent families bonus | Title-inferred roles lower confidence | Consultant → strategy |
| **industry_overlap** | Tree distance + adjacency edges | 1 hop retail↔ecommerce > random | Industry taxonomy § career-tax | Statistical priors stabilize | Finance → fintech |
| **evidence_overlap** | Same **evidence types** proving needed bundle | Count weighted evidence matches | §9 weights | Stack independent proofs | Two projects showing same skill |
| **employer_overlap** | Target employer similar to past employers | Type + industry + size bucket | Employer taxonomy | Thin data → widen bucket | SME ops → scaleup ops |
| **trajectory_overlap** | Candidate position aligns with path to target | Match to path steps completed | Next-step weights | Inferred transitions weaker | PM → operations AD |
| **network_overlap** | Shared schools, employers, events (if ethical) | Conservative scoring | Low default weight | High privacy risk | Use opt-in data only |
| **culture_overlap** | Values/motivation fit (weak) | Keywords + review themes | **Low cap** | review_based heavy discount | Startup ↔ entrepreneurial signals |

### Advanced notions

| Topic | Handling |
|-------|----------|
| **Partial overlap** | Report **band** (e.g. 0.45–0.55) when confidence mixed. |
| **Transferable competencies** | Map via `related_to` competency edges; partial credit with logged rule id. |
| **Adjacent industries** | Explicit adjacency list; not pure tree distance. |
| **Cross-functional transitions** | Asymmetric: candidate A→role B may differ from B→A (different bars). |
| **Asymmetric overlap** | Employer needs 10 skills; candidate has 8 **core** → high; has 8 **peripheral** → lower — use **requirement weights** from market. |

### Short examples

| Transition | Overlap story |
|------------|---------------|
| **Consultant → strategy** | High analytical_skills + communication overlap; industry_overlap varies |
| **PM → operations** | High operational_skills + project overlap; leadership may need evidence bump |
| **Finance → fintech** | industry_overlap medium; competency_overlap in regulatory + tech stack gap |
| **Engineering → product** | Partial role_overlap; competency_overlap on discovery, stakeholder mgmt |

---

## 7. Recommendation priority model

Recommendations are **ordered work items**. Priority combines **impact**, **effort**, **urgency**, and **blocker type**.

| Priority class | Trigger (conceptual) | Urgency | Est. effort | Est. impact | Typical outcome | Prioritization logic |
|----------------|----------------------|---------|-------------|-------------|-----------------|----------------------|
| **high_impact_low_effort** | Large gap closure for small work | High | Low | High | Quick win | Sort first in UI |
| **high_impact_high_effort** | Cert, degree, long portfolio | Medium | High | High | Phase 2 plan | Split into milestones |
| **critical_missing_evidence** | Explicit “show proof” market | High | Medium | High | Interview pass | Never buried below cosmetic tips |
| **market_mismatch** | profile vs NAV market bundle | Medium | Medium–high | High | Better targeting | After blockers |
| **trajectory_blocker** | Next step missing enabler | High for that path | Varies | High for path | Clear sequence | Tie to trajectory graph |
| **selection_blocker** | Known case/tech stage weak | High before interview | Medium | High | Pass rate | Date-aware (interview in 5d) |
| **confidence_gap** | Too many weak_signal decisions | Medium | Low | Medium | Stabilize profile | “Verify or quantify” |
| **visibility_gap** | Good overlap, low discovery | Lower early | Medium | Medium–long | Inbound opps | After core evidence |

### Operational rules

| Topic | Rule |
|-------|------|
| **Stacking** | Max **N** active “urgent” items (e.g. 3); rest in backlog to reduce fatigue. |
| **Conflicts** | “Apply broadly” vs “niche positioning” → **market_mismatch** resolver chooses based on evidence strength + stage. |
| **Fatigue** | Rotate categories daily in notifications; same gap not repeated if dismissed with reason. |
| **Sequencing** | evidence before visibility; role alignment before network spam. |
| **Timing sensitivity** | Interview date triggers **selection_blocker** boost; deadline suppresses long courses. |

### Examples mapped

| User action example | Priority class |
|---------------------|----------------|
| Add quantified results | critical_missing_evidence / high_impact_low_effort |
| Improve LinkedIn | visibility_gap / high_impact_low_effort (if content exists) |
| Obtain certification | high_impact_high_effort when explicit gate |
| Build portfolio | critical_missing_evidence for PM/design |
| Strengthen network | network_gap; never above critical blockers |
| Improve interview readiness | selection_blocker when interview scheduled |

---

## 8. Candidate readiness model

Readiness is a **stage** derived from **aggregated signals**, not vanity %.

| Stage | Definition | Required signals (indicative) | Typical weaknesses | Typical recommendations | Conv. probability (order of magnitude) | Evidence expectations |
|-------|------------|------------------------------|--------------------|---------------------------|----------------------------------------|-------------------------|
| **exploring** | No clear target | Diverse browsing | Target scatter | Narrow role/industry; research | Low | Light |
| **early_ready** | Target chosen; gaps visible | Basic competency map | Evidence thin | Quantify projects; courses | Low–medium | Growing |
| **application_ready** | Core gaps soft; materials coherent | CV ↔ role overlap mid+ | Tailoring weak | improve_positioning; apps | Medium | Solid CV |
| **interview_ready** | Strong overlap + selection prep | selection_signal coverage | Case/tech drill | Mock, cases | Medium–high | Proof + prep logs |
| **high_probability** | Strong evidence + market fit | High evidence weights, low critical gaps | Overconfidence | Maintain + selective polish | High | Verifiable wins |
| **transition_ready** | Ready for **next** trajectory step | trajectory_signal green | Narrative for new identity | Trajectory packaging | Varies | Step-specific proof |

### Dynamics

| Topic | Rule |
|-------|------|
| **Progression** | Advance stage when **all critical gaps** below threshold for **target** and **evidence_floor** met. |
| **Decay** | Stale market_window or outdated certs → drop one stage unless refreshed. |
| **Role-specific** | Same person can be interview_ready for role A, exploring for role B — track **per target**. |
| **Market sensitivity** | Hiring freeze market_signal lowers **high_probability** ceiling globally for that industry window. |
| **Confidence-aware** | Cannot reach interview_ready on llm_extracted-only profile without hard evidence for key claims. |

---

## 9. Evidence weighting model

Weights are **defaults** for scoring **evidence_signal** contribution to overlap and gap relief. Tune per **role_family** and **employer_type** in implementation configs.

### Default weight table (illustrative v0)

| Evidence | Default weight | Rationale | Strengths | Weaknesses | Durability | Recency | Role sensitivity | Employer sensitivity |
|----------|----------------|-----------|------------|-------------|------------|---------|-------------------|------------------------|
| **measurable_result** | 5 | Quantified impact hardest to fake | Interview sticky | Context missing | Medium | Decays 3–5y tech / faster fads | High for commercial | High in PE, consulting |
| **certification** | 4 | Third-party skill proof | Gate clearing | Not all roles care | High until expiry | Strong half-life | Very high regulated | High enterprise |
| **degree** | 4 | Bar credential in NO public + some corporates | Stable | Slow to change | Very high | Old degree still counts | High law/health/academia | High public |
| **leadership_role** | 4 | People responsibility | Exec paths | Title inflation | High | Recent team size matters | High mgmt | High enterprise |
| **trainee_program** | 4 | Competitive filter | Strong signal early career | Year-specific | Medium | Recency high | High grad hiring | Sponsor employers |
| **publication** | 4 | Research depth | R&D, policy | Niche | High | Older OK if cited | Research_analysis | Research_institute |
| **portfolio** | 3 | Observable craft | Creative, PM, tech | Quality variance | Medium | Refresh 2–3y | High product/design | Startups, agencies |
| **internship** | 3 | Structured work sample | Early career | Short | Medium | Recent matters most | Universal early | Most |
| **project** | 3 | Scoped delivery | Universal | Scope creep in claims | Medium | Per project date | Tech, consulting | Most |
| **work_sample** | 5 | Employer-evaluated artifact | Very strong | Access unequal | Medium | Event-based | Hiring-heavy | Work_trial employers |
| **presentation** | 3 | Comm skills proof | Sales, public | Self-reported event | Medium | Recent better | Client-facing | Consulting |
| **side_project** | 2 | Motivation + skill | Tech | Unfinished risk | Low–medium | Fast decay if stale | Tech | Startups |
| **volunteer_work** | 2 | Values + soft | Culture | Less standardized | Medium | Story freshness | NGO, employer CSR | Values hiring |
| **board_role** | 4–5 | Governance exposure | Exec paths | Private opacity | High | Long tenure | Senior | Listed companies |
| **recommendation** | 2–3 | Social proof | Trust | Bias | Medium | Dated refs weaker | Sales | SME |
| **award** | 2 | Signal boost | Motivation | Vanity | Low–medium | Spike then fade | Marketing | Varies |
| **mentoring** | 2 | Leadership adjacent | People track | Hard verify | Medium | — | people_hr | Enterprise |
| **network_signal** | 1–2 | Access | Referral markets | Causal weak | Low | Event-based | Finance | PE, banking |
| **generic_claim** | 1 | “Hard-working” | Fills space only | No proof | Low | — | None | Noise |

### Rules

| Topic | Guidance |
|-------|----------|
| **Stacking** | Same competency supported by cert + project → use **max** or **soft-or** with diminishing marginal gain (e.g. second piece +0.5 cap). |
| **Diminishing returns** | After 3 strong evidences for same node, extra bullets add <ε to score — avoid length gaming. |
| **Weak evidence inflation** | Many generic_claim rows **do not** sum past weight 2 equivalent for that competency. |
| **Role-specific weighting** | For `technology`, boost portfolio/side_project; for `public_administration`, boost degree + process evidence. |
| **Trajectory-sensitive** | Next-step may require **leadership_role** weight bump even if current role overlap is high. |

---

## 10. White spot scoring model

### When a white spot exists (conjunctive heuristic)

A **white spot** is present when **all** of the following are roughly true (thresholds tuned in governance):

1. **Employers / market value** the capability highly (`market_importance` high from ads + explicit Spor 2).  
2. **Candidates rarely document** it (`documentation_frequency` low in CV/LinkedIn corpus or self-report proxies).  
3. **Education rarely trains** it (`education_coverage` low in learning outcomes / course catalogs).  
4. **Career services rarely emphasize** it (`career_service_coverage` low on Spor 1 career pages).

### Components

| Component | Meaning |
|-----------|---------|
| **white_spot_strength** | Aggregated score **0–1** from the four inputs + confidence discount. |
| **market_importance** | Frequency × explicit_requirement weight in NAV/trainee text for window W. |
| **documentation_frequency** | Share of profiles/applications mentioning evidence types linked to capability. |
| **education_coverage** | Programs with mapped learning outcomes covering capability. |
| **candidate_awareness** | Surveys / optional quick checks (“did you highlight X?”) if available. |

### Phenomena

| Phenomenon | Meaning |
|-------------|---------|
| **Hidden market expectations** | High market_importance, low explicit_requirement rate but high inferred_pattern (culture). |
| **Invisible selection criteria** | selection_signal shows factor; ads silent → **process_gap** + white_spot. |
| **Silent rejection factors** | Network/referral, “polish”, pacing — weak_signal, ethical care. |
| **Emerging white spots** | Spiking keyword with low education_coverage → “new gap opening.” |

### Examples

| Theme | Why it’s a white spot candidate |
|-------|--------------------------------|
| **Networking / referrals** | High market_importance in finance; low documentation_frequency |
| **Quantified business impact** | Ads want numbers; CVs generic |
| **Case interview readiness** | Trainee selection_signal strong; education_coverage low |
| **Presentation skills** | Client roles; sparse portfolio-like proof |
| **Stakeholder management** | PM/consulting; often only soft bullets |
| **Portfolio evidence** | Tech/product; many claims, few links |

### Product effects

| Surface | Behavior |
|---------|----------|
| **Recommendations** | Boost **improve_evidence** / **visibility** / **case** items when white_spot_strength > τ and confidence OK. |
| **RAG** | Prefer chunks that **name** the white spot with **citation** to market window; avoid blaming candidate. |
| **Package builder** | Add optional “evidence kit” module for top white spot in target industry. |

---

## 11. RAG retrieval priority model

### Principles (high → low)

1. **verified_statistical** > **inferred_pattern**  
2. **explicit employer criteria** > **review_based**  
3. **Recent** market_window > **stale**  
4. **Norwegian market** facts > generic global career blog  
5. **Repeated patterns** (aggregated NAV) > single anecdote  
6. **Evidence-backed** recommendation rules > generic pep talk  

### Preferred retrieval order (default stack)

1. Verified statistics (SSB, NIFU tables, official aggregates)  
2. Explicit employer requirements (NAV + Spor 2 quotes)  
3. Explicit trainee / selection criteria  
4. Normalized market patterns (aggregated signals with n disclosed)  
5. Validated review patterns (aggregated + caveats)  
6. Inferred AI insights (clearly labeled, low rank)  
7. Generic career advice (fallback, short)

### Ranking dimensions

| Dimension | Effect |
|-----------|--------|
| **Confidence-aware** | Multiply rank score by confidence coefficient. |
| **Source weighting** | `source_reliability_score` from registry. |
| **Freshness** | Exponential decay on `market_period` distance. |
| **Role relevance** | Boost chunks tagged with target `role_family_id`. |
| **Candidate stage** | exploring → more “how to choose”; interview_ready → more “drill” content. |

### Hallucination prevention

- No numeric claim without **chunk citation** to a statistic table or counted NAV aggregate.  
- LLM answers **must** list **confidence** + **period** in UI footers.  
- **Recommendation traceability**: chunk → signal id → rule id.

### Retrieval examples

| Query intent | What ranks first |
|--------------|------------------|
| **Job-specific advice** | explicit_requirement from that employer + similar NAV cluster |
| **Interview prep** | explicit_selection_criterion + selection_signal patterns |
| **LinkedIn optimization** | market_signal keyword bundles + evidence_gap fixes (not vibes) |
| **Trajectory guidance** | trajectory_overlap paths + verified_statistical outcomes if any |

---

## 12. Signal relationships

```text
evidence ──supports──► signal (typed, strength)
signals ──compose──► derived signals (e.g. market aggregates)
signals + targets ──produce──► gaps / overlaps (with confidence)
gaps + overlaps + readiness ──prioritize──► recommendations
candidate stages ──gate──► which rules fire
industries / employers ──bias──► weights and risk_signal
trajectory models ──shift──► trajectory_gap and trajectory_overlap
```

| Mechanism | Meaning |
|-----------|---------|
| **Cascading** | Parent market_signal shifts child competency_gap thresholds. |
| **Contradictory** | High role_overlap but high risk_signal → show **both** with conflict UI. |
| **Conflicts** | Two recommendations reduce same metric — resolver picks higher **priority class** (§7). |
| **Inheritance** | Team member inherits project signals with lower strength. |
| **Derived** | “Leadership index” from bundle of evidence types — versioned formula id. |
| **Temporal decay** | Strength/confidence decay functions per signal type (§3–4). |

---

## 13. Scoring governance

| Control | Purpose |
|---------|---------|
| **Scoring versioning** | `scoring_model_version` on every derived output for reproducibility. |
| **Recalculation** | Batch on taxonomy change or new market_window; incremental for user edits. |
| **Auditability** | Store inputs snapshot ids (not necessarily full PII). |
| **Explainability** | Every score exposes “top 3 contributing signals.” |
| **Human override** | SME can pin confidence or suppress recommendation for employer X. |
| **AI-assisted scoring** | Proposals in shadow mode until precision/recall thresholds met. |
| **Threshold tuning** | Change τ only via config PR with evaluation metrics. |
| **Bias detection** | Monitor gap rates by gender proxy geography / field (careful ethics); watch review_based skew. |
| **Fairness** | Do not use protected attributes as inputs; audit inferred proxies. |
| **Market drift detection** | Keyword distribution shift triggers review of inferred_pattern rules. |

**Quality evaluation:** offline replay on historical cohorts; online **outcome** feedback (interview invite self-report optional) with privacy.

**Overfitting:** validate on **multiple industries** and **candidate stages**; penalize models that only fit consulting-heavy samples.

---

## 14. Usage inside sokr.online

| Feature | Scoring role |
|---------|--------------|
| **Package builder** | Bundles by priority class + readiness stage. |
| **CV generation** | Emphasize high-weight evidence; fill evidence_gap first. |
| **LinkedIn optimization** | visibility_gap + market_signal keyword alignment. |
| **Application strategy** | market_mismatch + employer_type weights. |
| **Networking guidance** | network_gap only when sector_prior says so; ethical copy. |
| **Interview preparation** | selection_blocker + selection_signal coverage. |
| **Role targeting** | role_gap + trajectory_overlap. |
| **Learning recommendations** | competency_gap + certification_gap + education_coverage. |
| **Candidate ranking** (internal) | Readiness + overlap − penalty for critical gaps — **transparent** to user if shown. |
| **Opportunity prioritization** | Match score × confidence × freshness. |
| **Transition planning** | trajectory_gap sequencing. |

### Persona examples

| Persona | Scoring emphasis |
|---------|------------------|
| **Graduate** | evidence_gap, process_gap, trainee alignment; lower network expectations |
| **Executive transition** | trajectory_gap, risk_signal, evidence for scale; board_role weight |
| **Career switch** | role_gap + competency_gap + industry_overlap partial credit |
| **Specialist** | deep competency_overlap; narrow role_overlap acceptable |
| **Public → private** | risk_signal + pace metrics + evidence_gap on commercial results |

---

## 15. Future expansion

| Direction | Notes |
|-----------|--------|
| **Predictive scoring** | Hire probability models — heavy governance, opt-in data. |
| **Labor-market forecasting** | ARIMA/nowcast on signal time series per taxonomy node. |
| **Recruiter calibration** | Close-loop with anonymized employer feedback — legal first. |
| **Hiring probability estimation** | Same; avoid discriminatory proxies. |
| **Trajectory prediction** | Markov / graph neural nets on role transitions — label as **predicted**. |
| **Benchmark scoring** | Percentile vs anonymized cohort by stage + field. |
| **AI simulation/testing** | Synthetic candidates to stress-test rules before deploy. |
| **Graph-based scoring** | Random walk on competency graph for related skills. |
| **Reinforcement feedback loops** | User thumbs-up/down adjusts **ranking** weights, not raw truth. |

---

## 16. Open questions

1. **Score transparency:** show raw numbers to users or only qualitative bands?  
2. **Candidate visibility:** which internal scores (e.g. “hire probability”) are ethical to display?  
3. **Confidence thresholds:** global vs per industry for critical_gap promotion.  
4. **Review-source legality:** which jurisdictions and retention for review_based signals.  
5. **Scoring fairness:** minimum cohort size before showing demographic-adjusted benchmarks.  
6. **Regional differences:** Oslo vs rural — separate market_window or smooth?  
7. **International expansion:** separate scoring configs per country vs unified scale.  
8. **Employer calibration:** will any employers supply ground-truth labels for validation?  
9. **Network_signal ethics:** opt-in only vs inferred graph features.  
10. **Conflict resolution UI:** when two recommendations contradict, who decides default?

---

### Architecture summary

The **scoring and signal layer** turns heterogeneous Norwegian labor-market inputs into **typed signals** with **strength** (how loud the observation is) and **confidence** (how trustworthy the interpretation is). **Gaps** and **overlaps** are computed from weighted signal differences and similarities, capped and explained by confidence. **Readiness stages** gate product maturity; **recommendation priority classes** order work. **Evidence weights** power proof-based overlap and gap relief. **White spots** highlight systemic documentation/training holes. **RAG retrieval** stacks sources from verified statistics down to generic advice, with freshness, role, and stage multipliers — always **citable** and **traceable** to signals and taxonomy ids. Governance (versioning, bias, drift, human override) keeps the system **debuggable** for product and engineering alike.
