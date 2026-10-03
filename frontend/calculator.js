/* =========================================================
   Lifeline — life insurance calculations (the "brain")
   -----------------------------------------------------------------
   WHY THIS FILE EXISTS
   The language model (gemini.js) runs the conversation and explains
   things in plain words. It must NOT do the arithmetic — LLMs are
   unreliable with numbers. This module does every calculation in
   plain, deterministic JavaScript so the result is always correct,
   repeatable, and explainable to a judge or an advisor.

   Methods implemented (all are real, industry-standard approaches):
     1. DIME needs analysis  (Debt + Income + Mortgage + Education)
     2. Human Life Value (HLV) — present value of future income
     3. Income-multiple sanity check (10x rule, range 10-15x)
     4. Rough premium estimation (term, by age/health/sex/amount/term)
     5. Term vs permanent guidance for the user's situation
     6. A printable summary builder (data for the "keep a copy" feature)

   No dependencies. No network. Browser or Node. Exposed as a single
   global `Life` object, matching the `Gemini` pattern in gemini.js.

   IMPORTANT: These are planning estimates for education, not an offer
   of insurance, a quote, or financial advice. Real underwriting and
   pricing come from a licensed carrier. Every result carries a
   `disclaimer` for this reason.
   ========================================================= */

const Life = (() => {
  "use strict";

  /* ---------------------------------------------------------------
     Tunable assumptions, in ONE place so they are easy to defend and
     adjust. Sources: DIME is used by John Hancock and most needs
     calculators; the 10-15x income rule is a common rule of thumb
     (Ramsey uses 10-12x); HLV is the present-value income approach
     (Investopedia). Figures are planning conventions, not quotes.
  --------------------------------------------------------------- */
  const DEFAULTS = Object.freeze({
    incomeReplacementYears: 10,   // DIME "I": common default (range 5-10+)
    educationPerChild: 100000,    // common rule-of-thumb per child (college)
    finalExpenses: 15000,         // funeral + final medical, typical US range
    incomeMultipleLow: 10,        // sanity-check band
    incomeMultipleHigh: 15,
    // HLV present-value assumptions:
    hlvDiscountRate: 0.03,        // real discount rate (after inflation)
    hlvIncomeGrowth: 0.02,        // expected annual income growth
    hlvPersonalConsumption: 0.30, // share of income the person spends on self
    retirementAge: 67,            // when income would have stopped
    maxCoverage: 50000000,        // sanity clamp so typos don't explode
  });

  /* ---------------- small helpers ---------------- */
  const n = (v, dflt = 0) => {
    const x = typeof v === "string" ? Number(v.replace(/[,$\s]/g, "")) : Number(v);
    return Number.isFinite(x) ? x : dflt;
  };
  const clampMoney = (x) => Math.max(0, Math.min(Math.round(x), DEFAULTS.maxCoverage));
  const round = (x, step = 1000) => Math.round(x / step) * step;

  const USD = (x) =>
    "$" + Math.round(n(x)).toLocaleString("en-US", { maximumFractionDigits: 0 });

  const DISCLAIMER =
    "This is an educational estimate to help you plan, not a quote, an offer of " +
    "insurance, or financial advice. Your actual coverage and price are set by a " +
    "licensed insurer after underwriting. Please confirm with a licensed advisor.";

  /* =========================================================
     1. DIME NEEDS ANALYSIS
     Coverage need = Debt + Income replacement + Mortgage + Education
                     - existing coverage - liquid savings/assets
     Returns the total AND an itemized, human-readable breakdown so the
     UI can SHOW the math (transparency builds trust, especially for
     older users and for judging).
  --------------------------------------------------------- */
  function calculateDIME(input = {}) {
    const annualIncome = n(input.annualIncome);
    const years = n(input.incomeReplacementYears, DEFAULTS.incomeReplacementYears);
    const nonMortgageDebt = n(input.nonMortgageDebt);
    const mortgageBalance = n(input.mortgageBalance);
    const numChildren = Math.max(0, Math.round(n(input.numChildren)));
    const perChild = input.educationPerChild != null
      ? n(input.educationPerChild) : DEFAULTS.educationPerChild;
    const finalExpenses = input.finalExpenses != null
      ? n(input.finalExpenses) : DEFAULTS.finalExpenses;

    // Offsets (what the family already has)
    const existingCoverage = n(input.existingCoverage);     // employer + personal
    const liquidSavings = n(input.liquidSavings);           // cash, investments

    const debtComponent = nonMortgageDebt + finalExpenses;
    const incomeComponent = annualIncome * years;
    const mortgageComponent = mortgageBalance;
    const educationComponent = numChildren * perChild;

    const grossNeed =
      debtComponent + incomeComponent + mortgageComponent + educationComponent;
    const offsets = existingCoverage + liquidSavings;
    const netNeed = clampMoney(grossNeed - offsets);

    const breakdown = [
      { key: "debt", label: "Debt + final expenses",
        detail: `Non-mortgage debt ${USD(nonMortgageDebt)} + final expenses ${USD(finalExpenses)}`,
        amount: debtComponent },
      { key: "income", label: "Income replacement",
        detail: `${USD(annualIncome)}/yr × ${years} years`,
        amount: incomeComponent },
      { key: "mortgage", label: "Mortgage payoff",
        detail: `Remaining mortgage balance`,
        amount: mortgageComponent },
      { key: "education", label: "Children's education",
        detail: numChildren
          ? `${numChildren} child${numChildren > 1 ? "ren" : ""} × ${USD(perChild)}`
          : "No children indicated",
        amount: educationComponent },
    ];
    const offsetLines = [
      { key: "existingCoverage", label: "Existing life insurance",
        detail: "Employer + personal policies you already have", amount: existingCoverage },
      { key: "liquidSavings", label: "Savings & liquid assets",
        detail: "Cash and investments available to your family", amount: liquidSavings },
    ];

    return {
      method: "DIME",
      total: round(netNeed),
      grossNeed: round(grossNeed),
      offsets: round(offsets),
      breakdown,
      offsetLines,
      assumptions: { incomeReplacementYears: years, educationPerChild: perChild, finalExpenses },
      explanation:
        `Your family would need about ${USD(grossNeed)} to cover debts, replace ` +
        `${years} years of income, pay off the mortgage, and fund education. ` +
        (offsets > 0
          ? `Subtracting the ${USD(offsets)} you already have leaves a gap of about ${USD(netNeed)}.`
          : `With nothing to offset it, the full ${USD(netNeed)} is the gap to cover.`),
      disclaimer: DISCLAIMER,
    };
  }

  /* =========================================================
     2. HUMAN LIFE VALUE (HLV)
     Present value of the income this person would have contributed to
     the household between now and retirement, net of what they spend
     on themselves, growing with raises and discounted to today.
     Better for working breadwinners; used as a cross-check / "advanced"
     view alongside DIME.
  --------------------------------------------------------- */
  function calculateHLV(input = {}) {
    const annualIncome = n(input.annualIncome);
    const age = n(input.age);
    const retirementAge = n(input.retirementAge, DEFAULTS.retirementAge);
    const growth = input.incomeGrowth != null ? n(input.incomeGrowth) : DEFAULTS.hlvIncomeGrowth;
    const discount = input.discountRate != null ? n(input.discountRate) : DEFAULTS.hlvDiscountRate;
    const personalConsumption =
      input.personalConsumption != null ? n(input.personalConsumption) : DEFAULTS.hlvPersonalConsumption;

    const years = Math.max(0, Math.round(retirementAge - age));
    // Income available to the household (net of what the person spends on self)
    const contribution = annualIncome * (1 - personalConsumption);

    // Sum of present values of each future year's contribution.
    let pv = 0;
    for (let t = 1; t <= years; t++) {
      const futureContribution = contribution * Math.pow(1 + growth, t - 1);
      pv += futureContribution / Math.pow(1 + discount, t);
    }
    const value = clampMoney(pv);

    return {
      method: "Human Life Value",
      total: round(value),
      yearsToRetirement: years,
      assumptions: {
        retirementAge, incomeGrowth: growth, discountRate: discount,
        personalConsumption,
      },
      explanation: years === 0
        ? "At or past the assumed retirement age, the Human Life Value method returns near zero because future earned income is what it measures."
        : `Over ${years} years to retirement, the income this person would have ` +
          `contributed to the household — about ${USD(contribution)}/yr after personal ` +
          `spending, growing ~${Math.round(growth * 100)}%/yr — is worth roughly ` +
          `${USD(value)} in today's dollars.`,
      disclaimer: DISCLAIMER,
    };
  }

  /* =========================================================
     3. INCOME-MULTIPLE SANITY CHECK (10x-15x rule)
     Fast directional cross-check, NOT the primary number.
  --------------------------------------------------------- */
  function incomeMultipleCheck(input = {}) {
    const annualIncome = n(input.annualIncome);
    const low = n(input.low, DEFAULTS.incomeMultipleLow);
    const high = n(input.high, DEFAULTS.incomeMultipleHigh);
    return {
      method: "Income multiple (rule of thumb)",
      low: round(annualIncome * low),
      high: round(annualIncome * high),
      explanation:
        `A quick rule of thumb is ${low}–${high}× your annual income, which is ` +
        `${USD(annualIncome * low)} to ${USD(annualIncome * high)}. Use it only to ` +
        `sanity-check the detailed number, not as the answer itself.`,
      disclaimer: DISCLAIMER,
    };
  }

  /* =========================================================
     4. ROUGH TERM-PREMIUM ESTIMATE
     A transparent, assumption-based monthly cost estimate so the user
     gets a feel for affordability. This is NOT a quote — real pricing
     needs underwriting. Model: base rate per $1,000 of coverage per
     year, scaled by age band, sex, health class, and term length.
     Figures are illustrative planning numbers, intentionally
     conservative, and clearly labeled as estimates.
  --------------------------------------------------------- */
  // base annual cost per $1,000 of coverage at a healthy baseline (age 30, 20yr term)
  const PREMIUM_BASE_PER_1000 = 0.9;
  const AGE_FACTOR = [ // [maxAge, multiplier]
    [29, 0.8], [34, 1.0], [39, 1.3], [44, 1.9], [49, 2.9],
    [54, 4.6], [59, 7.5], [64, 12], [69, 19], [120, 30],
  ];
  const HEALTH_FACTOR = {
    excellent: 0.8, preferred: 0.8,
    good: 1.0, standard: 1.0,
    average: 1.3,
    poor: 1.9, substandard: 1.9,
  };
  const TERM_FACTOR = { 10: 0.75, 15: 0.9, 20: 1.0, 30: 1.4 };

  function estimateTermPremium(input = {}) {
    const coverage = n(input.coverage || input.amount);
    const age = n(input.age, 35);
    const sex = String(input.sex || "").toLowerCase();
    const smoker = input.smoker === true || /yes|smok|tobacco/i.test(String(input.smoker || ""));
    const health = HEALTH_FACTOR[String(input.health || "good").toLowerCase()] ?? 1.0;
    const termYears = n(input.termYears, 20);

    if (coverage <= 0) {
      return { method: "Term premium estimate", available: false,
        explanation: "Enter a coverage amount to estimate a monthly premium.",
        disclaimer: DISCLAIMER };
    }

    const ageMult = (AGE_FACTOR.find(([maxA]) => age <= maxA) || [120, 30])[1];
    const sexMult = sex.startsWith("f") ? 0.85 : 1.0;      // women generally lower
    const smokerMult = smoker ? 2.5 : 1.0;                 // smoking is a big driver
    const termMult = TERM_FACTOR[termYears] ?? 1.0;

    const annual =
      (coverage / 1000) * PREMIUM_BASE_PER_1000 *
      ageMult * sexMult * smokerMult * health * termMult;
    const monthly = annual / 12;

    return {
      method: "Term premium estimate",
      available: true,
      coverage: round(coverage),
      termYears,
      monthly: Math.max(5, Math.round(monthly)),       // floor; carriers have minimums
      annual: Math.max(60, Math.round(annual)),
      factors: { ageMult, sexMult, smokerMult, health, termMult },
      explanation:
        `A rough estimate for ${USD(coverage)} of ${termYears}-year term coverage is ` +
        `about ${USD(Math.max(5, Math.round(monthly)))}/month` +
        (smoker ? " (tobacco use raises this significantly)" : "") +
        `. This is an illustration only — real pricing depends on a health review.`,
      disclaimer: DISCLAIMER,
    };
  }

  /* =========================================================
     5. TERM vs PERMANENT GUIDANCE
     Plain-language, situation-aware comparison (the Path 2 stretch
     goal). Returns both a general explainer and tailored notes.
  --------------------------------------------------------- */
  function compareTermVsPermanent(input = {}) {
    const age = n(input.age);
    const hasDependents = !!(n(input.numChildren) > 0 || input.hasDependents);
    const budgetSensitive = input.budgetSensitive === true;
    const wantsCashValue = input.wantsCashValue === true;
    const needsLifelong = input.lifelongNeed === true ||
      /estate|special needs|funeral|final expense|lifelong|permanent/i.test(String(input.goal || ""));

    const notes = [];
    if (hasDependents && age && age < 55 && !needsLifelong)
      notes.push("With dependents and a time-bound need (mortgage, raising kids), term usually gives the most protection per dollar.");
    if (budgetSensitive)
      notes.push("If budget is the main concern, term costs far less for the same death benefit.");
    if (needsLifelong)
      notes.push("For a need that never ends (final expenses, a special-needs dependent, estate planning), permanent coverage is designed to stay in force for life.");
    if (wantsCashValue)
      notes.push("If building cash value you can borrow against matters to you, that is a feature of permanent policies, not term.");
    if (age && age >= 60 && !needsLifelong)
      notes.push("At older ages, term becomes pricier and harder to qualify for; a smaller permanent or final-expense policy is often the practical choice.");
    if (!notes.length)
      notes.push("Most families start with term for the big, temporary need and consider a smaller permanent policy only for lifelong goals.");

    return {
      method: "Term vs permanent",
      term: {
        summary: "Covers you for a set number of years (e.g. 10, 20, 30). Lower cost, no cash value, protection ends when the term does.",
        bestFor: "Replacing income and covering a mortgage or raising children — needs that end.",
      },
      permanent: {
        summary: "Designed to last your whole life and can build cash value over time. Higher cost.",
        bestFor: "Lifelong needs: final expenses, a dependent who will always need support, or estate planning.",
      },
      tailoredNotes: notes,
      disclaimer: DISCLAIMER,
    };
  }

  /* =========================================================
     6. RECOMMENDATION — tie the methods together
     Lead with DIME, cross-check with the income multiple, offer HLV
     as the advanced view, and flag when they disagree a lot.
  --------------------------------------------------------- */
  function recommend(input = {}) {
    const dime = calculateDIME(input);
    const multiple = incomeMultipleCheck(input);
    const hlv = n(input.age) ? calculateHLV(input) : null;

    // Is DIME within/near the sanity band?
    const withinBand = dime.grossNeed >= multiple.low * 0.6 && dime.grossNeed <= multiple.high * 1.4;
    const flags = [];
    if (!withinBand && n(input.annualIncome) > 0) {
      flags.push(
        `Your detailed (DIME) figure of ${USD(dime.grossNeed)} is outside the quick ` +
        `${DEFAULTS.incomeMultipleLow}–${DEFAULTS.incomeMultipleHigh}× band ` +
        `(${USD(multiple.low)}–${USD(multiple.high)}). Worth double-checking the inputs.`
      );
    }
    if (dime.total === 0 && dime.grossNeed > 0)
      flags.push("Your existing coverage and savings already meet the estimated need — you may not need more.");

    return {
      recommendedCoverage: dime.total,
      primary: dime,
      sanityCheck: multiple,
      humanLifeValue: hlv,
      flags,
      disclaimer: DISCLAIMER,
    };
  }

  /* =========================================================
     7. INPUT CHECKLIST — what the conversation should collect
     The bot can use this to know what it still needs to ask, and to
     explain WHY each item matters (good for older users).
  --------------------------------------------------------- */
  const INPUT_FIELDS = Object.freeze([
    { key: "annualIncome", label: "Your annual income", why: "Drives how much income your family would need to replace.", required: true },
    { key: "age", label: "Your age", why: "Affects the advanced calculation and the cost estimate.", required: false },
    { key: "sex", label: "Sex (for pricing)", why: "Insurers price men and women differently.", required: false },
    { key: "smoker", label: "Do you use tobacco?", why: "Tobacco use is one of the biggest cost factors.", required: false },
    { key: "health", label: "General health", why: "Health class affects the price, not the amount you need.", required: false },
    { key: "numChildren", label: "Number of children/dependents", why: "Used for education costs and whether coverage is needed at all.", required: false },
    { key: "mortgageBalance", label: "Mortgage balance", why: "Often the largest single debt to pay off.", required: false },
    { key: "nonMortgageDebt", label: "Other debts", why: "Credit cards, car loans, student loans the family would inherit.", required: false },
    { key: "existingCoverage", label: "Existing life insurance", why: "Employer and personal policies reduce what you still need.", required: false },
    { key: "liquidSavings", label: "Savings & investments", why: "Money already available to your family reduces the gap.", required: false },
    { key: "incomeReplacementYears", label: "Years of income to replace", why: "How long your family would lean on the payout (default 10).", required: false },
  ]);

  function missingFields(input = {}) {
    return INPUT_FIELDS.filter((f) => {
      const v = input[f.key];
      return f.required && (v == null || v === "" || n(v) === 0);
    }).map((f) => f.key);
  }

  /* =========================================================
     8. PRINTABLE SUMMARY (data for the "keep a copy" feature)
     Produces a plain object the UI turns into an on-screen + printable
     document. Older users want something tangible they can print,
     save, or share with family / an advisor.
  --------------------------------------------------------- */
  function buildSummary(input = {}, opts = {}) {
    const rec = recommend(input);
    const tvp = compareTermVsPermanent(input);
    const premium = estimateTermPremium({
      ...input,
      coverage: input.coverage || rec.recommendedCoverage,
      termYears: input.termYears || DEFAULTS.incomeReplacementYears,
    });

    return {
      title: "Your Life Insurance Summary",
      createdAt: new Date().toISOString(),
      forName: input.name || opts.name || "",
      yourAnswers: INPUT_FIELDS
        .filter((f) => input[f.key] != null && input[f.key] !== "")
        .map((f) => ({ label: f.label, value: input[f.key] })),
      recommendedCoverage: rec.recommendedCoverage,
      recommendedCoverageText: USD(rec.recommendedCoverage),
      howWeGotThere: rec.primary.breakdown,
      offsets: rec.primary.offsetLines,
      sanityCheck: rec.sanityCheck,
      humanLifeValue: rec.humanLifeValue,
      estimatedMonthlyPremium: premium.available ? premium.monthly : null,
      estimatedMonthlyPremiumText: premium.available ? USD(premium.monthly) + "/month" : null,
      termVsPermanent: tvp,
      flags: rec.flags,
      nextSteps: [
        "Review these numbers and correct anything that looks off.",
        "Confirm your existing employer coverage — people often forget it.",
        "Talk to a licensed advisor to turn this estimate into a real quote.",
        "Keep this summary; you can print it or save it as a PDF.",
      ],
      disclaimer: DISCLAIMER,
    };
  }

  /* Minimal, self-contained print: open the summary in the browser's
     print dialog (works as "Save as PDF" everywhere). The UI can also
     render the same data inline in a side panel. */
  function printableHTML(summary) {
    const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    const row = (l, r) => `<tr><td>${esc(l)}</td><td style="text-align:right">${esc(r)}</td></tr>`;
    const needRows = summary.howWeGotThere
      .map((b) => row(`${b.label} — ${b.detail}`, USD(b.amount))).join("");
    const offsetRows = summary.offsets
      .filter((o) => n(o.amount) > 0)
      .map((o) => row(`Less: ${o.label}`, "−" + USD(o.amount))).join("");
    return `<!doctype html><html><head><meta charset="utf-8">
<title>${esc(summary.title)}</title>
<style>
  body{font:16px/1.5 Roboto,Arial,sans-serif;color:#1a1a1a;max-width:720px;margin:40px auto;padding:0 20px}
  h1{color:#650030;font-size:28px;margin-bottom:4px}
  h2{color:#650030;font-size:19px;margin-top:28px;border-bottom:2px solid #eee;padding-bottom:4px}
  table{width:100%;border-collapse:collapse;margin:8px 0}
  td{padding:6px 4px;border-bottom:1px solid #eee}
  .total{font-size:24px;font-weight:700;color:#650030}
  .note{background:#f6f1f3;border-left:4px solid #650030;padding:12px 16px;margin:16px 0;border-radius:6px}
  .fine{color:#666;font-size:13px;margin-top:28px}
  ul{padding-left:20px} li{margin:6px 0}
  @media print{body{margin:0}}
</style></head><body>
<h1>${esc(summary.title)}</h1>
${summary.forName ? `<p>Prepared for <strong>${esc(summary.forName)}</strong></p>` : ""}
<p>${new Date(summary.createdAt).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}</p>

<div class="note">Your estimated coverage need:
  <div class="total">${esc(summary.recommendedCoverageText)}</div></div>

<h2>How we got there</h2>
<table>${needRows}${offsetRows}
  <tr><td><strong>Estimated coverage gap</strong></td>
      <td style="text-align:right"><strong>${esc(summary.recommendedCoverageText)}</strong></td></tr>
</table>

${summary.estimatedMonthlyPremiumText ? `<h2>Rough monthly cost</h2>
<p>About <strong>${esc(summary.estimatedMonthlyPremiumText)}</strong> for term coverage — an illustration only, not a quote.</p>` : ""}

<h2>Term vs. permanent, for you</h2>
<p><strong>Term:</strong> ${esc(summary.termVsPermanent.term.summary)}</p>
<p><strong>Permanent:</strong> ${esc(summary.termVsPermanent.permanent.summary)}</p>
<ul>${summary.termVsPermanent.tailoredNotes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>

<h2>Next steps</h2>
<ul>${summary.nextSteps.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>

<p class="fine">${esc(summary.disclaimer)}</p>
</body></html>`;
  }

  /* Open the printable summary in a new window and trigger print.
     Returns false if the browser blocked the popup. */
  function printSummary(summary) {
    const html = printableHTML(summary);
    const w = window.open("", "_blank");
    if (!w) return false;
    w.document.write(html);
    w.document.close();
    w.focus();
    setTimeout(() => { try { w.print(); } catch { /* user can print manually */ } }, 300);
    return true;
  }

  /* =========================================================
     Public API
  --------------------------------------------------------- */
  return {
    DEFAULTS,
    // formatting / util
    USD, DISCLAIMER,
    // calculations
    calculateDIME,
    calculateHLV,
    incomeMultipleCheck,
    estimateTermPremium,
    compareTermVsPermanent,
    recommend,
    // conversation support
    INPUT_FIELDS,
    missingFields,
    // output / "keep a copy"
    buildSummary,
    printableHTML,
    printSummary,
  };
})();

/* Make it usable from Node too (for quick tests), harmless in browser. */
if (typeof module !== "undefined" && module.exports) module.exports = Life;
