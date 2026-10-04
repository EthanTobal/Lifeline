/* Text chat talks only to the Lifeline API; AWS credentials stay on the server. */
const LifelineBackend = (() => {
  "use strict";
  const API_BASE = (window.LIFELINE_API_BASE || "https://oa8m1sol3h.execute-api.us-east-2.amazonaws.com").replace(/\/$/, "");
  const TURN_URL = API_BASE + "/api/turn";
  const SUBMIT_URL = API_BASE + "/api/submit-review";
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const money = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;

  function validateResponse(data) {
    if (!object(data) || typeof data.session_id !== "string" || !data.session_id.trim() ||
        typeof data.assistant_message !== "string" || !data.assistant_message.trim() ||
        !object(data.assessment) || !["idle", "collecting", "ready"].includes(data.assessment.status) ||
        !object(data.assessment.profile) || !object(data.assessment.assumptions) ||
        !Array.isArray(data.assessment.missing_fields) || !object(data.needs_assessment) ||
        typeof data.disclaimer !== "string" || !data.disclaimer.trim()) {
      throw new Error("The Lifeline service returned an incomplete response. Please try again.");
    }
    if (data.assessment.status === "ready") {
      const needs = data.needs_assessment, breakdown = needs.breakdown;
      if (!money(needs.illustrative_gap) || !object(breakdown) ||
          !money(breakdown.gross_need) || !money(breakdown.total_offsets) ||
          !Array.isArray(breakdown.components) || !Array.isArray(breakdown.offsets) ||
          ![...breakdown.components, ...breakdown.offsets].every((row) =>
            object(row) && typeof row.label === "string" && money(row.amount))) {
        throw new Error("The Lifeline service returned an incomplete assessment. Please try again.");
      }
    }
    return data;
  }

  function createClient({ fetchImpl = (...args) => fetch(...args), timeoutMs = 45000 } = {}) {
    let sessionId, generation = 0;
    const pending = new Set();
    function reset() {
      generation++;
      sessionId = undefined;
      for (const controller of pending) controller.abort();
    }
    async function turn({ message = "", profileUpdates, assumptionUpdates, signal } = {}) {
      if (typeof message !== "string" || (profileUpdates !== undefined && !object(profileUpdates)) ||
          (assumptionUpdates !== undefined && !object(assumptionUpdates))) {
        throw new Error("Please provide valid assessment updates.");
      }
      signal?.throwIfAborted();
      const run = generation, controller = new AbortController();
      pending.add(controller);
      let timedOut = false;
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      try {
        const response = await fetchImpl(TURN_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...(sessionId ? { session_id: sessionId } : {}), message,
            memories: typeof LifelineMemories !== "undefined" ? LifelineMemories.list().map((item) => item.text) : [],
            ...(profileUpdates !== undefined ? { profile_updates: profileUpdates } : {}),
            ...(assumptionUpdates !== undefined ? { assumption_updates: assumptionUpdates } : {}),
          }),
          signal: controller.signal,
        });
        controller.signal.throwIfAborted();
        if (!response.ok) throw new Error(`The Lifeline service could not complete your request (${response.status}). Please try again.`);
        let data;
        try { data = await response.json(); }
        catch { throw new Error("The Lifeline service returned an unreadable response. Please try again."); }
        controller.signal.throwIfAborted();
        if (run !== generation) throw new DOMException("Conversation reset", "AbortError");
        validateResponse(data);
        sessionId = data.session_id;
        return data;
      } catch (error) {
        if (timedOut) throw new Error("The Lifeline service took too long to respond. Please try again.");
        if (controller.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
        if (error.name === "TypeError") throw new Error("Could not reach the Lifeline service. Check your connection and try again.");
        throw error;
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        pending.delete(controller);
      }
    }
    async function submitForReview({ contact = "", signal } = {}) {
      // Hand the collected (securely stored) assessment to a human advisor.
      // Reuses the current conversation's session id.
      if (!sessionId) throw new Error("Start a conversation before sending it for review.");
      signal?.throwIfAborted();
      const controller = new AbortController();
      pending.add(controller);
      let timedOut = false;
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      try {
        const response = await fetchImpl(SUBMIT_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, contact }),
          signal: controller.signal,
        });
        controller.signal.throwIfAborted();
        if (!response.ok) throw new Error(`Could not send your details for review (${response.status}). Please try again.`);
        let data;
        try { data = await response.json(); }
        catch { throw new Error("The Lifeline service returned an unreadable response. Please try again."); }
        if (!object(data) || data.ok !== true || typeof data.reference !== "string") {
          throw new Error(data && data.error ? data.error : "The review request could not be completed. Please try again.");
        }
        return data;  // { ok, reference, status, advisor_notified, message }
      } catch (error) {
        if (timedOut) throw new Error("The Lifeline service took too long to respond. Please try again.");
        if (controller.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
        if (error.name === "TypeError") throw new Error("Could not reach the Lifeline service. Check your connection and try again.");
        throw error;
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        pending.delete(controller);
      }
    }

    return { turn, submitForReview, reset, getSessionId: () => sessionId };
  }
  /* One client shared by every surface (text chat and Gemini Live voice) so a
     user can answer by voice and then continue by text without losing the
     assessment. `createClient` is kept for isolated/testing use. */
  let shared = null;
  function getSharedClient() {
    if (!shared) shared = createClient();
    return shared;
  }

  /* Mint a short-lived Gemini Live credential. The permanent API key stays on
     the server; this returns only the ephemeral token the browser needs. */
  async function requestGeminiToken() {
    const response = await fetch(TURN_URL.replace(/\/api\/turn$/, "/api/gemini-token"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) throw new Error("Voice mode is unavailable right now.");
    const data = await response.json();
    if (data.error || !data.token) throw new Error(data.detail || "Voice mode is unavailable right now.");
    return data;
  }

  return { TURN_URL, SUBMIT_URL, createClient, getSharedClient, requestGeminiToken, validateResponse };
})();
