# Changelog — Infra-Ensure

Changes of the Infra-Ensure fork on top of PyPSA-AT. PyPSA-AT changes are in
`CHANGELOG.AT.md`. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Planned]

### Added
- Industry-history mini-workflow (`pixi run industry-history`) building the industrial production per country for every year from 2014 to 2023 (`config/infra-ensure/config.industry-history.yaml`)

### Fixed
- Ammonia production uses the years in the USGS file, so reference years before 2019 no longer raise a `KeyError`
- Industry production passes through unchanged for years without Ariadne FORECAST data
