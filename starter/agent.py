"""Entry point for the official evaluator.

evaluator/local_evaluator.py:12 hardcodes `from starter.agent import Agent`, and
docs/submission_rules.md forbids modifying evaluator files. So this module stays where
it is and re-exports the real implementation from src/.

The organiser's own instructions permit this: "Participants can modify or replace the
starter Agent while continuing to use the official local evaluator."
"""

from src.agent import Agent

__all__ = ["Agent"]
