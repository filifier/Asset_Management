# Portfolio BI — Motore di Business Intelligence per il tuo portafoglio

Uno strumento che **descrive lo stato** del tuo asset incrociando cinque
livelli di dati (momentum/valutazione dell'asset, contesto macro attuale,
trend/outlook macro, sensibilità statistica a 11 fattori macro, la tua
posizione) e produce uno **scorecard trasparente**.

**Filosofia:** descrive, non prescrive. Nessun output "compra/vendi". Anche
la regressione statistica è **descrittiva** (sensibilità storica), non
predittiva — non dice se un fattore salirà o scenderà, solo come l'asset si
è mosso storicamente quando è successo. Ogni numero — incluse le proiezioni
— è ricalcolabile a mano dai dati pubblicati. La decisione resta tua.

## Cosa fa

1. Legge la tua posizione da `data/position.json`
2. Scarica i dati di mercato — livello attuale **e storico dal 2021-01-01**
   (deliberatamente esclude il crollo/rimbalzo COVID del 2020) — da Yahoo
   Finance (11 benchmark/fattori macro) e BlackRock (NAV del fondo, storico
   completo dal lancio)
3. Calcola cinque pilastri con regole trasparenti, incluso un modello di
   regressione OLS multi-fattoriale (`engine/regression.py`)
4. Stampa uno scorecard leggibile e salva **più file**:
   - `data/scorecard.json` — completo, coi tuoi dati di posizione. Resta solo
     in locale (escluso da git).
   - `docs/data/scorecard.json` — asset + macro + regressione, **senza
     nessun dato personale**. Questo è quello sicuro da pubblicare su
     GitHub Pages.
   - `docs/data/nav_history.json`, `docs/data/macro_history.json` — storico
     grezzo, usato dal grafico e dal calcolatore privato lato browser.

## Come si usa (in locale)

```bash
pip install -r requirements.txt
python run.py
```

`requirements.txt` include `numpy` e `statsmodels` (per la regressione OLS
con diagnostica statistica — p-value, R², VIF). Tira dentro anche `pandas`,
`scipy`, `patsy` come dipendenze transitive di statsmodels.

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

## I cinque pilastri (in engine/scoring.py)

- **Asset momentum & valuation** — trend NAV a 1 mese, dove sta il prezzo nel
  range 52 settimane, performance a 1 e 5 anni **vs S&P 500** (proxy di
  mercato generico, non il benchmark ufficiale del fondo — l'etichetta lo dice
  esplicitamente per non creare ambiguità su cosa viene confrontato).
- **Macro context** — il **livello attuale** dei segnali macro (tassi, VIX,
  oro, petrolio, FX) **pesati per quanto contano per QUESTO tipo di asset**.
  Le pesature sono in `ASSET_PROFILES` — **euristiche, scritte a mano**,
  non stimate sui dati (vedi sotto "Pesi euristici vs regressione").
- **Macro outlook (trend)** — la stessa lista di fattori, ma letta come
  **direzione/variazione dell'ultimo mese** invece che livello attuale. È la
  parte "forecasting": non un modello predittivo, ma una variazione
  percentuale semplice e ricalcolabile a mano (vedi sotto). Usa le stesse
  pesature per asset di `ASSET_PROFILES`.
- **Analisi fattoriale (regressione OLS)** — sensibilità storica del
  rendimento dell'asset a 11 fattori macro, stimata con una vera regressione
  statistica (coefficienti, p-value, R², VIF). Vedi la sezione dedicata sotto.
- **Your position** — P&L, concentrazione nel portafoglio. **Solo locale**: non
  entra mai nello scorecard pubblico.

Ogni pilastro dà un punteggio in [-2, +2] e la lista dei segnali che l'hanno
prodotto. Puoi vedere e modificare ogni soglia.

## Pesi euristici vs regressione — non sono la stessa cosa

