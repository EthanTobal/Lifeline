# LifeLine Demo Policy Matching Rules

> **HACKATHON DEMO LOGIC**
>
> These rules are educational matching logic for fictional products. They do not represent Lincoln Financial underwriting, suitability standards, product recommendations, or financial advice.

## Separation of responsibilities

LifeLine should first calculate an illustrative financial need independently of any product.

`Customer inputs -> Needs assessment -> Illustrative coverage gap`

Only after the assessment is complete should the application compare demo product structures.

`Illustrative assessment + stated goals -> Demo policy comparison`

## Signals used for comparison

### Time horizon
If the user's obligations are clearly time-bound, term examples can be surfaced for comparison.

- Around 20 years or less: LifeLine Term 20 may be included.
- Longer time-bound obligations: LifeLine Term 30 may be included.
- Long-term/lifetime interest: permanent examples may be included.

These are demo rules, not suitability determinations.

### Cash-value interest
If the user specifically wants to learn about potential cash value, the application may include the IUL and VUL demo examples in the comparison.

### Simplicity
If the user emphasizes straightforward temporary protection, term examples can be surfaced before more complex permanent examples.

### Market exposure
If the user asks about index-linked potential, surface the IUL demo for education.
If the user asks about investment-option market exposure, surface the VUL demo for education.

### Affordability
Affordability is a context signal, not part of the needs formula. LifeLine must not reduce a calculated financial need simply because the user provides a lower budget.

The MVP should avoid presenting invented premium quotes as if they were real pricing.

## Output language

Allowed:
- "Here are demo policy structures you can compare."
- "This example aligns with the time horizon you entered."
- "This option illustrates longer-term coverage with potential cash value."
- "Here are the tradeoffs between these examples."

Avoid:
- "We recommend this policy."
- "This is the best policy for you."
- "You should buy this."
- "Lincoln would approve you for this policy."
- "Your premium will be..."
- "You qualify for..."

## Required UI label

Every fictional policy card should display:

**Hackathon Demo — Not an actual Lincoln Financial product or quote**
