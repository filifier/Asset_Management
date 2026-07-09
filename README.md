# Portfolio BI — Motore di Business Intelligence per il tuo portafoglio

Uno strumento che **descrive lo stato** del tuo asset incrociando quattro
livelli di dati (momentum/valutazione dell'asset, contesto macro attuale,
trend/outlook macro, la tua posizione) e produce uno **scorecard
trasparente**.

**Filosofia:** descrive, non prescrive. Nessun output "compra/vendi", nessun
modello black-box. Ogni segnale — inclusi quelli di trend — è una regola
esplicita e ricalcolabile a mano. La decisione resta tua.

## Cosa fa

1. Legge la tua posizione da `data/position.json`
2. Scarica i dati di mercato — livello attuale **e storico** — da Yahoo
   Finance (benchmark) e BlackRock (NAV del fondo)
3. Calcola quattro pilastri con regole trasparenti
4. Stampa uno scorecard leggibile e salva **due file**:
   - `data/scorecard.json` — completo, coi tuoi dati di posizione. Resta solo
     in locale (escluso da git).
   - `docs/data/scorecard.json` — solo asset + macro, **senza nessun dato
     personale**. Questo è quello sicuro da pubblicare su GitHub Pages.

## Come si usa (in locale)

```bash
pip install -r requirements.txt
python run.py
```

Se non hai internet o il fetch del NAV fallisce, usa l'ultimo NAV noto — non
inventa mai numeri.

Il NAV del fondo viene dalla stessa fonte che alimenta il grafico storico
della pagina BlackRock (endpoint interno `.../<id>.ajax?tab=chart`, non uno
scraping fragile dell'HTML) — dà sia l'ultimo prezzo sia **tutto lo storico
dal lancio del fondo**. Da questo storico, `run.py` calcola dal vivo il range
52 settimane, i rendimenti 1y/5y del fondo e del benchmark (S&P 500 da Yahoo),
e il trend NAV a ~1 mese — nessuno di questi numeri è più scritto a mano.

Copia `data/position.example.json` in `data/position.json` e mettici i tuoi
valori reali — quel file non viene mai versionato su git (vedi `.gitignore`).

## I quattro pilastri (in engine/scoring.py)

- **Asset momentum & valuation** — trend NAV a 1 mese, dove sta il prezzo nel
  range 52 settimane, performance a 1 e 5 anni **vs S&P 500** (proxy di
  mercato generico, non il benchmark ufficiale del fondo — l'etichetta lo dice
  esplicitamente per non creare ambiguità su cosa viene confrontato).
- **Macro context** — il **livello attuale** dei segnali macro (tassi, VIX,
  oro, petrolio, FX) **pesati per quanto contano per QUESTO tipo di asset**.
  Le pesature sono in `ASSET_PROFILES`.
- **Macro outlook (trend)** — la stessa lista di fattori, ma letta come
  **direzione/variazione dell'ultimo mese** invece che livello attuale. È la
  parte "forecasting": non un modello predittivo, ma una variazione
  percentuale semplice e ricalcolabile a mano (vedi sotto). Usa le stesse
  pesature per asset di `ASSET_PROFILES`.
- **Your position** — P&L, concentrazione nel portafoglio. **Solo locale**: non
  entra mai nello scorecard pubblico.

Ogni pilastro dà un punteggio in [-2, +2] e la lista dei segnali che l'hanno
prodotto. Puoi vedere e modificare ogni soglia.

## Outlook: perché trend statistico e non ML

Ho scelto deliberatamente di non usare machine learning per il forecasting.
Un modello ML (regressione, LSTM, ecc.) introdurrebbe una scatola nera che
contraddice la filosofia "ogni segnale è ispezionabile" del progetto, e
richiederebbe molti più dati storici di quelli disponibili con una fonte
gratuita. Il pilastro "Macro outlook" usa invece **variazione percentuale
su una finestra di ~21 giorni di trading (~1 mese)**: se vuoi verificarlo,
`(ultimo_prezzo - prezzo_di_21_giorni_fa) / prezzo_di_21_giorni_fa * 100` — lo
stesso numero che vedi nel segnale. La direzione (in salita/discesa) viene
poi tradotta in supportive/caution tramite `TREND_POLARITY` in
`engine/scoring.py`, con la stessa logica esplicita usata per il resto del
motore. Per tassi, gold e oro, petrolio ed EUR/USD non è tracciato ancora un
proxy storico (BTP-Bund) — resta "n/a" finché non viene collegata una fonte.

## Sezione "Andamento" — grafico e proiezione

Sotto i pillar c'è un grafico (`docs/chart.js`, vanilla JS + SVG, nessuna
libreria esterna) che sovrappone l'asset ai benchmark/macro selezionati.
Tutte le serie sono **indicizzate a 100** all'inizio del periodo visibile,
altrimenti non sarebbero confrontabili (€ vs punti indice vs % vs $). Puoi
scegliere il periodo (3M/6M/1Y/5Y/All) e quali serie mostrare.

