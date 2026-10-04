/**
 * Tests for the Lifeline text API. Kept out of index.mjs so the Lambda zip
 * does not ship the test runner.
 */
import {
  DEFAULT_ASSUMPTIONS,
  calculateNeeds,
  decodeSession,
  handleTurn,
  handler,
  testOverrides,
} from "./index.mjs";

function assert(condition, label) {
  if (!condition) throw new Error(label);
}

async function runTests() {
  process.env.SESSION_SECRET = process.env.SESSION_SECRET || "test-session-secret";
  const failures = [];
  async function check(label, fn) {
    testOverrides.model = null;
    testOverrides.retrieve = null;
    testOverrides.library = null;
    try {
      await fn();
      console.log(`ok  ${label}`);
    } catch (error) {
      failures.push(`${label}: ${error.message}`);
      console.error(`fail  ${label}: ${error.message}`);
    }
  }

  const validate = (data) => {
    assert(typeof data.session_id === "string" && data.session_id.trim(), "session");
    assert(typeof data.assistant_message === "string" && data.assistant_message.trim(), "message");
    assert(["idle", "collecting", "ready"].includes(data.assessment.status), "status");
    assert(data.assessment.profile && data.assessment.assumptions && Array.isArray(data.assessment.missing_fields), "assessment shape");
    assert(data.needs_assessment && typeof data.disclaimer === "string", "needs");
    if (data.assessment.status === "ready") {
      const needs = data.needs_assessment;
      assert(needs.illustrative_gap >= 0, "gap");
      assert(needs.breakdown.gross_need >= 0 && needs.breakdown.total_offsets >= 0, "totals");
      for (const row of [...needs.breakdown.components, ...needs.breakdown.offsets]) {
        assert(typeof row.label === "string" && row.amount >= 0, "row");
      }
    }
  };

  await check("calculator matches the worked example", async () => {
    const calc = calculateNeeds({
      annual_income: 80_000,
      num_children: 2,
      mortgage_balance: 200_000,
      non_mortgage_debt: 30_000,
      existing_coverage: 100_000,
      liquid_savings: 50_000,
    }, DEFAULT_ASSUMPTIONS);
    assert(calc.breakdown.gross_need === 1_245_000, `gross ${calc.breakdown.gross_need}`);
    assert(calc.breakdown.total_offsets === 150_000, "offsets");
    assert(calc.illustrative_gap === 1_095_000, `gap ${calc.illustrative_gap}`);
  });

  await check("intake asks once, accepts answers, and finishes", async () => {
    let session;
    const replies = ["80000", "2", "200000", "30000", "100000", "50000"];
    const seen = [];
    for (const reply of replies) {
      const turn = await handleTurn({ session_id: session, message: reply, path: "coverage" });
      validate(turn);
      session = turn.session_id;
      seen.push(turn.assessment.next_field);
    }
    const done = await handleTurn({ session_id: session, message: "thanks", path: "coverage" });
    assert(done.assessment.status === "ready", done.assessment.status);
    assert(done.needs_assessment.illustrative_gap === 1_095_000, "finished gap");
    assert(!done.assistant_message.includes("About how much do you earn"), "asked income again");
    assert(seen[0] === "num_children" && seen[4] === "liquid_savings", seen.join(","));
  });

  await check("two unclear replies skip the field instead of looping", async () => {
    const first = await handleTurn({ message: "I'd like an estimate", path: "coverage" });
    assert(first.assessment.next_field === "annual_income", "starts at income");
    const junk1 = await handleTurn({ session_id: first.session_id, message: "asdf", path: "coverage" });
    assert(junk1.assessment.next_field === "annual_income", "still income");
    assert(junk1.assistant_message.includes("skip"), "offers skip");
    const junk2 = await handleTurn({ session_id: junk1.session_id, message: "still nothing", path: "coverage" });
    assert(junk2.assessment.next_field === "num_children", `moved on: ${junk2.assessment.next_field}`);
    assert(junk2.assessment.skipped_fields.includes("annual_income"), "skipped income");
  });

  await check("a question is answered and does not burn the answer attempt", async () => {
    testOverrides.model = () => "A beneficiary is the person who receives the money. What is your mortgage balance?";
    const start = await handleTurn({ message: "I want an estimate", path: "coverage" });
    const asked = await handleTurn({
      session_id: start.session_id,
      message: "What is a beneficiary?",
      path: "coverage",
    });
    assert(asked.assistant_message.includes("person who receives the money"), "answered");
    assert(!asked.assistant_message.includes("mortgage"), "stripped the model's extra question");
    assert(asked.assistant_message.trim().endsWith("?"), "keeps the pending question");
    assert(asked.assessment.next_field === "annual_income", "did not advance");
    const still = await handleTurn({ session_id: asked.session_id, message: "What is term life?", path: "coverage" });
    assert(still.assessment.next_field === "annual_income", "questions do not skip the field");
  });

  await check("invented dollar amounts are discarded", async () => {
    testOverrides.model = () => "You should buy a policy for $999 a month.";
    const start = await handleTurn({ message: "How does life insurance work?", path: "general" });
    assert(!start.assistant_message.includes("999"), start.assistant_message);
    assert(!/per month/i.test(start.assistant_message), "no monthly price");
    assert(start.assessment.status === "idle", "general stays idle");
  });

  await check("policy path does not start the intake", async () => {
    const turn = await handleTurn({
      message: "I make 80000 and I have 2 kids",
      path: "policy",
    });
    assert(turn.assessment.status === "idle", turn.assessment.status);
    assert(turn.assessment.next_field === null, "no next field");
    assert(!turn.assessment.profile.annual_income, "did not store intake");
  });

  await check("a finished estimate stays finished while answering", async () => {
    testOverrides.model = () => "Term life insurance lasts for a set number of years and then ends.";
    let session;
    for (const reply of ["90000", "No dependents", "No mortgage", "No other debts", "No coverage yet", "No savings set aside"]) {
      const turn = await handleTurn({ session_id: session, message: reply, path: "coverage" });
      session = turn.session_id;
    }
    const ready = decodeSession(session);
    assert(ready.profile.annual_income === 90_000, "income stored");
    const follow = await handleTurn({ session_id: session, message: "What is term life insurance?", path: "coverage" });
    assert(follow.assessment.status === "ready", follow.assessment.status);
    assert(follow.assistant_message.includes("set number of years"), follow.assistant_message);
    assert(follow.needs_assessment.illustrative_gap === 915_000, `gap ${follow.needs_assessment.illustrative_gap}`);
  });

  await check("one sentence can fill every field", async () => {
    const turn = await handleTurn({
      path: "coverage",
      message: "I make 80000, I have 2 kids, my mortgage is 200000, I owe 30000, I have 100000 of coverage, and 50000 in savings.",
    });
    assert(turn.assessment.status === "ready", `${turn.assessment.status} missing ${turn.assessment.missing_fields}`);
    assert(turn.needs_assessment.illustrative_gap === 1_095_000, "one-shot gap");
    assert(turn.recommendation.product.name.includes("term"), "recommended term");
  });

  await check("changing an assumption recalculates", async () => {
    const first = await handleTurn({
      path: "coverage",
      message: "I make 80000, I have 2 kids, my mortgage is 200000, I owe 30000, I have 100000 of coverage, and 50000 in savings.",
    });
    const second = await handleTurn({
      session_id: first.session_id,
      path: "coverage",
      message: "Please recalculate with these assumptions.",
      assumption_updates: { income_replacement_years: 20 },
    });
    assert(second.needs_assessment.illustrative_gap === 1_895_000, `recalc ${second.needs_assessment.illustrative_gap}`);
    assert(second.assessment.status === "ready", "still ready");
  });

  await check("comparison does not restart intake", async () => {
    const first = await handleTurn({
      path: "coverage",
      message: "I make 80000, no dependents, no mortgage, no other debts, no coverage yet, no savings set aside.",
    });
    const compare = await handleTurn({ session_id: first.session_id, path: "coverage", message: "Compare alternatives" });
    assert(compare.comparison.has_more === true, "has more");
    assert(compare.assessment.status === "ready", "ready");
    assert(!compare.assistant_message.includes("How many children"), "no intake question");
    const all = await handleTurn({ session_id: compare.session_id, path: "coverage", message: "Show me all my options" });
    assert(all.comparison.has_more === false, "all shown");
    assert(all.assistant_message.includes("Permanent"), "both products");
  });

  await check("skip and not-sure chips advance", async () => {
    const start = await handleTurn({ message: "estimate", path: "coverage" });
    const skip = await handleTurn({ session_id: start.session_id, message: "skip", path: "coverage" });
    assert(skip.assessment.next_field === "num_children", skip.assessment.next_field);
    const unsure = await handleTurn({ session_id: skip.session_id, message: "I'm not sure", path: "coverage" });
    assert(unsure.assessment.next_field === "mortgage_balance", unsure.assessment.next_field);
  });

  await check("document questions are answered from the library", async () => {
    testOverrides.library = [
      { title: "Lifeline Term 20", key: "policies/lifeline-term-20.md" },
      { title: "Permanent Life Insurance", key: "documents/permanent-life-insurance.md" },
    ];
    testOverrides.retrieve = async () => [{
      content: "LifeLine Term 20 is a fictional demo term policy. One sample face amount in the file is $250,000. It is not a real Lincoln product.",
      location: "s3://lifeline-documents/policies/lifeline-term-20.md",
      score: 0.5,
    }];
    const asked = await handleTurn({ message: "what plans are there", path: "general" });
    assert(asked.assessment.status === "idle", asked.assessment.status);
    assert(asked.artifacts.length === 1, "catalog card");
    assert(asked.artifacts[0].markdown.includes("| Lifeline Term 20 | 20-year term |"), asked.artifacts[0].markdown);
    assert(asked.artifacts[0].markdown.includes("Permanent Life Insurance"), "guide missing");
    assert(asked.assistant_message.includes("card below"), asked.assistant_message);
    assert(!asked.assistant_message.includes("Lifeline Term 20"), "plan list stayed in the card");
    assert(!/fictional|not a real|not actual/i.test(asked.artifacts[0].markdown), asked.artifacts[0].markdown);
    testOverrides.model = (prompt) => {
      if (!/Term 20/i.test(prompt)) return "We can look at an estimate together.";
      assert(!/fictional demo|not a real Lincoln/i.test(prompt), "source disclaimer leaked into the prompt");
      assert(prompt.includes("$250,000"), "passage missing");
      return "Lifeline Term 20 is a fictional demo. A sample face amount in the file is $250,000. It is not a real Lincoln product.";
    };
    const detail = await handleTurn({ message: "what does the Term 20 document cover", path: "general" });
    assert(detail.assistant_message.includes("$250,000"), "dropped a figure that was in the document");
    assert(detail.assistant_message.includes("Lifeline Term 20"), detail.assistant_message);
    assert(!/fictional|not a real|not actual/i.test(detail.assistant_message), detail.assistant_message);
    assert(!detail.artifacts, "a single document stays in the reply");
    const wondering = await handleTurn({ message: "Im wondering what plans there are" });
    assert(wondering.assessment.status === "idle", wondering.assessment.status);
    assert(wondering.assessment.next_field === null, "did not start intake");
    assert(wondering.artifacts[0].title === "Plans and guides", wondering.artifacts[0].title);
    const again = await handleTurn({ session_id: wondering.session_id, message: "what plans are there" });
    assert(!again.artifacts, "the card is not sent again");
    assert(again.assistant_message.includes("card above"), again.assistant_message);
    const start = await handleTurn({ message: "I want an estimate", path: "coverage" });
    const during = await handleTurn({ session_id: start.session_id, message: "which policies do you have", path: "coverage" });
    assert(during.assessment.next_field === "annual_income", "did not treat the question as income");
    assert(during.artifacts[0].markdown.includes("Lifeline Term 20"), during.artifacts[0].markdown);
    assert(during.assistant_message.includes("About how much do you earn"), "kept the pending question");
  });

  await check("a demo policy id opens that policy", async () => {
    const opener = await handleTurn({
      message: "I already have a life insurance policy and I'd like help understanding it.",
      path: "policy",
    });
    assert(opener.assessment.status === "idle", opener.assessment.status);
    assert(/is this a demo/i.test(opener.assistant_message), opener.assistant_message);
    assert(!opener.policy_id, "no policy yet");
    const yes = await handleTurn({ session_id: opener.session_id, message: "Yes, this is a demo", path: "policy" });
    assert(yes.assistant_message.includes("DEMO-TERM20-0001"), yes.assistant_message);
    assert(!yes.artifacts, "asking for the id does not invent a document");
    const opened = await handleTurn({ session_id: yes.session_id, message: "DEMO-TERM20-0001", path: "policy" });
    assert(opened.policy_id === "DEMO-TERM20-0001", opened.policy_id);
    assert(opened.artifacts[0].title === "Policy DEMO-TERM20-0001", opened.artifacts?.[0]?.title);
    assert(opened.artifacts[0].markdown.includes("$250,000"), "coverage missing");
    assert(opened.artifacts[0].markdown.includes("None on this policy"), "riders should be empty");
    assert(!/fictional|hackathon/i.test(opened.artifacts[0].markdown), opened.artifacts[0].markdown);
    const kept = decodeSession(opened.session_id);
    assert(kept.policyId === "DEMO-TERM20-0001", "policy did not stay on the session");
    testOverrides.model = (prompt) => {
      assert(prompt.includes("DEMO-TERM20-0001"), "policy context missing");
      assert(prompt.includes("$250,000"), "coverage missing from context");
      assert(/none on this policy/i.test(prompt), "rider fact missing");
      return "Your policy pays $250,000 if you die during the 20-year term. There are no riders on it.";
    };
    const follow = await handleTurn({ session_id: opened.session_id, message: "What does my policy cover?", path: "policy" });
    assert(follow.assistant_message.includes("$250,000"), follow.assistant_message);
    assert(!follow.artifacts, "the card is not sent again on a later question");
    assert(follow.policy_id === "DEMO-TERM20-0001", "policy dropped");
    const other = await handleTurn({ session_id: follow.session_id, message: "DEMO-TERM30-0002", path: "policy" });
    assert(other.policy_id === "DEMO-TERM30-0002", other.policy_id);
    assert(other.artifacts[0].markdown.includes("Waiver of premium"), other.artifacts[0].markdown);
    const missing = await handleTurn({ message: "DEMO-NOPE-9999", path: "policy" });
    assert(!missing.policy_id, "unknown id was stored");
    assert(!missing.artifacts, "unknown id created a document");
    assert(missing.assistant_message.includes("DEMO-NOPE-9999"), missing.assistant_message);
  });

  await check("plans are spoken of as real products", async () => {
    testOverrides.model = () => "Life insurance pays the people you choose if you die. The documents I have access to are explanatory pages and fictional demo policies, such as the Lifeline Variable Protection and Lifeline Indexed Protection, which are not actual products.";
    const turn = await handleTurn({ message: "How does life insurance work?", path: "general" });
    assert(/people you choose/i.test(turn.assistant_message), turn.assistant_message);
    assert(/Lifeline Variable Protection/i.test(turn.assistant_message), turn.assistant_message);
    assert(!/fictional|demo policies|not actual products/i.test(turn.assistant_message), turn.assistant_message);
  });

  await check("a reply that fills nothing is still answered", async () => {
    testOverrides.model = () => "Working part time is still something we can plan around.";
    const start = await handleTurn({ message: "I want an estimate", path: "coverage" });
    const reply = await handleTurn({
      session_id: start.session_id,
      message: "I work part time and I'm not sure how to think about this",
      path: "coverage",
    });
    assert(reply.assessment.next_field === "annual_income", "did not skip the field");
    assert(reply.assistant_message.includes("part time"), reply.assistant_message);
    assert(reply.assistant_message.includes("About how much do you earn"), "kept the question");
    assert(!reply.artifacts, "no repeated card");
  });

  await check("spoken dependents and rough amounts are accepted", async () => {
    const income = await handleTurn({ message: "about 80k a year", path: "coverage" });
    assert(income.assessment.profile.annual_income === 80_000, `income ${income.assessment.profile.annual_income}`);
    const family = await handleTurn({ session_id: income.session_id, message: "a wife and a son", path: "coverage" });
    assert(family.assessment.profile.num_children === 1, `children ${family.assessment.profile.num_children}`);
    assert(family.assistant_message.includes("1 dependent"), family.assistant_message);
    assert(family.assessment.next_field === "mortgage_balance", family.assessment.next_field);
    const mortgage = await handleTurn({ session_id: family.session_id, message: "we rent", path: "coverage" });
    assert(mortgage.assessment.profile.mortgage_balance === 0, "rent is zero mortgage");
    const debt = await handleTurn({ session_id: mortgage.session_id, message: "roughly 33k", path: "coverage" });
    assert(debt.assessment.profile.non_mortgage_debt === 33_000, `debt ${debt.assessment.profile.non_mortgage_debt}`);
    assert(debt.assistant_message.includes("$33,000"), debt.assistant_message);
    const partner = await handleTurn({ message: "just my partner", path: "coverage" });
    const onlyPartner = await handleTurn({ session_id: partner.session_id, message: "90000", path: "coverage" });
    // Income was not asked yet on a fresh session that opened with a family phrase.
    const opened = await handleTurn({ message: "I earn 50000 and I have a husband and two daughters", path: "coverage" });
    assert(opened.assessment.profile.annual_income === 50_000, "income in the same sentence");
    assert(opened.assessment.profile.num_children === 2, `family ${opened.assessment.profile.num_children}`);
    assert(onlyPartner.assessment.profile.annual_income === 90_000, "bare income still works");
  });

  await check("gateway event matches the frontend contract", async () => {
    const event = {
      requestContext: { http: { method: "POST" } },
      rawPath: "/api/turn",
      body: JSON.stringify({ message: "What is a beneficiary?", path: "general", memories: ["Lives in Ohio"] }),
    };
    testOverrides.model = () => "A beneficiary is the person you name to receive the payout.";
    const result = await handler(event);
    assert(result.statusCode === 200, `status ${result.statusCode}`);
    const data = JSON.parse(result.body);
    validate(data);
    assert(data.assistant_message.includes("person you name"), data.assistant_message);
    const review = await handler({
      requestContext: { http: { method: "POST" } },
      rawPath: "/api/submit-review",
      body: JSON.stringify({ session_id: data.session_id, contact: "a@example.com" }),
    });
    const submitted = JSON.parse(review.body);
    assert(review.statusCode === 503, `review status ${review.statusCode}`);
    assert(submitted.ok === false, "review must not claim success when storage is off");
    assert(/not sent/i.test(submitted.error), submitted.error);
  });

  await check("a skipped field does not publish a gap", async () => {
    const start = await handleTurn({ message: "estimate", path: "coverage" });
    let session = start.session_id;
    const skipped = await handleTurn({ session_id: session, message: "skip", path: "coverage" });
    assert(skipped.assessment.skipped_fields.includes("annual_income"), "income skipped");
    session = skipped.session_id;
    let last = skipped;
    for (const reply of ["0", "0", "0", "0", "0"]) {
      last = await handleTurn({ session_id: session, message: reply, path: "coverage" });
      session = last.session_id;
    }
    assert(last.assessment.status === "collecting", last.assessment.status);
    assert(last.needs_assessment.illustrative_gap === null, "blank income became a gap");
    assert(/not zero/i.test(last.assistant_message), last.assistant_message);
    const filled = await handleTurn({ session_id: session, message: "80000", path: "coverage" });
    assert(filled.assessment.profile.annual_income === 80_000, "income filled");
    assert(filled.assessment.status === "ready", filled.assessment.status);
    assert(filled.needs_assessment.illustrative_gap === 815_000, `gap ${filled.needs_assessment.illustrative_gap}`);
  });

  await check("a tampered session is ignored", async () => {
    const first = await handleTurn({ message: "80000", path: "coverage" });
    assert(first.session_id.startsWith("s2."), first.session_id.slice(0, 4));
    const flipped = first.session_id.slice(0, -1) + (first.session_id.endsWith("A") ? "B" : "A");
    const next = await handleTurn({ session_id: flipped, message: "hello there", path: "coverage" });
    assert(next.assessment.profile.annual_income === undefined, "tampered income survived");
  });

  await check("voice route is absent", async () => {
    const result = await handler({ rawPath: "/api/gemini-token", requestContext: { http: { method: "POST" } }, body: "{}" });
    assert(result.statusCode === 404, "gemini omitted");
  });

  if (process.env.LIFELINE_LIVE === "1") {
    await check("live model answers without asking a new question", async () => {
      testOverrides.model = null;
      const turn = await handleTurn({ message: "What is the difference between term and permanent life insurance?", path: "general" });
      assert(/term/i.test(turn.assistant_message) && /permanent|whole life/i.test(turn.assistant_message), turn.assistant_message);
      assert(!turn.assistant_message.includes("?"), turn.assistant_message);
      assert(!/\$\d/.test(turn.assistant_message), turn.assistant_message);
    });
  }

  if (failures.length) {
    console.error(`\n${failures.length} failed`);
    process.exit(1);
  }
  console.log("\nall passed");
}

runTests();
