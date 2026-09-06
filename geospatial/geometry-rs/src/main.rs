//! CLI for the geometry validator.
//!
//! Reads WKT from `--wkt <WKT>` or stdin and emits a JSON report with
//! `valid`, `errors`, `area`, and `bbox` — no external crates.

use std::io::Read;
use std::process::ExitCode;

use geometry_rs::{geometry_area, parse_wkt, validate, Bbox, Geometry};

fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

fn json_string_array(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|e| format!("\"{}\"", json_escape(e))).collect();
    format!("[{}]", inner.join(","))
}

fn format_f64(v: f64) -> String {
    if v.is_finite() {
        let s = format!("{v}");
        if s.contains('.') || s.contains('e') || s.contains('E') || s.contains("inf") {
            s
        } else {
            format!("{s}.0")
        }
    } else {
        "null".to_string()
    }
}

fn bbox_json(b: &Bbox) -> String {
    format!(
        "[{},{},{},{}]",
        format_f64(b.min_x),
        format_f64(b.min_y),
        format_f64(b.max_x),
        format_f64(b.max_y)
    )
}

fn report(valid: bool, errors: &[String], area: Option<f64>, bbox: Option<Bbox>) -> String {
    let area_json = area.map_or_else(|| "null".to_string(), format_f64);
    let bbox_json_s = bbox.map_or_else(|| "null".to_string(), |b| bbox_json(&b));
    format!(
        "{{\"valid\":{},\"errors\":{},\"area\":{},\"bbox\":{}}}",
        if valid { "true" } else { "false" },
        json_string_array(errors),
        area_json,
        bbox_json_s
    )
}

fn read_input(args: &[String]) -> Result<String, String> {
    let mut wkt: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--wkt" => {
                let value = args
                    .get(i + 1)
                    .ok_or_else(|| "--wkt requires a value".to_string())?;
                wkt = Some(value.clone());
                i += 2;
            }
            flag if flag.starts_with("--wkt=") => {
                wkt = Some(flag["--wkt=".len()..].to_string());
                i += 1;
            }
            "-h" | "--help" => {
                println!(
                    "Usage: geometry-rs [--wkt '<WKT>']\n\
                     Reads WKT POLYGON/MULTIPOLYGON from --wkt or stdin and prints a JSON\n\
                     validation report with keys: valid, errors, area, bbox."
                );
                std::process::exit(0);
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    if let Some(w) = wkt {
        return Ok(w);
    }
    let mut buf = String::new();
    std::io::stdin()
        .read_to_string(&mut buf)
        .map_err(|e| format!("failed to read stdin: {e}"))?;
    Ok(buf.trim().to_string())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let input = match read_input(&args) {
        Ok(s) => s,
        Err(e) => {
            let errors = vec![e.clone()];
            println!("{}", report(false, &errors, None, None));
            eprintln!("error: {e}");
            return ExitCode::from(2);
        }
    };
    if input.is_empty() {
        let errors = vec!["no WKT input provided (use --wkt or stdin)".to_string()];
        println!("{}", report(false, &errors, None, None));
        return ExitCode::from(2);
    }
    let geom: Geometry = match parse_wkt(&input) {
        Ok(g) => g,
        Err(e) => {
            let errors = vec![format!("parse error: {e}")];
            println!("{}", report(false, &errors, None, None));
            return ExitCode::from(1);
        }
    };
    let errors = validate(&geom);
    let area = geometry_area(&geom);
    let bbox = Bbox::from_geometry(&geom);
    let valid = errors.is_empty();
    println!("{}", report(valid, &errors, Some(area), bbox));
    if valid {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}
