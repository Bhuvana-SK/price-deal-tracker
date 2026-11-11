Product matching on the held-out **test split** (42 pairs; thresholds tuned on 42 dev pairs). Embedding model: `BAAI/bge-small-en-v1.5`.

| Method | Precision | Recall | F1 | Accuracy | LLM calls |
|---|---:|---:|---:|---:|---:|
| Fuzzy string match (baseline) | 38% | 94% | 0.55 | 40% | n/a |
| Embeddings only | 43% | 100% | 0.60 | 50% | n/a |
| Embeddings + spec rules | 46% | 100% | 0.63 | 55% | n/a |

Thresholds (picked on dev): fuzzy ≥ 0.526; embeddings ≥ 0.833; embeddings after rules ≥ 0.833. In the Claude pipeline, embeddings only rule pairs **out** (similarity < 0.813, which keeps every dev match) and Claude verifies the rest. Hard negatives such as `V9` vs `V9+` score up to 0.99, so no similarity is safe to auto-accept.

The Claude row needs `ANTHROPIC_API_KEY` (or cached verdicts); run `tracker eval-matching --live` once a key is set.

<details><summary>Errors made by "Embeddings + spec rules"</summary>

- FP [variant_color] Auralis NC700 Wireless Headphones - Black | Auralis NC700 Wireless Headphones - Silver
- FP [accessory] Auralis NC700 Wireless Headphones | Replacement Ear Pads for Auralis NC700 Headphones
- FP [variant_color] Kinetix K8 128GB Graphite | Kinetix K8 128GB Glacier Blue
- FP [variant_carrier] Kinetix K8 128GB Unlocked | Kinetix K8 128GB Carrier Locked (NetOne)
- FP [different_model] Lumora OLED55 55-Inch 4K TV | Lumora QLED55 55-Inch 4K TV
- FP [bundle] Brewix Pro Espresso Machine | Brewix Pro Espresso Machine + Grinder Bundle
- FP [generation] Stridex Run 3 Running Shoes | Stridex Run 4 Running Shoes
- FP [accessory] Stridex Run 3 Running Shoes | Stridex Performance Running Socks (3 Pairs)
- FP [different_model] Voltra V9 Robot Vacuum | Voltra V7 Robot Vacuum
- FP [variant_connectivity] Novapad 11 Tablet 256GB Wi-Fi | Novapad 11 Tablet 256GB Wi-Fi + Cellular
- FP [variant_size] Novapad 11 Tablet 256GB | Novapad 13 Tablet 256GB
- FP [different_model] Glidewave GW27U 27-inch 4K Monitor | Glidewave GW27Q 27-inch QHD Monitor
- FP [accessory] Glidewave GW27U Monitor | Glidewave GW27U Monitor Arm Mount
- FP [bundle] Ferro 12-inch Cast Iron Skillet | Ferro 12-inch Cast Iron Skillet with Lid
- FP [bundle] Pixelor Z50 Mirrorless Camera Body | Pixelor Z50 Mirrorless Camera with 18-55mm Lens Kit
- FP [different_model] Hearthly 6-Quart Air Fryer | Hearthly 6-Quart Air Fryer Oven with Rotisserie
- FP [variant_color] Hearthly 6-Quart Air Fryer, Black | Hearthly 6-Quart Air Fryer, Stainless
- FP [different_brand] Kinetix K8 Smartphone 128GB | Zenphone Z8 Smartphone 128GB
- FP [bundle] Ferro 12-inch Cast Iron Skillet | Ferro Cast Iron Skillet Set (8, 10, 12 inch)

</details>
