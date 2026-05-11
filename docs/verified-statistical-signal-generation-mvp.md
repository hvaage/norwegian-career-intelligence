# Verified statistical signal generation MVP

**Specification only:** no SQL, no signal-generation scripts, no orchestration code, no frontend logic.  
This document defines the first operational framework for generating `verified_statistical` signals from normalized public statistical observations in the Norwegian Career Intelligence Dataset and **sokr.online**.

**Related documents:**

- `docs/statistical-observation-schema.md`
- `docs/statistical-ingestion-pipeline-mvp.md`
- `docs/public-statistical-ingestion-and-normalization-plan.md`
- `docs/scoring-and-signal-model.md`
- `docs/career-taxonomy-design.md`
- `docs/ssb-normalization-mvp.md`

---

## 1. Purpose of verified statistical signals

The statistical observation layer stores normalized measured facts.

The signal layer exists to transform those measured facts into:

- interpretable labor-market intelligence
- explainable market indicators
- comparable trajectories
- gap and overlap evidence
- recommendation inputs
- future benchmarking inputs
- future forecasting inputs

Signals are therefore:

```text
measured interpretation layers

—not raw statistics.
```

---

## 2. Observation vs signal

### Observations

One observation equals one measured statistical fact.

Examples:

employment count  
unemployment rate  
employment share  
population count  
occupation participation  
transition probability  

Observations are:

immutable  
directly traceable  
dimension-specific  
source-authoritative  

### Signals

Signals are:

interpreted statistical patterns  

built from one or more observations.

Signals may include:

growth  
decline  
imbalance  
concentration  
volatility  
transition likelihood  
role saturation  
education mismatch  
regional specialization  

Signals are therefore:

derived but explainable  

---

## 3. MVP signal philosophy

The MVP prioritizes:

explainability  
deterministic generation  
lineage preservation  
statistical transparency  
reproducibility  
conservative interpretation  

The MVP explicitly avoids:

black-box inference  
opaque AI scoring  
hidden weighting systems  
causal claims  
forecasting  
probabilistic hallucination  

Signals must always be explainable from source observations.

---

## 4. Signal granularity

### Principle

Signal granularity defines:

how specific a signal is allowed to be  

before reliability becomes too weak.

### Allowed MVP granularity

#### Education-level signals

Examples:

engineering graduates  
health sciences  
economics/business  
humanities  

#### Occupation-level signals

Examples:

software developers  
nurses  
teachers  
construction workers  

#### Industry-level signals

Examples:

IT industry  
healthcare  
finance  
manufacturing  

#### Regional signals

Examples:

Oslo  
Vestland  
Trondheim  

#### Demographic slices

Examples:

gender  
age groups  
education levels  
employment type  

### Disallowed MVP granularity

The MVP should avoid:

individual institutions  
tiny occupations  
sparse categories  
unstable microsegments  
highly fragmented combinations  

Example:

Female part-time economics graduates age 20–24 in one municipality  

This becomes statistically unstable.

---

## 5. Signal categories

### Verified statistical

Directly supported by measured observations.

Examples:

employment growth  
declining participation  
gender imbalance  
regional concentration  

### Market signals

Describe labor-market demand/supply structures.

Examples:

rising employment share  
industry expansion  
regional specialization  

### Trajectory signals

Describe likely directional movement.

Examples:

education pathways  
occupation migration  
transition patterns  

### Risk signals

Describe structural instability.

Examples:

declining occupation share  
demographic imbalance  
shrinking participation  
concentration risk  

### Transition signals

Describe movement between states.

Examples:

education → occupation  
occupation → industry  
industry → region  

---

## 6. Derived vs measured

### Measured

Measured values originate directly from source observations.

Examples:

12% employment increase  
240,000 employed  
61% participation  

Measured values inherit:

verified_statistical  

confidence directly from authoritative datasets.

### Derived

Derived signals are interpretations built from measured observations.

Examples:

“strong labor-market growth”  
“occupation saturation”  
“emerging transition pathway”  

Derived signals require:

separate confidence  
explainability  
transformation lineage  
aggregation rules  

Derived signals must never pretend to be raw measurements.

---

## 7. Aggregation rules

### Principle

Aggregation converts multiple observations into:

trends  
ratios  
comparisons  
concentrations  
movement indicators  

### Allowed MVP aggregations

#### Temporal aggregation

Examples:

yearly growth  
rolling averages  
multi-period comparisons  

#### Dimensional aggregation

Examples:

industry totals  
occupation families  
education categories  

#### Ratio calculations

Examples:

employment share  
gender ratio  
concentration ratio  

### Disallowed MVP aggregation

The MVP should avoid:

complex weighting systems  
hidden ranking formulas  
machine-learned aggregation  
opaque composite scores  

---

## 8. Threshold strategy

### Purpose

Thresholds determine when observations become meaningful signals.

### Examples

#### Growth threshold

employment increase > X%  

#### Risk threshold

participation decline > X%  

#### Concentration threshold

industry share exceeds Y%  

#### Transition threshold

trajectory probability exceeds Z%  

### MVP philosophy

Thresholds should initially be:

conservative  
explainable  
adjustable  
versioned  

Thresholds are governance artifacts.

---

## 9. Statistical confidence

