# Docs Agent Silpo MCP allowlist

Derived from the real `docs/silpo_mcp_tools.json` (39 tools, captured
2026-08-25 via `scripts/probe_silpo_mcp.py`: 33 with `readOnlyHint: true`,
6 write). Split by **each tool's own description**, not by name pattern —
`silpo_get_loyalty_info` and `silpo_get_promo_codes` say "for the
authenticated user" despite not matching the `silpo_get_my_*` name prefix,
and a naive prefix rule would have missed them.

Of the 33 read-only tools: **17 non-personal (default allowlist)**,
**16 personal (excluded, `docs/decisions.md` §2)**.

## Default allowlist — 17 non-personal, read-only tools

Enabled now. Each returns catalogue/location/branch data, not an
authenticated user's own profile, orders, or account state.

```
silpo_find_address
silpo_find_nova_poshta_offices
silpo_find_nova_poshta_settlements
silpo_find_products_batch
silpo_get_available_delivery_types
silpo_get_categories
silpo_get_categories_tree
silpo_get_category
silpo_get_popular_categories
silpo_get_product_details
silpo_get_product_sets
silpo_get_products
silpo_get_promotions
silpo_get_replacements
silpo_get_similar_products
silpo_get_time_slots
silpo_list_branches
```

`silpo_get_time_slots`, `silpo_get_available_delivery_types`, and
`silpo_find_products_batch` need branch/delivery parameters that in
practice come from a live cart — noted because the allowlist boundary is
about *what the response contains* (branch/product/slot data), not *what
parameters the caller happens to have on hand*.

## Excluded — 16 personal, read-only tools

Blocked until PII masking plus a Privacy Safety evaluator check on that
masking exist (`docs/decisions.md` §2). Each one's own description names
"the authenticated user" or returns a real person's profile, order,
loyalty, or cart data:

```
silpo_get_coupon_details          # "from silpo_get_my_coupons" — same personal chain
silpo_get_loyalty_info            # "for the authenticated user" — not a silpo_get_my_* name
silpo_get_my_certificates
silpo_get_my_coupons
silpo_get_my_delivery_addresses
silpo_get_my_family
silpo_get_my_favorites
silpo_get_my_food_restrictions
silpo_get_my_offline_orders
silpo_get_my_online_orders
silpo_get_my_premium_subscription
silpo_get_my_profile
silpo_get_my_promos
silpo_get_my_shopping_cart        # also excluded outright below — cart, not just personal
silpo_get_promo_codes             # "for the authenticated user" — not a silpo_get_my_* name
silpo_get_shopping_cart_by_id     # also excluded outright below — cart, not just personal
```

**Independent of the personal/non-personal split:** task §1 says the
system is read-only and never touches the cart. Both cart-reading tools
above (`silpo_get_my_shopping_cart`, `silpo_get_shopping_cart_by_id`) stay
out of every Docs Agent allowlist even if PII masking is later solved —
Docs Agent has no legitimate reason to read cart state at all.

## Write tools (6) — never in any Docs Agent allowlist

```
silpo_add_or_update_cart_products
silpo_add_or_update_certificates
silpo_add_or_update_favorite_products
silpo_clear_shopping_cart
silpo_remove_cart_products
silpo_update_shopping_cart
```

`readOnlyHint: false` on the server confirms these are not accidentally
exposed by a filter bug — a second, independent boundary from the
allowlist above, matching task §1's read-only requirement directly.
