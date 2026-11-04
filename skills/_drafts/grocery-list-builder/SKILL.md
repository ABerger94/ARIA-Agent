---
name: grocery-list-builder
description: Build a grouped grocery list from a meal description.
budget: 10
origin: sample
---

Build a grouped grocery list from a meal the user describes.

1. Take the meal (or meals) the user names. If quantities or servings are unclear, assume 4 servings and say so.
2. Work out the ingredient list for the meal: fresh items first, then pantry items. Use reasonable home-cook quantities.
3. Ask the user which pantry staples they already have (oil, salt, flour, common spices) if they have not said; do not list staples they confirm they own.
4. Group the list by store section, in this order: Produce; Meat and Seafood; Dairy and Eggs; Bakery; Pantry and Dry Goods; Frozen; Household.
5. Within each section, list each item with its quantity, e.g. "Chicken thighs - 2 lb".
6. Present the finished list to the user in chat, section by section, and offer to adjust servings or swap ingredients.
7. If the user names a budget, add a rough per-section subtotal estimate and flag the most expensive items.
