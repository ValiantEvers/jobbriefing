# Job Briefing

Daglig, personlig stillings-briefing. Scraper Finn.no, scorer mot din profil, viser de beste treffene.

## Slik fungerer det

1. **GitHub Actions** kjører `collect.py` hver morgen kl. 08:00 (Oslo-tid)
2. Scriptet søker Finn.no med 7 ulike søk tilpasset din profil
3. Stillinger scores med en profil-match-score (1-10)
4. De beste treffene publiseres til `jobs.json`
5. Dashboardet (`index.html`) viser stillingene rangert etter match

## Scoring-metode

| Faktor | Vekt | Beskrivelse |
|--------|------|-------------|
| Rolle-match | 40% | Matcher tittelen Wealth Mgmt, Private Banking, Fund Sales? |
| Kompetanse-match | 25% | Treffer stillingen dine ferdigheter? |
| Selskaps-kvalitet | 20% | Er det et anerkjent finansselskap? |
| Ferskhet | 15% | Hvor ny er annonsen? |

Justering: Junior/trainee-stillinger får +1.0 bonus, senior/leder-stillinger får -1.5 straff.

## Oppsett

Samme steg som geobriefing:
1. Lag nytt repo `jobbriefing` under din GitHub-bruker
2. Last opp alle filene
3. Lag `.github/workflows/jobs.yml` via "Create new file"
4. Aktiver GitHub Pages (Settings → Pages → main branch)
5. Gi Actions skrivetilgang (Settings → Actions → General → Read and write)
6. Kjør manuelt første gang via Actions-fanen

## Tilpasning

Rediger `config.json` for å:
- Legge til flere Finn.no-søk
- Endre rolle-nøkkelord og prioritering
- Legge til/fjerne selskaper i tier-listene
- Justere scoring-vekter

## Stack

- **Scraping**: Python 3 (kun stdlib)
- **Kilde**: Finn.no jobbsøk
- **Frontend**: Vanilla HTML/CSS/JS
- **Hosting**: GitHub Pages (gratis)
- **Automatisering**: GitHub Actions (gratis)

## Kostnad

**$0/mnd.**
