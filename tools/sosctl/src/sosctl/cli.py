"""sosctl CLI — command surface per tools/README.md (WP-01 / EP-CP-01)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import audit, gitops, ledger, policy
from .registry import TenantRegistry
from .states import STATE_TENANT_IDS, TIERS

app = typer.Typer(
    name="sosctl",
    help="State Operating System platform operator CLI.",
    no_args_is_help=True,
)
tenant_app = typer.Typer(help="Tenant lifecycle management.", no_args_is_help=True)
policy_app = typer.Typer(help="Dynamic policy-pack management.", no_args_is_help=True)
ledger_app = typer.Typer(help="TigerBeetle ledger bootstrap.", no_args_is_help=True)
audit_app = typer.Typer(help="Immutable audit archive operations.", no_args_is_help=True)
app.add_typer(tenant_app, name="tenant")
app.add_typer(policy_app, name="policy")
app.add_typer(ledger_app, name="ledger")
app.add_typer(audit_app, name="audit")

console = Console()
err_console = Console(stderr=True)

StateOpt = typer.Option(..., "--state", help="State tenant id (lagos|ogun|osun|benue|nasarawa|taraba)")


def _check_state(state: str) -> str:
    state = state.lower()
    if state not in STATE_TENANT_IDS:
        err_console.print(
            f"[red]unknown state '{state}'[/red]; valid: {', '.join(sorted(STATE_TENANT_IDS))}"
        )
        raise typer.Exit(code=2)
    return state


@tenant_app.command("create")
def tenant_create(
    state: str = StateOpt,
    tier: str = typer.Option(..., "--tier", help="Tenant tier: shared|hybrid|dedicated"),
    out_dir: Path = typer.Option(None, "--out-dir", help="GitOps output root (default ./out/gitops/{state}/)"),
    registry: Optional[Path] = typer.Option(None, "--registry", help="Tenant registry JSON path"),
) -> None:
    """Generate the tenant GitOps bundle and register the tenant."""
    state = _check_state(state)
    if tier not in TIERS:
        err_console.print(f"[red]unknown tier '{tier}'[/red]; valid: {', '.join(TIERS)}")
        raise typer.Exit(code=2)
    target = Path(out_dir) if out_dir else Path("out/gitops") / state
    bundle = gitops.write_bundle(state, tier, target)
    record = TenantRegistry(registry).create(state, tier, bundle.manifest)

    console.print(f"[green]Tenant '{state}' provisioned ({tier})[/green] → {target}")
    table = Table(title="GitOps manifest summary")
    table.add_column("Resource")
    table.add_column("Value")
    for key, value in bundle.manifest.items():
        table.add_row(key, value)
    console.print(table)
    for rel in sorted(bundle.files):
        console.print(f"  • {target / rel}")
    console.print(f"[dim]registry: {TenantRegistry(registry).path} (status={record['status']})[/dim]")


@tenant_app.command("list")
def tenant_list(
    registry: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """List registered tenants (local registry backend)."""
    tenants = TenantRegistry(registry).list()
    if not tenants:
        console.print("[yellow]No tenants registered.[/yellow]")
        return
    table = Table(title="SOS tenants (local registry — production backend: control-plane API)")
    for col in ("tenant_id", "state", "tier", "status", "created_at"):
        table.add_column(col)
    for t in tenants:
        table.add_row(t["tenant_id"], t["state"], t["tier"], t["status"], t["created_at"])
    console.print(table)


@tenant_app.command("status")
def tenant_status(
    state: str = StateOpt,
    registry: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Show a single tenant's registry record."""
    state = _check_state(state)
    record = TenantRegistry(registry).get(state)
    if record is None:
        err_console.print(f"[red]tenant '{state}' not found[/red]")
        raise typer.Exit(code=1)
    table = Table(title=f"Tenant {record['tenant_id']}")
    table.add_column("Field")
    table.add_column("Value")
    for key in ("tenant_id", "state", "tier", "status", "created_at", "suspended_at", "suspend_reason"):
        table.add_row(key, str(record.get(key)))
    for res, val in record.get("provisioned_resources", {}).items():
        table.add_row(f"resource.{res}", str(val))
    console.print(table)


