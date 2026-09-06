//! Deterministic geometry validation primitives for WKT `POLYGON` and
//! `MULTIPOLYGON`.
//!
//! Pure safe Rust, no external crates. Provides:
//!
//! - WKT parsing (`parse_wkt`)
//! - Validation: ring closure, minimum ring size, coordinate ranges,
//!   non-zero area, no duplicate consecutive vertices (`validate`)
//! - Planar shoelace area (`ring_area`, `polygon_area`, `geometry_area`)
//! - Bounding boxes (`Bbox`) and `bbox_intersects`

use std::fmt;

/// A 2D coordinate (longitude/x, latitude/y).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

/// A linear ring: a sequence of points. A valid ring has at least 4 points
/// and is closed (first point == last point).
pub type Ring = Vec<Point>;

/// A polygon: an exterior ring followed by zero or more interior rings (holes).
#[derive(Debug, Clone, PartialEq)]
pub struct Polygon {
    pub rings: Vec<Ring>,
}

/// Parsed geometry: either a single polygon or a multipolygon.
#[derive(Debug, Clone, PartialEq)]
pub enum Geometry {
    Polygon(Polygon),
    MultiPolygon(Vec<Polygon>),
}

/// Axis-aligned bounding box.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Bbox {
    pub min_x: f64,
    pub min_y: f64,
    pub max_x: f64,
    pub max_y: f64,
}

impl Bbox {
    /// Compute the bbox covering all points of the given rings.
    pub fn from_rings<'a, I>(rings: I) -> Option<Bbox>
    where
        I: IntoIterator<Item = &'a Ring>,
    {
        let mut iter = rings.into_iter().flat_map(|r| r.iter());
        let first = iter.next()?;
        let mut b = Bbox {
            min_x: first.x,
            min_y: first.y,
            max_x: first.x,
            max_y: first.y,
        };
        for p in iter {
            if p.x < b.min_x {
                b.min_x = p.x;
            }
            if p.y < b.min_y {
                b.min_y = p.y;
            }
            if p.x > b.max_x {
                b.max_x = p.x;
            }
            if p.y > b.max_y {
                b.max_y = p.y;
            }
        }
        Some(b)
    }

    /// Compute the bbox of a whole geometry.
    pub fn from_geometry(geom: &Geometry) -> Option<Bbox> {
        match geom {
            Geometry::Polygon(p) => Bbox::from_rings(p.rings.iter()),
            Geometry::MultiPolygon(polys) => {
                Bbox::from_rings(polys.iter().flat_map(|p| p.rings.iter()))
            }
        }
    }
}

/// Returns true when two bounding boxes intersect (inclusive edges).
pub fn bbox_intersects(a: &Bbox, b: &Bbox) -> bool {
    a.min_x <= b.max_x && b.min_x <= a.max_x && a.min_y <= b.max_y && b.min_y <= a.max_y
}

/// Parse error with a human-readable message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError(pub String);

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for ParseError {}

/// Minimal tokenizer/parser for WKT `POLYGON` and `MULTIPOLYGON`.
struct Parser<'a> {
    chars: Vec<char>,
    pos: usize,
    src: &'a str,
}

impl<'a> Parser<'a> {
    fn new(src: &'a str) -> Self {
        Parser {
            chars: src.chars().collect(),
            pos: 0,
            src,
        }
    }

    fn skip_ws(&mut self) {
        while self.pos < self.chars.len() && self.chars[self.pos].is_whitespace() {
            self.pos += 1;
        }
    }

    fn peek(&mut self) -> Option<char> {
        self.skip_ws();
        self.chars.get(self.pos).copied()
    }

    fn expect_char(&mut self, c: char) -> Result<(), ParseError> {
        match self.peek() {
            Some(actual) if actual == c => {
                self.pos += 1;
                Ok(())
            }
            Some(actual) => Err(ParseError(format!(
                "expected '{c}' but found '{actual}' at offset {}",
                self.pos
            ))),
            None => Err(ParseError(format!(
                "expected '{c}' but reached end of input"
            ))),
        }
    }

