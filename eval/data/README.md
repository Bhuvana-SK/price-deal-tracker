# Product-matching evaluation set

`product_pairs.csv` has 84 hand-labelled pairs of listing titles: 31 matches and 53 non-matches. Every brand and product is made up.

## Labelling rule

Two listings are the **same product** (`label = 1`) only when a shopper would pay for exactly the same thing. That means the same:

- brand, model and generation;
- configuration: storage, screen or pan size, capacity, colour, connectivity, carrier and gender fit;
- condition: new, not renewed or refurbished;
- bundle contents: body only vs. with lens, with or without a base or lid.

Accessories for a product are never the product. Rewording, abbreviations, word order, missing filler words and capitalisation don't change the label.

## Kinds

`kind` records why a pair is easy or hard, so errors can be broken down:

| kind | label | meaning |
|---|---|---|
| reworded, abbreviated, reordered, sparse | 1 | the same product described differently |
| variant_storage, variant_size, variant_color, variant_connectivity, variant_carrier, variant_gender | 0 | the same model in a different configuration |
| generation, different_model | 0 | a neighbouring model, such as `K7`/`K8` or `V9`/`V9+` |
| accessory, bundle, condition | 0 | a related item, a bundle, or a refurbished unit |
| different_brand, unrelated | 0 | easy negatives |

## Caveats

- The set is small and synthetic, and the same person wrote the titles, the labels and the matching rules. Treat the results as a sanity check, not a benchmark.
- Pairs with an even `pair_id` are the **dev** split, used to pick thresholds. Pairs with an odd `pair_id` are the **test** split, used only for the reported numbers.
