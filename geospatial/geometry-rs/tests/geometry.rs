//! Integration tests for the geometry-rs library: WKT parsing, validation,
//! area, and bounding boxes.

use geometry_rs::{
    bbox_intersects, geometry_area, parse_wkt, validate, Bbox, Geometry,
};

const VALID_PARCEL: &str = "POLYGON ((3.34 7.12, 3.36 7.12, 3.36 7.14, 3.34 7.14, 3.34 7.12))";

#[test]
fn parses_and_validates_realistic_parcel_polygon() {
    let geom = parse_wkt(VALID_PARCEL).expect("should parse");
    assert!(matches!(geom, Geometry::Polygon(_)));
    assert!(validate(&geom).is_empty());
    let area = geometry_area(&geom);
    assert!((area - 0.0004).abs() < 1e-12, "area = {area}");
    let bbox = Bbox::from_geometry(&geom).expect("bbox");
    assert_eq!(
        bbox,
        Bbox {
            min_x: 3.34,
            min_y: 7.12,
            max_x: 3.36,
            max_y: 7.14
        }
    );
}

#[test]
fn parses_multipolygon_with_multiple_members_and_holes() {
    let wkt = "MULTIPOLYGON (
        ((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 4 2, 4 4, 2 4, 2 2)),
        ((20 20, 22 20, 22 22, 20 22, 20 20))
    )";
    let geom = parse_wkt(wkt).expect("should parse");
    match &geom {
        Geometry::MultiPolygon(polys) => {
            assert_eq!(polys.len(), 2);
            assert_eq!(polys[0].rings.len(), 2);
            assert_eq!(polys[1].rings.len(), 1);
        }
        _ => panic!("expected multipolygon"),
    }
    assert!(validate(&geom).is_empty());
    // 100 - 4 + 4 = 100
    assert!((geometry_area(&geom) - 100.0).abs() < 1e-9);
}

#[test]
fn rejects_unsupported_geometry_types() {
    for wkt in [
        "POINT (1 2)",
        "LINESTRING (0 0, 1 1)",
        "GEOMETRYCOLLECTION (POINT (1 2))",
        "CIRCLE (0 0, 1)",
    ] {
        assert!(parse_wkt(wkt).is_err(), "expected error for {wkt}");
    }
}

#[test]
fn rejects_malformed_wkt() {
    for wkt in [
        "",
        "POLYGON",
        "POLYGON (",
        "POLYGON ((0 0, 1 1, 0 0)",
        "POLYGON ((0 0, a b, 0 0))",
        "POLYGON ((0 0, 1 1, 0 0)) extra",
        "MULTIPOLYGON ()",
    ] {
        assert!(parse_wkt(wkt).is_err(), "expected error for {wkt:?}");
    }
}

#[test]
fn rejects_empty_polygon_keyword_forms() {
    assert!(parse_wkt("POLYGON EMPTY").is_err());
    assert!(parse_wkt("MULTIPOLYGON EMPTY").is_err());
}

#[test]
fn flags_unclosed_ring() {
    let geom = parse_wkt("POLYGON ((0 0, 4 0, 4 4, 0 4))").unwrap();
    let errors = validate(&geom);
    assert!(errors.iter().any(|e| e.contains("not closed")), "{errors:?}");
}

#[test]
fn flags_ring_with_too_few_points() {
    let geom = parse_wkt("POLYGON ((0 0, 1 0, 0 0))").unwrap();
    let errors = validate(&geom);
    assert!(errors.iter().any(|e| e.contains("minimum")), "{errors:?}");
}

#[test]
fn flags_out_of_range_longitude_and_latitude() {
    let geom = parse_wkt("POLYGON ((0 0, 181 0, 181 4, 0 4, 0 0))").unwrap();
    assert!(validate(&geom).iter().any(|e| e.contains("longitude")));
    let geom = parse_wkt("POLYGON ((0 0, 4 0, 4 90.5, 0 90.5, 0 0))").unwrap();
    assert!(validate(&geom).iter().any(|e| e.contains("latitude")));
    // Boundary values are allowed.
    let geom = parse_wkt("POLYGON ((-180 -90, 180 -90, 180 90, -180 90, -180 -90))").unwrap();
    assert!(validate(&geom).is_empty());
}

#[test]
fn flags_zero_area_degenerate_polygon() {
    let geom = parse_wkt("POLYGON ((1 1, 2 2, 3 3, 1 1))").unwrap();
    assert!(validate(&geom).iter().any(|e| e.contains("zero area")));
}

#[test]
fn flags_duplicate_consecutive_vertices() {
    let geom = parse_wkt("POLYGON ((0 0, 4 0, 4 0, 4 4, 0 4, 0 0))").unwrap();
    let errors = validate(&geom);
    assert!(
        errors.iter().any(|e| e.contains("duplicate consecutive")),
        "{errors:?}"
    );
}

#[test]
fn collects_multiple_errors_at_once() {
    // Unclosed AND too few points.
    let geom = parse_wkt("POLYGON ((0 0, 400 0, 1 1))").unwrap();
    let errors = validate(&geom);
    assert!(errors.len() >= 3, "{errors:?}");
    assert!(errors.iter().any(|e| e.contains("minimum")));
    assert!(errors.iter().any(|e| e.contains("not closed")));
    assert!(errors.iter().any(|e| e.contains("out of range")));
}

#[test]
fn bbox_intersects_covers_overlap_touch_and_disjoint() {
    let a = Bbox {
        min_x: 0.0,
        min_y: 0.0,
        max_x: 4.0,
        max_y: 4.0,
    };
    let overlapping = Bbox {
        min_x: 2.0,
        min_y: 2.0,
        max_x: 6.0,
        max_y: 6.0,
    };
    let touching = Bbox {
        min_x: 4.0,
        min_y: 0.0,
        max_x: 8.0,
        max_y: 4.0,
    };
    let disjoint = Bbox {
        min_x: 4.0001,
        min_y: 0.0,
        max_x: 8.0,
        max_y: 4.0,
    };
    assert!(bbox_intersects(&a, &a));
    assert!(bbox_intersects(&a, &overlapping));
    assert!(bbox_intersects(&overlapping, &a));
    assert!(bbox_intersects(&a, &touching));
    assert!(!bbox_intersects(&a, &disjoint));
    assert!(!bbox_intersects(&disjoint, &a));
}

#[test]
fn bbox_of_multipolygon_covers_all_members() {
    let geom = parse_wkt(
        "MULTIPOLYGON (((-10 -5, -8 -5, -8 -3, -10 -3, -10 -5)), ((5 2, 9 2, 9 8, 5 8, 5 2)))",
    )
    .unwrap();
    let bbox = Bbox::from_geometry(&geom).unwrap();
    assert_eq!(
        bbox,
        Bbox {
            min_x: -10.0,
            min_y: -5.0,
            max_x: 9.0,
            max_y: 8.0
        }
    );
}

#[test]
fn handles_extreme_but_valid_coordinates() {
    let geom = parse_wkt(
        "POLYGON ((179.999 -89.999, 180 -89.999, 180 -89.998, 179.999 -89.998, 179.999 -89.999))",
    )
    .unwrap();
    assert!(validate(&geom).is_empty());
    assert!(geometry_area(&geom) > 0.0);
}

#[test]
fn handles_scientific_notation_coordinates() {
    let geom = parse_wkt("POLYGON ((0 0, 1e-3 0, 1e-3 1E-3, 0 1e-3, 0 0))").unwrap();
    assert!(validate(&geom).is_empty());
    assert!((geometry_area(&geom) - 1e-6).abs() < 1e-15);
}