    fn expect_keyword(&mut self, kw: &str) -> Result<(), ParseError> {
        self.skip_ws();
        let end = (self.pos + kw.len()).min(self.chars.len());
        let candidate: String = self.chars[self.pos..end].iter().collect();
        if candidate.eq_ignore_ascii_case(kw) {
            // Ensure the keyword is not a prefix of a longer identifier.
            match self.chars.get(end) {
                Some(c) if c.is_ascii_alphabetic() => Err(ParseError(format!(
                    "unexpected keyword near offset {} in {:?}",
                    self.pos, self.src
                ))),
                _ => {
                    self.pos = end;
                    Ok(())
                }
            }
        } else {
            Err(ParseError(format!(
                "expected keyword '{kw}' at offset {}",
                self.pos
            )))
        }
    }

    fn parse_number(&mut self) -> Result<f64, ParseError> {
        self.skip_ws();
        let start = self.pos;
        if matches!(self.chars.get(self.pos), Some('+') | Some('-')) {
            self.pos += 1;
        }
        let mut seen_digit = false;
        while let Some(c) = self.chars.get(self.pos) {
            match c {
                '0'..='9' => {
                    seen_digit = true;
                    self.pos += 1;
                }
                '.' | 'e' | 'E' => {
                    self.pos += 1;
                    // Allow sign only immediately after exponent marker.
                    if matches!(c, 'e' | 'E')
                        && matches!(self.chars.get(self.pos), Some('+') | Some('-'))
                    {
                        self.pos += 1;
                    }
                }
                _ => break,
            }
        }
        if !seen_digit {
            return Err(ParseError(format!(
                "expected number at offset {start}"
            )));
        }
        let text: String = self.chars[start..self.pos].iter().collect();
        text.parse::<f64>()
            .map_err(|_| ParseError(format!("invalid number '{text}'")))
    }

    fn parse_point(&mut self) -> Result<Point, ParseError> {
        // Optional parenthesized point form: "(x y)" or bare "x y".
        let parenthesized = self.peek() == Some('(');
        if parenthesized {
            self.expect_char('(')?;
        }
        let x = self.parse_number()?;
        let y = self.parse_number()?;
        if parenthesized {
            self.expect_char(')')?;
        }
        Ok(Point { x, y })
    }

    fn parse_ring(&mut self) -> Result<Ring, ParseError> {
        self.expect_char('(')?;
        let mut ring = Vec::new();
        loop {
            ring.push(self.parse_point()?);
            match self.peek() {
                Some(',') => {
                    self.pos += 1;
                }
                Some(')') => {
                    self.pos += 1;
                    break;
                }
                other => {
                    return Err(ParseError(format!(
                        "expected ',' or ')' in ring but found {other:?} at offset {}",
                        self.pos
                    )))
                }
            }
        }
        Ok(ring)
    }

    /// Parse a polygon body: `((ring), (ring), ...)` — the outer parens
    /// included. Used for both POLYGON bodies and MULTIPOLYGON members.
    fn parse_polygon_body(&mut self) -> Result<Polygon, ParseError> {
        self.expect_char('(')?;
        let mut rings = Vec::new();
        loop {
            rings.push(self.parse_ring()?);
            match self.peek() {
                Some(',') => {
                    self.pos += 1;
                }
                Some(')') => {
                    self.pos += 1;
                    break;
                }
                other => {
                    return Err(ParseError(format!(
                        "expected ',' or ')' in polygon but found {other:?} at offset {}",
                        self.pos
                    )))
                }
            }
        }
        Ok(Polygon { rings })
    }