### Direct observation confidence

Direct SSB measurements generally inherit:

verified_statistical  

with high confidence.

### Derived signal confidence

Derived confidence depends on:

sample size  
dimensional stability  
aggregation depth  
temporal consistency  
mapping reliability  
sparsity risk  

### Confidence downgrade triggers

#### Sparse categories

Tiny groups reduce reliability.

#### Weak mappings

Cross-taxonomy mappings reduce certainty.

#### Multi-hop derivation

Observation → aggregation → interpretation → recommendation reduces confidence.

### Confidence categories

Examples:

verified_statistical  
strong_statistical  
moderate_statistical  
inferred  
experimental  

---

## 10. Explainability

### Requirement

Every signal must explain:

source datasets  
periods  
dimensions  
transformations  
aggregation logic  
confidence rationale  

### Example

This signal is based on:

- SSB table 11615
- period 2024
- region Oslo
- engineering-related education fields
- employment growth +12%
- derived using yearly percentage-change logic

### Explainability principles

Signals must never become:

black-box scores  
hidden AI outputs  
opaque rankings  

---

## 11. Lineage inheritance

Signals inherit lineage from observations.

Signal lineage therefore includes:

source dataset  
dataset version  
source files  
normalization version  
transformation version  
signal-generation version  
aggregation logic version  

### Principle

Every signal must be reproducible from:

```text
raw observation → transformation → signal
```

---

## 12. Trend windows

### Purpose

Trend windows define:

which temporal range is used  

for signal generation.

### MVP windows

#### Single-year

Simple yearly comparison.

#### Multi-year

Examples:

2-year trend  
3-year trend  
rolling averages  

### MVP limitations

The MVP avoids:

long forecasting horizons  
advanced seasonality adjustment  
predictive modeling  

---

## 13. Overlap rules

### Purpose

Overlap signals identify:

shared statistical characteristics  

between entities.

### Examples

#### Education overlap

Two education groups entering similar occupations.

#### Occupation overlap

Two occupations sharing industry patterns.

#### Regional overlap

Regions with similar employment structures.

### Constraints

Overlap does not imply equivalence.

Similarity must remain explainable.

---

## 14. Risk rules

### Purpose

Risk signals identify:

structural decline  
concentration  
imbalance  
volatility  
demographic exposure  

### Examples

#### Participation decline

Falling occupation participation over time.

#### Gender imbalance

Extreme skew in occupation representation.

#### Industry concentration

Heavy dependence on one industry.

#### Regional fragility

Dependence on shrinking labor-market structures.

---

## 15. Transition rules

### Purpose

Transition signals describe movement potential.

### Examples

#### Education → occupation

Common occupation outcomes.

#### Occupation → industry

Typical industry distribution.

#### Regional movement

Regional concentration shifts.

### MVP constraints

The MVP does not claim:

causal transitions  
guaranteed outcomes  
individual prediction  

Transitions are:

statistical directional patterns  

---

## 16. Mapping dependency

Signals depend on mappings between:

education taxonomy  
occupation taxonomy  
industry taxonomy  
trajectory taxonomy  
recommendation categories  

### Important principle

Mappings are interpretive layers.

Mappings are not observations.

Weak mappings reduce confidence.

---

## 17. Statistical signal lifecycle

### Pipeline

```text
raw datasets
→ normalized observations
→ validated observations
→ verified_statistical signals
→ interpreted intelligence
→ recommendations
```

Signals are therefore:

mid-layer intelligence artifacts  

—not raw data and not final recommendations.

---

## 18. Validation requirements

Signals must pass:

lineage validation  
explainability validation  
threshold validation  
aggregation validation  
confidence validation  
dimensional consistency validation  
temporal consistency validation  

### Hard-fail examples

missing provenance  
unsupported aggregation  
undefined threshold  
conflicting dimensions  
unverifiable derived logic  

---

## 19. MVP limitations

The MVP does not include:

realtime updates  
forecasting  
embeddings  
AI-generated narratives  
autonomous recommendations  
causal inference  
predictive ranking  
hidden weighting systems  
large-scale benchmarking  

---

## 20. Future evolution

Future phases may include:

NAV labor-market signals  
OECD comparisons  
Eurostat benchmarking  
forecasting  
labor-market simulation  
graph-based transitions  
embedding-assisted retrieval  
recommendation optimization  
temporal anomaly detection  
statistical benchmarking engines  

---

## 21. Open questions

Signal persistence vs recomputation strategy  
Aggregation governance ownership  
Threshold tuning process  
Sparse-category handling rules  
Temporal smoothing strategy  
Transition-confidence calibration  
Cross-source conflict handling  
Mapping governance/versioning  
Statistical anomaly policies  
Signal versioning strategy  

---

## Summary

The verified statistical signal layer transforms normalized statistical observations into explainable labor-market intelligence.

The architecture separates:

measured observations  

from:

interpreted signals  

This separation is critical for:

explainability  
reproducibility  
statistical integrity  
confidence governance  
future recommendation systems  
future forecasting  
future RAG retrieval  
future trajectory intelligence  

The MVP prioritizes:

conservative interpretation  
deterministic logic  
lineage preservation  
statistical transparency  
explainability-first signal generation  
human-governed confidence  
observation-first architecture  
