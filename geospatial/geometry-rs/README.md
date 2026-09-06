# geometry-rs

Deterministic, high-performance geometry validation primitives for the
geospatial platform (Stage 5). Pure safe Rust with **no external crates**.

## Features

- Parse WKT `POLYGON ((...))` and `MULTIPOLYGON (((...)))` (case-insensitive
  keywords, arbitrary whitespace, signed/decimal/scientific coordinates).
- Validate:
  - ring closure (first vertex == last vertex),
  - minimum ring size (at least 4 points),
  - coordinate ranges (longitude in `[-180, 180]`, latitude in `[-90, 90]`),
  - non-zero polygon area,
  - no duplicate consecutive vertices.
- Compute planar shoelace area (holes are subtracted) and bounding boxes.
- `bbox_intersects(a, b)` for bbox overlap checks (touching edges count).

## Library usage

```rust
use geometry_rs::{parse_wkt, validate, geometry_area, Bbox, bbox_intersects};

let geom = parse_wkt("POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))")?;
assert!(validate(&geom).is_empty());
assert_eq!(geometry_area(&geom), 16.0);
let bbox = Bbox::from_geometry(&geom).unwrap();
assert!(bbox_intersects(&bbox, &bbox));
# Ok::<(), geometry_rs::ParseError>(())
```

## CLI usage

```bash
# From an argument
geometry-rs --wkt "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"

# From stdin
echo "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 1, 0 0)))" | geometry-rs
```

Output is a single JSON object (hand-serialized, no JSON crate):

```json
{"valid":true,"errors":[],"area":16.0,"bbox":[0.0,0.0,4.0,4.0]}
```

`bbox` is `[min_x, min_y, max_x, max_y]`; `area`/`bbox` are `null` when the
input cannot be parsed. Exit codes: `0` valid, `1` invalid geometry or parse
error, `2` usage/input error.

## Development

```bash
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
```

The crate is `#![forbid(unsafe_code)]`-compatible by construction: it uses no
`unsafe` blocks and no third-party dependencies.