    fn parse_geometry(&mut self) -> Result<Geometry, ParseError> {
        self.skip_ws();
        let rest: String = self.chars[self.pos..].iter().collect();
        let upper = rest.to_ascii_uppercase();
        let geom = if upper.starts_with("MULTIPOLYGON") {
            self.expect_keyword("MULTIPOLYGON")?;
            // Reject "MULTIPOLYGON EMPTY" — we require actual coordinates.
            if self.peek() != Some('(') {
                return Err(ParseError(
                    "MULTIPOLYGON must be followed by '(' (EMPTY not supported)".into(),
                ));
            }
            self.expect_char('(')?;
            let mut polys = Vec::new();
            loop {
                polys.push(self.parse_polygon_body()?);
                match self.peek() {
                    Some(',') => {
                        self.pos += 1;
                    }
                    Some(')') => {
                        self.pos += 1;
                        break;
                    }
                    other => {
                        return Err(ParseError(format!(
                            "expected ',' or ')' in multipolygon but found {other:?} at offset {}",
                            self.pos
                        )))
                    }
                }
            }
            Geometry::MultiPolygon(polys)
        } else if upper.starts_with("POLYGON") {
            self.expect_keyword("POLYGON")?;
            if self.peek() != Some('(') {
                return Err(ParseError(
                    "POLYGON must be followed by '(' (EMPTY not supported)".into(),
                ));
            }
            Geometry::Polygon(self.parse_polygon_body()?)
        } else {
            return Err(ParseError(
                "unsupported geometry type: only POLYGON and MULTIPOLYGON are supported".into(),
            ));
        };
        self.skip_ws();
        if self.pos != self.chars.len() {
            return Err(ParseError(format!(
                "trailing input after geometry at offset {}",
                self.pos
            )));
        }
        Ok(geom)
    }
}

/// Parse a WKT `POLYGON` or `MULTIPOLYGON` string (case-insensitive keyword,
/// optional whitespace). Z/M ordinates are not supported.
pub fn parse_wkt(input: &str) -> Result<Geometry, ParseError> {
    Parser::new(input).parse_geometry()
}

/// Signed planar shoelace area of a ring. Positive for counter-clockwise
/// winding, negative for clockwise (in x/y coordinate order).
pub fn ring_area(ring: &Ring) -> f64 {
    if ring.len() < 2 {
        return 0.0;
    }
    let mut sum = 0.0;
    for pair in ring.windows(2) {
        sum += pair[0].x * pair[1].y - pair[1].x * pair[0].y;
    }
    sum / 2.0
}

/// Polygon area: |exterior| minus sum(|holes|). Never negative.
pub fn polygon_area(poly: &Polygon) -> f64 {
    let mut area = 0.0;
    for (i, ring) in poly.rings.iter().enumerate() {
        let a = ring_area(ring).abs();
        if i == 0 {
            area += a;
        } else {
            area -= a;
        }
    }
    area.max(0.0)
}

/// Total area of a geometry (sum over polygon members).
pub fn geometry_area(geom: &Geometry) -> f64 {
    match geom {
        Geometry::Polygon(p) => polygon_area(p),
        Geometry::MultiPolygon(polys) => polys.iter().map(polygon_area).sum(),
    }
}

/// Minimum number of points in a ring (3 distinct vertices + closing point).
pub const MIN_RING_SIZE: usize = 4;
/// Valid longitude range.
pub const LON_RANGE: (f64, f64) = (-180.0, 180.0);
/// Valid latitude range.
pub const LAT_RANGE: (f64, f64) = (-90.0, 90.0);

fn points_equal(a: &Point, b: &Point) -> bool {
    a.x == b.x && a.y == b.y
}

fn validate_ring(ring: &Ring, label: &str, errors: &mut Vec<String>) {
    if ring.len() < MIN_RING_SIZE {
        errors.push(format!(
            "{label}: ring has {} points, minimum is {MIN_RING_SIZE}",
            ring.len()
        ));
        // Range/duplicate checks below are still meaningful.
    }
    if ring.len() >= 2 && !points_equal(&ring[0], &ring[ring.len() - 1]) {
        errors.push(format!("{label}: ring is not closed"));
    }
    for (i, p) in ring.iter().enumerate() {
        if !p.x.is_finite() || !p.y.is_finite() {
            errors.push(format!("{label}: vertex {i} has non-finite coordinate"));
            continue;
        }
        if p.x < LON_RANGE.0 || p.x > LON_RANGE.1 {
            errors.push(format!(
                "{label}: vertex {i} longitude {} out of range [{}, {}]",
                p.x, LON_RANGE.0, LON_RANGE.1
            ));
        }
        if p.y < LAT_RANGE.0 || p.y > LAT_RANGE.1 {
            errors.push(format!(
                "{label}: vertex {i} latitude {} out of range [{}, {}]",
                p.y, LAT_RANGE.0, LAT_RANGE.1
            ));
        }
    }
    for (i, pair) in ring.windows(2).enumerate() {
        if points_equal(&pair[0], &pair[1]) {
            errors.push(format!(
                "{label}: duplicate consecutive vertices at index {i}"
            ));
        }
    }
}

