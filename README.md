# norwegian-career-intelligence
Analysis of Norwegian job ads

## System ownership

This repository owns the NAV/Arbeidsplassen source mirror and its upstream
contracts. It preserves ACTIVE and INACTIVE posting history in the Supabase
project `norwegian-career-intelligence` (`rcqnuzplpncnkjmldwqs`).

The public market-intelligence backend used by
[`karrierenmin.no/markedsinnsikt`](https://karrierenmin.no/markedsinnsikt) is
maintained separately in [`hvaage/ESCO`](https://github.com/hvaage/ESCO). That
repository owns the public market RPCs and imports for:

- NHO Kompetansebarometeret
- NAV Bedriftsundersokelsen
- NAV monthly unemployment and vacancy statistics
- SSB employment, occupation, industry, education, regional and salary tables
- ESCO and STYRK/EURES occupation and competence mappings

The two NAV products are intentionally different. This repository provides the
continuously updated posting mirror used by the job funnel. The ESCO market
backend currently uses NAV's official monthly vacancy aggregate. Live posting
aggregates are not part of the public market RPCs until a dedicated,
deduplicated nowcast contract is implemented between the two projects.

Do not copy NHO, SSB or public market migrations into this repository. Changes
to those sources belong in `hvaage/ESCO`; changes to NAV posting ingestion and
source contracts belong here.
