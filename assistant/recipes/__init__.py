"""Recipes: how a job went when it went well, fed back so the next model starts ahead.

Complements automations rather than competing with them -- a TOML automation replays a
deterministic task exactly, a recipe informs a fuzzy one. See docs/recipes.md.
"""

from .service import RecipeService
from .store import RecipeStore

__all__ = ["RecipeService", "RecipeStore"]