@tenant_app.command("suspend")
def tenant_suspend(
    state: str = StateOpt,
    reason: str = typer.Option(..., "--reason", help="Suspension reason (audited)"),
    registry: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Suspend a tenant (e.g. concession breach). Audited to the registry."""
    state = _check_state(state)
    try:
        record = TenantRegistry(registry).suspend(state, reason)
    except KeyError as exc:
        err_console.print(f"[red]{exc.args[0]}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]Tenant '{state}' suspended[/yellow]: {record['suspend_reason']}")


@policy_app.command("validate")
def policy_validate(
    file: Path = typer.Option(..., "--file", help="Policy pack JSON"),
    schema: Path = typer.Option(policy.DEFAULT_SCHEMA_PATH, "--schema"),
) -> None:
    """Validate a policy pack (JSON Schema + SOS guardrails). Nonzero exit on failure."""
    result = policy.validate_policy_file(file, schema)
    if result.ok:
        console.print(f"[green]OK[/green] {file} conforms to {schema} and SOS guardrails")
        return
    err_console.print(f"[red]Policy pack invalid:[/red] {file}")
    for err in result.errors:
        err_console.print(f"  ✗ {err}")
    raise typer.Exit(code=1)


@policy_app.command("apply")
def policy_apply(
    state: str = StateOpt,
    file: Path = typer.Option(..., "--file"),
    schema: Path = typer.Option(policy.DEFAULT_SCHEMA_PATH, "--schema"),
    out_dir: Path = typer.Option(Path("out/config/states"), "--out-dir"),
) -> None:
    """Validate then stage a policy pack into the config-style output dir."""
    state = _check_state(state)
    result = policy.validate_policy_file(file, schema)
    if not result.ok:
        err_console.print(f"[red]Policy pack invalid; not staged:[/red] {file}")
        for err in result.errors:
            err_console.print(f"  ✗ {err}")
        raise typer.Exit(code=1)
    doc_state = result.document.get("tenant_state_id")
    if doc_state != state:
        err_console.print(
            f"[red]state mismatch:[/red] --state={state} but pack tenant_state_id={doc_state}"
        )
        raise typer.Exit(code=1)
    dest = policy.stage_policy(state, result.document, out_dir)
    console.print(f"[green]Policy pack staged[/green] → {dest}")


@ledger_app.command("init-chart")
def ledger_init_chart(
    state: str = StateOpt,
    gazette_ref: str = typer.Option(..., "--gazette-ref", help="Gazetted statutory instrument ref"),
    out_dir: Path = typer.Option(Path("out/ledger"), "--out-dir"),
) -> None:
    """Emit the chart-of-accounts bootstrap JSON mapped to the state's CRF."""
    state = _check_state(state)
    try:
        dest = ledger.write_chart(state, gazette_ref, out_dir)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    console.print(f"[green]Chart of accounts initialized[/green] → {dest}")
    chart = ledger.build_chart(state, gazette_ref)
    table = Table(title=f"{state.title()} CRF bootstrap (gazette: {chart['gazette_reference']})")
    table.add_column("Code")
    table.add_column("Account")
    for acct in chart["accounts"]:
        table.add_row(str(acct["code"]), acct["name"])
    console.print(table)


@ledger_app.command("init")
def ledger_init(
    state: str = StateOpt,
    apply: bool = typer.Option(False, "--apply", help="Actually create accounts (default: dry-run)"),
    addresses: Optional[str] = typer.Option(
        None, "--addresses", help="Comma-separated TigerBeetle host:port list (default $TB_ADDRESSES)"
    ),
    cluster_id: int = typer.Option(1, "--cluster-id", help="TigerBeetle cluster ID (default $TB_CLUSTER_ID or 1)"),
) -> None:
    """Idempotently create the state's chart-of-accounts accounts.

    DRY-RUN by default: prints the deterministic account plan without
    touching any cluster. With --apply, connects to the cluster
    (fail-closed: requires --addresses/$TB_ADDRESSES) and creates the
    accounts; already-existing accounts are treated as success.
    """
    import os

    state = _check_state(state)
    raw_addresses = addresses if addresses is not None else os.environ.get("TB_ADDRESSES", "")
    address_list = [a.strip() for a in raw_addresses.split(",") if a.strip()]
    env_cluster = os.environ.get("TB_CLUSTER_ID")
    if cluster_id == 1 and env_cluster:
        cluster_id = int(env_cluster)

    plan = ledger.build_account_plan(state)
    table = Table(title=f"{state.title()} chart-of-accounts account plan (ledger 1)")
    for col in ("Code", "Account", "128-bit account id"):
        table.add_column(col)
    for acct in plan:
        table.add_row(str(acct["code"]), acct["name"], str(acct["account_id"]))
    console.print(table)

    if not apply:
        console.print(
            f"[yellow]dry-run:[/yellow] {len(plan)} account(s) would be created; "
            "re-run with --apply to provision."
        )
        return

    if not address_list:
        err_console.print(
            "[red]--apply requires TB_ADDRESSES or --addresses[/red] (fail-closed)"
        )
        raise typer.Exit(code=2)
    try:
        summary = ledger.provision_accounts(state, address_list, cluster_id=cluster_id)
    except (RuntimeError, ValueError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(
        f"[green]Accounts provisioned[/green]: {summary['accounts_created']} created, "
        f"{summary['accounts_already_existing']} already existed "
        f"(cluster {summary['cluster_id']} @ {', '.join(summary['addresses'])})"
    )


@audit_app.command("verify-chain")
def audit_verify_chain(
    tenant: str = typer.Argument(..., help="Tenant id whose audit chain to verify"),
    archive_root: Path = typer.Option(
        "out/audit", "--archive-root", help="LocalFileArchive root (JSONL, one file per tenant)"
    ),
) -> None:
    """Recompute the tenant's audit hash chain from the immutable archive.

    Exits 0 when the chain is intact; exits 1 when any event was mutated,
    deleted, or re-ordered (tamper detection).
    """
    try:
        events = audit.load_tenant_chain(archive_root, tenant)
    except FileNotFoundError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    errors = audit.verify_event_chain(events)
    if errors:
        err_console.print(
            f"[red]AUDIT CHAIN COMPROMISED[/red] tenant={tenant} "
            f"events={len(events)} errors={len(errors)}"
        )
        for error in errors:
            err_console.print(f"  [red]• {error}[/red]")
        raise typer.Exit(code=1)
    console.print(
        f"[green]audit chain intact[/green] tenant={tenant} events={len(events)} "
        f"(genesis → {events[-1]['event_hash'] if events else 'n/a'})"
    )


if __name__ == "__main__":
    app()
