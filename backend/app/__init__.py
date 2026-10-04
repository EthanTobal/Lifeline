"""Lifeline backend package.

Layered on purpose so arithmetic never mixes with AI generation:

    calculator.py  — deterministic needs math (pure, no AWS, no AI)
    models.py      — customer profile + assessment data model
    bedrock_service.py — isolated Bedrock KB retrieval + model generation
    orchestrator.py    — ties profile + calculator + Bedrock into one response
    config.py      — environment-variable configuration

The LLM may EXPLAIN results. It must never produce authoritative numbers.
"""
