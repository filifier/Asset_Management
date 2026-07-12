/*
 * profile.js — investor profiling (MiFID-style questionnaire, simplified
 * for personal use). Five questions on education/experience, instruments
 * used, wealth/exposure, time horizon, and loss tolerance — the same
 * axes a real adequacy questionnaire asks. Answered once at login,
 * editable later from "Il tuo portafoglio" (account bar).
 *
 * The profile does NOT change what the platform computes (OLS, Fama-
 * French, technicals stay objective) — it only changes what's SURFACED
 * to the user: which news get priority in "News rilevanti". Still
 * "descrive, non prescrive": no buy/sell suggestion is derived from it.
 */

const PROFILE_QUESTIONS = [
  {
    id: "q1",
    text: "Qual è il suo livello di istruzione e la sua attuale/passata professione?",
    hint: "Definisce la competenza teorica di base.",
    options: [
      { key: "A", text: "Licenza media o diploma superiore — lavoro in settori non economici", points: 1 },
      { key: "B", text: "Laurea o Master — lavoro in settori non economici", points: 2 },
      { key: "C", text: "Laurea in materie economico/giuridiche, o professionista del settore finanziario", points: 3 },
    ],
  },
  {
    id: "q2",
    text: "Con quali strumenti finanziari ha operato negli ultimi 3 anni, e con che frequenza?",
    hint: "Determina l'esperienza pratica con i mercati.",
    options: [
      { key: "A", text: "Solo conti deposito, buoni fruttiferi, o nessun investimento", points: 1 },
      { key: "B", text: "Obbligazioni, BTP, fondi comuni bilanciati — frequenza bassa/media", points: 2 },
      { key: "C", text: "Azioni, ETF azionari, derivati, criptovalute — frequenza alta", points: 3 },
    ],
  },
  {
    id: "q3",
    text: "A quanto ammonta il suo patrimonio totale, e che percentuale ne rappresenta questo investimento?",
    hint: "Valuta la capacità reale di assorbire eventuali perdite.",
    options: [
      { key: "A", text: "Meno di 25.000€ — oltre il 70% del mio patrimonio complessivo", points: 1 },
      { key: "B", text: "Tra 25.000€ e 100.000€ — tra il 30% e il 70% del mio patrimonio", points: 2 },
      { key: "C", text: "Oltre 100.000€ — meno del 30% del mio patrimonio complessivo", points: 3 },
    ],
  },
  {
    id: "q4",
    text: "Qual è l'orizzonte temporale ideale per questo specifico investimento?",
    hint: "Più è lungo, più il profilo può essere dinamico.",
    options: [
      { key: "A", text: "Breve termine — meno di 2 anni", points: 1 },
      { key: "B", text: "Medio termine — da 2 a 5 anni", points: 2 },
      { key: "C", text: "Lungo termine — oltre 5-10 anni", points: 3 },
    ],
  },
  {
    id: "q5",
    text: "Se il suo investimento subisse una perdita improvvisa del 15%, lei come reagirebbe?",
    hint: "Misura la tolleranza psicologica alla volatilità.",
    options: [
      { key: "A", text: "Venderei immediatamente tutto, per evitare ulteriori perdite", points: 1 },
      { key: "B", text: "Manterrei l'investimento, in attesa che il mercato si riprenda", points: 2 },
      { key: "C", text: "Acquisterei altre quote dello stesso investimento, approfittando dei prezzi bassi", points: 3 },
    ],
  },
];

// answers: {q1:"A", q2:"B", ...}. Score range 5–15 → 3 profile labels.
function computeRiskProfile(answers) {
  let score = 0;
  for (const q of PROFILE_QUESTIONS) {
    const opt = q.options.find(o => o.key === (answers && answers[q.id]));
    score += opt ? opt.points : 0;
  }
  let label, desc;
  if (score <= 8) {
    label = "Conservativo";
    desc = "Preferisce stabilità e protezione del capitale: poca esperienza pratica sui mercati e/o poco margine per assorbire perdite.";
  } else if (score <= 12) {
    label = "Moderato";
    desc = "Un equilibrio tra crescita e prudenza, con una discreta esperienza sui mercati.";
  } else {
    label = "Dinamico";
    desc = "Esperienza e capacità di assorbire oscillazioni marcate, in cambio di un potenziale di crescita maggiore.";
  }
  return { score, min: 5, max: 15, label, desc, answers };
}

// Which news topics (see engine/news.py TOPICS) get priority for each
// profile label — Conservativo leans macro/defensive, Dinamico leans
// growth/speculative, Moderato stays neutral (no extra boost).
const PROFILE_TOPIC_BOOST = {
  "Conservativo": ["tassi", "inflazione", "volatilita", "valute", "oro", "geopolitica"],
  "Moderato": [],
  "Dinamico": ["ai-chip", "cripto", "azionario", "energia", "utili"],
};