La **proiezione lineare** (checkbox a parte) è una regressione lineare
semplice (minimi quadrati) calcolata sul NAV del periodo selezionato ed
estesa in avanti di circa 1/4 della finestra. Mostra sempre l'**R²** — quanto
bene la retta spiega i dati reali — così quando è basso (spesso lo è, i
prezzi non sono lineari) si vede subito che la proiezione è debole, invece di
nasconderlo. Il testo sotto il grafico lo dice esplicitamente: non è una
previsione, è l'estensione geometrica del trend recente. Stessa filosofia
"no black-box" del resto del motore — è una retta, la puoi ricalcolare a
mano da `slope`/`intercept` in `chart.js`.

## Pubblico vs privato

Il pilastro "Your position" contiene i tuoi numeri reali (importo investito,
valore portafoglio) — dati che non vuoi finiscano in un repository pubblico.
Per questo:

- `docs/` è la dashboard **pubblica**: mostra asset momentum, macro context e
  macro outlook (dati di mercato, nessun dato tuo), più `docs/data/nav_history.json`
  — lo storico del NAV del fondo, anche questo dato di mercato pubblico. È
  quella che pubblichi su GitHub Pages.
- Nella dashboard pubblica c'è comunque una sezione **"La tua posizione
  (privata)"**: un piccolo form dove chiunque (anche tu, sul tuo dispositivo)
  inserisce **importo investito e data di acquisto** — non le unità. Il
  browser cerca da solo il NAV di quel giorno nello storico pubblico
  (`nav_history.json`) e calcola P&L e concentrazione, **interamente in
  JavaScript** — non viene mai inviato a un server né salvato nel repository.
  Se spunti "ricorda", resta solo nel `localStorage` del tuo browser.
- `web/` è la dashboard di **sviluppo locale**: mostra tutti e quattro i
  pilastri usando i dati reali da `data/position.json`, comoda per uso
  personale sul tuo Mac.

## Struttura

```
portfolio_bi/
├── run.py                    # entry point: lega tutto
├── requirements.txt
├── data/
│   ├── position.example.json # template — copialo in position.json
│   ├── position.json         # LA TUA POSIZIONE reale (gitignored)
│   └── scorecard.json        # output completo, locale (gitignored)
├── engine/
│   ├── fetch.py                # scarica NAV (BlackRock) + benchmark e storico
│   │                            # (Yahoo Finance, no API key)
│   └── scoring.py              # il motore trasparente: 4 pillar_*, build_scorecard
│                                # (completo) e build_public_scorecard (senza posizione)
├── web/                       # dashboard di sviluppo locale (tutti i pilastri)
│                                # riusa docs/chart.js e docs/data/*.json
└── docs/                      # dashboard pubblica (GitHub Pages) + calcolatore
    ├── chart.js                # grafico + proiezione lineare (vanilla JS/SVG)
    └── data/
        ├── scorecard.json      # output pubblico, senza dati personali
        ├── nav_history.json    # storico NAV del fondo — dato di mercato, pubblico
        └── macro_history.json  # storico benchmark/macro — dato di mercato, pubblico
```

## Prossimi passi — cose da chiedere a Claude Code

Apri questa cartella nella scheda **Code** dell'app Claude e prova a chiedere:

- *"Leggi il README, installa le dipendenze e fai girare run.py, poi mostrami
  lo scorecard."*
- *"Aggiungi un front-end HTML in web/ che renderizza scorecard.json con la
  stessa estetica verde/crema del mio dashboard."*
- *"Aggiungi un secondo asset alla mia posizione e un nuovo profilo di pesi
  macro per un ETF azionario globale."*
- *"Rendi più robusto lo scraping del NAV in fetch.py e aggiungi un test."*

## Modifica la tua posizione

Copia `data/position.example.json` in `data/position.json` (se non l'hai già
fatto) e metti i tuoi valori reali — resta locale, non viene versionato:

```json
{
  "portfolio_value_eur": 50000,
  "holdings": [
    {
      "name": "BGF Sustainable Energy E2 EUR",
      "isin": "LU0171290074",
      "profile_key": "clean_energy_equity",
      "invested_amount_eur": 1500,
      "purchase_date": "2024-03-15"
    }
  ]
}
```

Niente `units`/`avg_cost` da calcolare a mano: `invested_amount_eur` è quanto
hai messo in totale, `purchase_date` è il giorno in cui l'hai comprato
(`YYYY-MM-DD`). `run.py` cerca da solo il NAV di quel giorno (o del trading
day precedente, se cade nel weekend) nello storico BlackRock, e da lì ricava
quante unità implicite possiedi e quanto valgono oggi — stessa identica
logica usata dal calcolatore privato nella dashboard pubblica.

## Importante

Uso personale e informativo. Non è consulenza finanziaria. I dati provengono da
BlackRock e Yahoo Finance e possono contenere errori o ritardi — verifica sempre
col tuo broker. Lo scorecard descrive lo stato corrente (e il suo trend recente);
non è una raccomandazione né una previsione affidabile.