fn validate_polygon(poly: &Polygon, label: &str, errors: &mut Vec<String>) {
    if poly.rings.is_empty() {
        errors.push(format!("{label}: polygon has no rings"));
        return;
    }
    for (i, ring) in poly.rings.iter().enumerate() {
        let ring_label = if i == 0 {
            format!("{label} exterior ring")
        } else {
            format!("{label} interior ring {i}")
        };
        validate_ring(ring, &ring_label, errors);
    }
    // Area is only meaningful if the rings are structurally valid.
    let structurally_ok = poly
        .rings
        .iter()
        .all(|r| r.len() >= MIN_RING_SIZE && points_equal(&r[0], &r[r.len() - 1]));
    if structurally_ok && polygon_area(poly) <= 0.0 {
        errors.push(format!("{label}: polygon has zero area"));
    }
}

/// Validate a parsed geometry. Returns a list of human-readable errors;
/// an empty list means the geometry is valid.
pub fn validate(geom: &Geometry) -> Vec<String> {
    let mut errors = Vec::new();
    match geom {
        Geometry::Polygon(p) => validate_polygon(p, "polygon", &mut errors),
        Geometry::MultiPolygon(polys) => {
            if polys.is_empty() {
                errors.push("multipolygon has no polygons".to_string());
            }
            for (i, p) in polys.iter().enumerate() {
                validate_polygon(p, &format!("polygon {i}"), &mut errors);
            }
        }
    }
    errors
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_simple_polygon() {
        let g = parse_wkt("POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))").unwrap();
        match g {
            Geometry::Polygon(p) => {
                assert_eq!(p.rings.len(), 1);
                assert_eq!(p.rings[0].len(), 5);
            }
            _ => panic!("expected polygon"),
        }
    }

    #[test]
    fn parses_polygon_with_hole_and_lowercase_keyword() {
        let g = parse_wkt("polygon ((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 4 2, 4 4, 2 4, 2 2))")
            .unwrap();
        match g {
            Geometry::Polygon(p) => assert_eq!(p.rings.len(), 2),
            _ => panic!("expected polygon"),
        }
    }

    #[test]
    fn parses_multipolygon() {
        let g = parse_wkt(
            "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 1, 0 0)), ((2 2, 3 2, 3 3, 2 3, 2 2)))",
        )
        .unwrap();
        match g {
            Geometry::MultiPolygon(polys) => assert_eq!(polys.len(), 2),
            _ => panic!("expected multipolygon"),
        }
    }

    #[test]
    fn parses_signed_and_decimal_numbers() {
        let g = parse_wkt("POLYGON ((-1.5 -2.25, 3.5 -2.25, 3.5 2.75, -1.5 2.75, -1.5 -2.25))")
            .unwrap();
        assert_eq!(validate(&g), Vec::<String>::new());
    }

    #[test]
    fn rejects_non_polygon_input() {
        assert!(parse_wkt("POINT (1 2)").is_err());
        assert!(parse_wkt("LINESTRING (0 0, 1 1)").is_err());
    }

    #[test]
    fn rejects_trailing_input() {
        assert!(parse_wkt("POLYGON ((0 0, 1 0, 1 1, 0 0)) junk").is_err());
    }

    #[test]
    fn shoelace_area_is_correct() {
        let g = parse_wkt("POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))").unwrap();
        assert!((geometry_area(&g) - 16.0).abs() < 1e-12);
    }

    #[test]
    fn area_subtracts_holes() {
        let g = parse_wkt(
            "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 4 2, 4 4, 2 4, 2 2))",
        )
        .unwrap();
        assert!((geometry_area(&g) - 96.0).abs() < 1e-12);
    }

    #[test]
    fn multipolygon_area_sums_members() {
        let g = parse_wkt(
            "MULTIPOLYGON (((0 0, 2 0, 2 2, 0 2, 0 0)), ((4 4, 5 4, 5 5, 4 5, 4 4)))",
        )
        .unwrap();
        assert!((geometry_area(&g) - 5.0).abs() < 1e-12);
    }

    #[test]
    fn detects_unclosed_ring() {
        let g = parse_wkt("POLYGON ((0 0, 4 0, 4 4, 0 4))").unwrap();
        let errors = validate(&g);
        assert!(errors.iter().any(|e| e.contains("not closed")), "{errors:?}");
    }

    #[test]
    fn detects_too_few_points() {
        let g = parse_wkt("POLYGON ((0 0, 1 1, 0 0))").unwrap();
        let errors = validate(&g);
        assert!(
            errors.iter().any(|e| e.contains("minimum")),
            "{errors:?}"
        );
    }

    #[test]
    fn detects_out_of_range_coordinates() {
        let g = parse_wkt("POLYGON ((0 0, 200 0, 200 4, 0 4, 0 0))").unwrap();
        let errors = validate(&g);
        assert!(
            errors.iter().any(|e| e.contains("out of range")),
            "{errors:?}"
        );
        let g = parse_wkt("POLYGON ((0 0, 4 0, 4 -95, 0 -95, 0 0))").unwrap();
        let errors = validate(&g);
        assert!(
            errors.iter().any(|e| e.contains("latitude")),
            "{errors:?}"
        );
    }

    #[test]
    fn detects_zero_area() {
        let g = parse_wkt("POLYGON ((0 0, 1 1, 2 2, 0 0))").unwrap();
        let errors = validate(&g);
        assert!(
            errors.iter().any(|e| e.contains("zero area")),
            "{errors:?}"
        );
    }

    #[test]
    fn detects_duplicate_consecutive_vertices() {
        let g = parse_wkt("POLYGON ((0 0, 4 0, 4 0, 4 4, 0 4, 0 0))").unwrap();
        let errors = validate(&g);
        assert!(
            errors
                .iter()
                .any(|e| e.contains("duplicate consecutive")),
            "{errors:?}"
        );
    }

    #[test]
    fn valid_geometry_has_no_errors() {
        let g = parse_wkt("POLYGON ((3 7, 9 7, 9 12, 3 12, 3 7))").unwrap();
        assert!(validate(&g).is_empty());
    }

    #[test]
    fn bbox_is_computed() {
        let g = parse_wkt(
            "MULTIPOLYGON (((0 0, 2 0, 2 2, 0 2, 0 0)), ((-3 1, -1 1, -1 5, -3 5, -3 1)))",
        )
        .unwrap();
        let b = Bbox::from_geometry(&g).unwrap();
        assert_eq!(
            b,
            Bbox {
                min_x: -3.0,
                min_y: 0.0,
                max_x: 2.0,
                max_y: 5.0
            }
        );
    }

    #[test]
    fn bbox_intersection_logic() {
        let a = Bbox {
            min_x: 0.0,
            min_y: 0.0,
            max_x: 2.0,
            max_y: 2.0,
        };
        let b = Bbox {
            min_x: 1.0,
            min_y: 1.0,
            max_x: 3.0,
            max_y: 3.0,
        };
        let c = Bbox {
            min_x: 2.0,
            min_y: 2.0,
            max_x: 4.0,
            max_y: 4.0,
        };
        let d = Bbox {
            min_x: 5.0,
            min_y: 5.0,
            max_x: 6.0,
            max_y: 6.0,
        };
        assert!(bbox_intersects(&a, &b));
        assert!(bbox_intersects(&b, &a));
        assert!(bbox_intersects(&a, &c)); // touching edges count
        assert!(!bbox_intersects(&a, &d));
    }
}
