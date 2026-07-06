# Portfolio BI — Motore di Business Intelligence per il tuo portafoglio

Uno strumento che **descrive lo stato** del tuo asset incrociando tre livelli di
dati (momentum/valutazione dell'asset, contesto macro, la tua posizione) e
produce uno **scorecard trasparente**. 

**Filosofia:** descrive, non prescrive. Nessun output "compra/vendi". Ogni
segnale è una regola esplicita e ispezionabile. La decisione resta tua.

## Cosa fa

1. Legge la tua posizione da `data/position.json`
2. Scarica i dati di mercato (benchmark da Stooq; NAV del fondo da BlackRock)
3. Calcola tre pilastri con regole trasparenti
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

Copia `data/position.example.json` in `data/position.json` e mettici i tuoi
valori reali — quel file non viene mai versionato su git (vedi `.gitignore`).

## I tre pilastri (in engine/scoring.py)

- **Asset momentum & valuation** — dove sta il prezzo nel range 52 settimane,
  performance vs benchmark a 1 e 5 anni.
- **Macro context** — i segnali macro (tassi, VIX, oro, petrolio, FX) **pesati
  per quanto contano per QUESTO tipo di asset**. Le pesature sono in
  `ASSET_PROFILES`.
- **Your position** — P&L, concentrazione nel portafoglio. **Solo locale**: non
  entra mai nello scorecard pubblico.

Ogni pilastro dà un punteggio in [-2, +2] e la lista dei segnali che l'hanno
prodotto. Puoi vedere e modificare ogni soglia.

## Pubblico vs privato

Il pilastro "Your position" contiene i tuoi numeri reali (unità, prezzo medio,
valore portafoglio) — dati che non vuoi finiscano in un repository pubblico.
Per questo:

- `docs/` è la dashboard **pubblica**: mostra solo asset momentum e macro
  context (dati di mercato, nessun dato tuo). È quella che pubblichi su GitHub
  Pages.
- Nella dashboard pubblica c'è comunque una sezione **"La tua posizione
  (privata)"**: un piccolo form dove chiunque (anche tu, sul tuo dispositivo)
  può inserire units/prezzo medio/valore portafoglio. Il calcolo di P&L e
  concentrazione avviene **interamente nel browser**, in JavaScript — non
  viene mai inviato a un server né salvato nel repository. Se spunti "ricorda",
  resta solo nel `localStorage` del tuo browser.
- `web/` è la dashboard di **sviluppo locale**: mostra tutti e tre i pilastri
  usando i dati reali da `data/position.json`, comoda per uso personale sul
  tuo Mac.

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
│   ├── fetch.py               # scarica NAV + benchmark (no API key)
│   └── scoring.py              # il motore trasparente, build_scorecard (completo)
│                                # e build_public_scorecard (senza posizione)
├── web/                       # dashboard di sviluppo locale (tutti i pilastri)
└── docs/                      # dashboard pubblica (GitHub Pages) + calcolatore
    └── data/scorecard.json     # output pubblico, senza dati personali
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
      "units": 100,
      "avg_cost": 15.00
    }
  ]
}
```

## Importante

Uso personale e informativo. Non è consulenza finanziaria. I dati provengono da
BlackRock e Stooq e possono contenere errori o ritardi — verifica sempre col tuo
broker. Lo scorecard descrive lo stato corrente; non è una raccomandazione.