I pesi in `ASSET_PROFILES` (macro context/outlook) sono **ipotesi scritte a
mano** nello scaffolding iniziale ("un fondo growth è molto sensibile ai
tassi → peso 1.0"), non calibrate sui dati di questo fondo. Il pilastro
"Analisi fattoriale" è l'opposto: coefficienti **stimati statisticamente**
dai rendimenti storici reali, con la loro incertezza (p-value) in chiaro. Le
due cose vivono deliberatamente separate — non ho sostituito i pesi euristici
con i coefficienti di regressione, per due motivi: (1) il modello di
regressione ha un R² modesto (~0.26, normale per una serie finanziaria) e
alcuni coefficienti hanno alta multicollinearità (VIF>10) e non sarebbero
pesature affidabili; (2) i pesi euristici restano intenzionalmente semplici
e ispezionabili senza serve capire un'OLS. Se in futuro vuoi far convergere
le due cose, i coefficienti sono lì, pronti da usare come punto di partenza.

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

## Analisi fattoriale — le 14 variabili richieste

Delle 14 variabili indipendenti richieste, **11 sono disponibili gratis e
incluse**, 3 no:

| # | Variabile | Fonte | Stato |
|---|---|---|---|
| X1 | VIX | Yahoo `^VIX` | ✅ |
| X2 | MOVE | Yahoo `^MOVE` | ✅ |
| X3 | US10Y | Yahoo `^TNX` | ✅ |
| X4 | US2Y | Yahoo `2YY=F` (futures sul rendimento 2y, proxy dello spot) | ✅ (fetched, non nella regressione — vedi sotto) |
| X5 | TERM_SPREAD | derivato: US10Y − US2Y | ✅ |
| X6 | REAL_YIELD (TIPS 10Y) | FRED `DFII10` | ❌ FRED non raggiungibile da questo ambiente |
| X7 | DXY | Yahoo `DX-Y.NYB` | ✅ |
| X8 | INFLATION_BREAKEVEN | FRED `T10YIE` | ❌ FRED non raggiungibile |
| X9 | CESI | Citi (proprietario) | ❌ nessuna fonte gratuita nota |
| X10 | SP500 | Yahoo `^GSPC` | ✅ |
| X11 | NASDAQ 100 | Yahoo `^NDX` | ✅ |
| X12 | GOLD | Yahoo `GC=F` | ✅ |
| X13 | CRUDE_OIL | Yahoo `CL=F` | ✅ |
| X14 | HY_CREDIT | Yahoo `HYG` (ETF iShares iBoxx HY Corporate Bond) | ✅ |

**FRED**: ho provato a raggiungere `fred.stlouisfed.org` per REAL_YIELD e
INFLATION_BREAKEVEN — la connessione si interrompe subito dopo l'handshake
TLS da questo ambiente. Potrebbe funzionare lanciando `run.py` da una rete
diversa; se vuoi posso aggiungere quei due fattori con un fallback che li
salta silenziosamente se FRED non risponde, così sono pronti se in futuro
diventa raggiungibile. **CESI**: è un indice proprietario Citi, distribuito
via Bloomberg/Refinitiv — non esiste una fonte gratuita, quindi non è incluso.

**US2Y e TERM_SPREAD non possono stare insieme a US10Y nella stessa
regressione**: `TERM_SPREAD = US10Y − US2Y` è una combinazione lineare
esatta delle altre due, quindi includerle tutte e tre renderebbe la matrice
del modello singolare (OLS non stimabile). Ho tenuto **US10Y (livello) +
TERM_SPREAD (pendenza della curva)** — la scomposizione standard in
econometria — ed escluso US2Y dalla regressione (resta comunque scaricato e
disponibile nel grafico).

**Metodologia di trasformazione**: le serie "price-like" (equity, commodity,
FX, ETF di credito, il fondo stesso) entrano come **rendimento % giornaliero**;
le serie "rate-like" (rendimenti, spread, indici di volatilità VIX/MOVE)
entrano come **variazione di livello** (differenza prima, non %) perché
possono attraversare lo zero — una "% di rendimento" su uno spread non ha
senso. Vedi `RATE_LIKE_FACTORS` in `engine/regression.py`.

## Tre tab: Portafoglio, Analisi & Previsione, Assistente

La dashboard pubblica è divisa in tre viste:

- **Il tuo portafoglio** — la parte personale (login-gated, con **profilazione
  al primo accesso**, vedi sotto): componi il portafoglio, vedi performance e
  concentrazione, e la card **"Top 3 notizie per te"** (vedi sotto).
- **Analisi & Previsione** — la parte **quant pura**, guidata dal portafoglio,
  in 4 sezioni numerate: **1. Regressione OLS** macro per titolo, **2.
  Fama-French-Carhart** (vedi sotto), **3. Indicatori tecnici**, **4. News
  rilevanti** (settore del portafoglio + profilo investitore, vedi sotto).
  Sopra le sezioni: il grafico "Andamento" e l'interpretazione in parole
  semplici. Reagisce al portafoglio.
- **Assistente** — la chat rule-based (`buildChatCard` in `docs/index.html`):
  domande in linguaggio naturale sul portafoglio/mercato, risposte pescate dai
  dati già calcolati. Nessun LLM ancora (beta a costo zero); il prossimo passo
  sarebbe un LLM dietro una Supabase Edge Function riusando il contesto già
  assemblato qui.

## Notizie: "Top 3 per te" (`engine/news.py` + `docs/data/news.json`)

Il primo mattone del "cervello finanziario": una rassegna stampa personalizzata
sul portafoglio dell'utente. Sei fonti — **Yahoo Finance, Investing.com,
MarketWatch, CNBC, Seeking Alpha, Reuters** (quest'ultima via Google News RSS,
avendo Reuters dismesso i propri feed) — lette **solo via RSS ufficiale**:
niente scraping dell'HTML, e ripubblichiamo solo titolo + fonte + link
(stile aggregatore, come Google News), il che ci tiene lontani da problemi
di ToS e copyright.

`run.py` scarica i feed, li deduplica e **tagga ogni titolo** con i ticker
dell'universo (matching sul nome societario e sul simbolo) e con ~12 temi
macro (tassi, inflazione, petrolio, AI & chip, …). Il ranking per-utente
avviene **nel browser** (il portafoglio vive lì): menzioni dei propri titoli
pesano di più, poi temi macro, poi freschezza. La card "📰 Top 3 notizie per
te" sta nella tab Portafoglio e si ri-ordina quando cambi le posizioni; la
chat risponde a "che notizie ci sono?" con le stesse 3, linkate.

Limite onesto: le notizie sono fresche quanto l'ultima esecuzione di
`run.py` + push. Il passo successivo naturale è una **GitHub Action**
schedulata (gratis sui repo pubblici) che rigeneri `news.json` ogni poche ore.

## Analisi fattoriale accademica Fama-French-Carhart (`docs/ff.js`)

La sezione di punta della tab quant. Scompone il rendimento del portafoglio
(pesato) nei quattro fattori di rischio accademici — **Mercato (Mkt-RF),
Dimensione (SMB), Stile value/growth (HML), Momentum (WML)** — con la classica
regressione di Carhart `(r_port − RF) = α + β·fattori`. I beta sono le
inclinazioni del portafoglio (es. "beta di mercato 1.7, tilt large-cap growth,
momentum positivo"); α è l'extra-rendimento non spiegato dai fattori,
presentato con cautela (su un portafoglio personale è quasi sempre non
significativo, e lo diciamo).

I fattori vengono da **Ken French's Data Library** (gratis, standard
accademico): serie "Developed" giornaliere, adatte a un portafoglio globale.
`engine/fetch.py::fetch_ff_factors` li scarica e `run.py` li pubblica in
`docs/data/ff_factors.json`; la regressione gira **nel browser** (`ff.js`
riusa l'OLS di `ols.js`) sul portafoglio dell'utente. Dati di mercato → file
statico, come tutto il resto.

## Regressione OLS macro per titolo (`docs/ols.js`)

La regressione per-asset della tab Analisi gira **interamente nel browser** —
niente Python, perché l'utente costruisce il portafoglio dopo il caricamento
della pagina e Yahoo non è raggiungibile dal browser (CORS). `docs/ols.js`
reimplementa in JavaScript esattamente la stessa metodologia di
`engine/regression.py` (stesse trasformazioni, term_spread derivato, us_2y
escluso, coefficienti/R²/VIF), usando i dati già pubblicati
(`macro_history.json` + `tickers/<SIM>.json`). Verificato: i numeri
coincidono con statsmodels alla 4ª cifra decimale. L'unica approssimazione
deliberata è nei p-value (CDF normale invece della t di Student — con n~1000
osservazioni sono indistinguibili). La sintesi in linguaggio semplice cita
solo i fattori significativi **e** a basso VIF (<10): un coefficiente
significativo ma collineare ha segno inaffidabile, quindi metterlo in una
frase per un utente medio sarebbe fuorviante — resta in tabella, col flag VIF.

## Sezione "Andamento" — grafico e proiezione

Nella tab "Analisi & Previsione" c'è un grafico (`docs/chart.js`, vanilla JS
+ SVG, nessuna libreria esterna) che sovrappone l'asset ai benchmark/macro
selezionati (11 serie disponibili). Tutte le serie sono **indicizzate a
100** all'inizio del periodo visibile, altrimenti non sarebbero confrontabili
(€ vs punti indice vs % vs $). Puoi scegliere il periodo (3M/6M/1Y/5Y/**Dal
2021**) e quali serie mostrare. "Dal 2021" non è un conteggio di giorni come
gli altri pulsanti: è un'ancora fissa al 2021-01-01, così ogni serie —
incluso il NAV del fondo, il cui storico grezzo arriva fino al 2005 — parte
esattamente dalla stessa data invece che "dallo stesso numero di punti fa".
È lo stesso motivo per cui `engine/fetch.py` scarica tutto da una data fissa
(`HISTORY_START_DATE`) invece di una finestra scorrevole: esclude
deliberatamente il crollo/rimbalzo COVID del 2020, che altrimenti
dominerebbe qualunque trend o regressione.

La **proiezione lineare** (checkbox a parte) fitta sempre gli **ultimi 12
mesi** di NAV (`PROJECTION_LOOKBACK_DAYS` in `chart.js`), indipendentemente
dal periodo selezionato per il grafico, ed estende ~63 giorni di trading in
avanti. È **ancorata all'ultimo prezzo reale**, non al valore che la retta
di regressione avrebbe in quel punto — la prima versione usava il valore
della retta fittata sull'intero periodo visibile, che su una finestra lunga
(es. "Dal 2021") poteva restare ben lontana dall'ultimo prezzo reale dopo un
rally o un crollo recente, risultando in una linea tratteggiata "staccata" e
fuorviante. Ancorare all'ultimo prezzo e usare solo lo *slope* della
regressione per la direzione risolve il problema restando comunque
un'estensione geometrica trasparente, non un modello diverso. Mostra sempre
l'**R²** (sul fit a 1 anno) — quanto bene la retta spiega i dati reali — così
quando è basso si vede subito che la proiezione è debole, invece di
nasconderlo. Stessa filosofia "no black-box" del resto del motore — è una
retta, la puoi ricalcolare a mano da `slope`/`intercept` in `chart.js`.

## Pubblico vs privato

Il pilastro "Your position" contiene i tuoi numeri reali (importo investito,
valore portafoglio) — dati che non vuoi finiscano in un repository pubblico.
Per questo:

- `docs/` è la dashboard **pubblica** ed è costruita **attorno al portafoglio
  dell'utente**, non attorno a un asset fisso. La prima tab, **"Il tuo
  portafoglio"**, è l'unica sezione principale: l'utente compone il proprio
  portafoglio nel browser — cerca un titolo/ETF/fondo per nome o ticker, lo
  seleziona, indica importo e data d'acquisto, e vede la performance. Nessuna
  unità da calcolare a mano. Anche **il fondo BlackRock** (il punto di
  partenza reale) si aggiunge così, come qualsiasi altro titolo — il suo
  storico è pubblicato in `docs/data/nav_history.json` e `getTickerHistory()`
  lo mappa al simbolo interno `BGF-SE`.
- La seconda tab, **"Analisi & Previsione"**, contiene tutto il livello
  analitico, guidato dal portafoglio: il grafico con i tuoi titoli, **una
  regressione OLS per ciascun asset** (quanto impatto ha ogni fattore macro),
  e il contesto di mercato (livello e trend dei fattori macro oggi).
- I calcoli (P&L, concentrazione, regressioni) avvengono **interamente nel
  browser** — i dati di mercato restano dati pubblici, nessuno di essi passa
  da un nostro server. Il portafoglio dell'utente (importi, date, titoli)
  viene invece **salvato nel suo account** via Supabase, per ritrovarlo su
  ogni dispositivo (vedi "Login e cloud" sotto).
- `web/` è la dashboard di **sviluppo locale**: mostra tutti i pilastri
  usando i dati reali da `data/position.json`, comoda per uso personale sul
  tuo Mac.

## Login e cloud (Supabase)

La dashboard pubblica richiede **login** (email + password) per usare la parte
personale: il portafoglio si salva nel cloud legato all'account, così non va
rifatto ad ogni accesso e si ritrova su qualsiasi dispositivo.

L'infrastruttura è **Supabase** (Backend-as-a-Service), scelto perché il sito è
statico (GitHub Pages, nessun server nostro): il browser parla direttamente a
Supabase, che gestisce autenticazione e un database Postgres.

- `docs/auth.js` — integrazione client: login/signup/logout + salva/carica il
  portafoglio. Contiene `SUPABASE_URL` e `SUPABASE_ANON_KEY`: sono valori
  **pubblici** (la anon key è progettata per stare nel JS del client). La
  sicurezza è garantita dalla **Row-Level Security** sul DB, non dal segreto
  della chiave.
- Tabella `portfolios` (una riga JSONB per utente, `user_id` = `auth.uid()`)
  con policy RLS che permettono a ciascuno di leggere/scrivere **solo la
  propria** riga. Lo SQL di setup è nella cronologia del progetto.
- Auth: email/password. La conferma via email è disattivata (`autoconfirm`)
  per registrazione immediata; per riattivarla in produzione basta collegare
  un provider SMTP — `docs/index.html` gestisce già il messaggio "conferma la
  mail". `docs/data/nav_history.json` resta pubblico (dato di mercato).

Nota privacy: rispetto alle versioni precedenti (portafoglio solo in
`localStorage`), ora il portafoglio **viene salvato nel cloud** legato
all'account. Resta privato (solo l'utente vi accede, protetto da password +
RLS), ma non è più "mai fuori dal dispositivo".

## Profilo investitore e "News rilevanti" (`docs/profile.js`)

Al primo login, prima di vedere il portafoglio, l'utente risponde a un
**questionario di profilazione in 5 domande** (istruzione/professione,
esperienza con strumenti finanziari, patrimonio ed esposizione, orizzonte
temporale, tolleranza a una perdita del 15%) — gli stessi assi di un
questionario di adeguatezza MiFID, semplificati per uso personale. Le
risposte producono un punteggio 5–15 e un'etichetta (**Conservativo /
Moderato / Dinamico**), salvata nel cloud (tabella `profiles`, stessa logica
RLS di `portfolios`) e modificabile in qualsiasi momento dal bottone
"⚙ Profilo" nella tab "Il tuo portafoglio".

**Robustezza:** se la tabella `profiles` non è ancora stata creata su
Supabase (o è irraggiungibile), il login **non si blocca**: il profilo viene
salvato in un cache `localStorage` per-utente (`investorProfile:<user_id>`) e
il questionario non ricompare a ogni accesso. Senza la tabella si perde però
la sincronizzazione tra dispositivi — creala (SQL sotto) per averla. Questo
evita il bug per cui, mancando la tabella, l'onboarding si ripresentava e il
salvataggio falliva in silenzio.

Il profilo **non cambia i calcoli** (OLS, Fama-French e indicatori tecnici
restano oggettivi) — cambia solo **cosa viene mostrato**: la Sezione 4 della
tab "Analisi & Previsione", **"News rilevanti"**, incrocia i temi macro più
frequenti nelle notizie sui titoli posseduti (il "settore" implicito del
portafoglio, dedotto dai dati invece che auto-dichiarato) con i temi che il
profilo di rischio privilegia (es. Conservativo → tassi/inflazione/volatilità;
Dinamico → AI/cripto/azionario). Resta "descrive, non prescrive": nessun
suggerimento di acquisto/vendita nasce da questo profilo.

Setup Supabase per la tabella `profiles` (SQL Editor, stesso pattern di
`portfolios`):

```sql
create table if not exists profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  answers jsonb not null,
  updated_at timestamptz default now()
);
alter table profiles enable row level security;
create policy "select own profile" on profiles for select using (auth.uid() = user_id);
create policy "insert own profile" on profiles for insert with check (auth.uid() = user_id);
create policy "update own profile" on profiles for update using (auth.uid() = user_id);
```

## Ricerca titoli — perché una lista pre-scaricata, non live

Il browser **non può** chiamare Yahoo Finance direttamente: sia l'endpoint di
ricerca sia quello dei prezzi sono bloccati da CORS (verificato in un browser
reale, non supposto). Quindi la ricerca "digita e trova" e il calcolo di
performance possono funzionare solo per titoli il cui storico **abbiamo già
pubblicato** come file statico sul nostro dominio. Due pezzi:

- `docs/data/ticker_list.json` — lista curata di ~107 titoli/ETF comuni
  (large cap USA, ETF UCITS popolari su Borsa Italiana e non), ognuno
  verificato contro Yahoo. La ricerca digitando avviene **istantaneamente nel
  browser**, senza nessuna chiamata di rete.
- `docs/data/tickers/<SIMBOLO>.json` — lo storico prezzi 2021→oggi di ciascun
  titolo della lista, scaricato in anticipo da `build_ticker_universe.py`.
  Quando l'utente sceglie un titolo, il browser carica solo **quel** file dal
  nostro sito e calcola la performance.

Per estendere la lista (nuovi titoli): aggiungi i simboli in
`build_ticker_universe.py`, rilancialo (`python build_ticker_universe.py` —
ci mette qualche minuto, non va lanciato ad ogni `run.py`), e ripubblica.

## Struttura

```
portfolio_bi/
├── run.py                     # entry point: lega tutto (multi-posizione)
├── build_ticker_universe.py   # pre-scarica lo storico dei titoli cercabili
├── requirements.txt            # numpy, statsmodels
├── data/
│   ├── position.example.json  # template — copialo in position.json
│   ├── position.json          # LA TUA POSIZIONE reale (gitignored)
│   └── scorecard.json         # output completo, locale (gitignored)
├── engine/
│   ├── fetch.py                # NAV fondi (BlackRock) + ticker (Yahoo) + 11 fattori
│   │                            # macro/benchmark, storico dal 2021-01-01, no API key
│   ├── regression.py            # OLS multi-fattoriale (coef/p-value/VIF), portfolio NAV,
│   │                            # sintesi in linguaggio semplice
│   └── scoring.py              # il motore trasparente: pillar_*, build_scorecard_from_pillars
├── web/                       # dashboard di sviluppo locale (tutti i pilastri)
│                                # riusa docs/chart.js e docs/data/*.json
└── docs/                      # dashboard pubblica (GitHub Pages) + input portafoglio
    ├── chart.js                # grafico + proiezione lineare (vanilla JS/SVG)
    └── data/
        ├── scorecard.json      # output pubblico, senza dati personali
        ├── nav_history.json    # storico prezzo prima posizione — dato pubblico
        ├── macro_history.json  # storico 11 fattori macro/benchmark — dato pubblico
        ├── ticker_list.json    # lista curata cercabile (~107 titoli/ETF)
        └── tickers/<SIM>.json  # storico prezzi per ogni titolo cercabile
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
