from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from akad.contract_loader import load_contract
from akad.models.contract import DataContract
from akad.models.result import OverallStatus, ValidationResult
from akad.registry_client import TOKEN_ENV, RegistryClient, auth_headers

# Force UTF-8 output so the ✓/✗ icons below don't crash on a non-UTF-8
# console (e.g. the cp1252 default on many Windows setups) — without this,
# typer.echo() raises UnicodeEncodeError instead of printing anything.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(name="akad", help="Akad — Data Contract Framework CLI", no_args_is_help=True)


@app.command()
def validate(
    contract:     Path = typer.Option(..., "--contract", "-c", help="Path to contract YAML"),
    registry_url: str | None = typer.Option(None, "--registry-url", "-r", help="Registry URL"),
    output:       str  = typer.Option("text", "--output", "-o", help="Output format: text|json"),
    token:        str | None = typer.Option(None, "--token", envvar=TOKEN_ENV, help=f"Registry API token (default: ${TOKEN_ENV})"),
) -> None:
    """Validate a dataset against its contract.

    Exit codes: 0 compliant, 1 breach, 2 contract or dataset could not be evaluated.
    """
    from akad.sdk import DataContractError, DataContractValidator

    try:
        validator = DataContractValidator(
            contract_path=contract,
            registry_url=registry_url,
            registry_token=token,
            notifiers=[],  # CLI never sends notifications
        )
        result = validator.validate()
    except DataContractError as exc:
        result = exc.result  # on_breach: fail — exit code below still reflects the status
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    _print_result(result, output)
    if result.is_breach:
        raise typer.Exit(code=1)
    if result.overall_status == OverallStatus.ERROR:
        raise typer.Exit(code=2)


@app.command()
def publish(
    contract:     Path = typer.Option(..., "--contract", "-c", help="Path to contract YAML"),
    registry_url: str  = typer.Option(..., "--registry-url", "-r", help="Registry URL"),
    token:        str | None = typer.Option(None, "--token", envvar=TOKEN_ENV, help=f"Registry API token (default: ${TOKEN_ENV})"),
) -> None:
    """Publish a contract to the registry. Exits 1 if the registry can't be
    reached or rejects the contract."""
    import httpx

    c = load_contract(contract)
    try:
        created = RegistryClient(registry_url, api_token=token).publish_contract(c)
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f"registry returned {exc.response.status_code}: {exc.response.text}"
        typer.echo(f"Error: could not publish {c.metadata.name} v{c.metadata.version} — {detail}", err=True)
        raise typer.Exit(code=1) from exc
    if created:
        typer.echo(f"Published {c.metadata.name} v{c.metadata.version}")
    else:
        typer.echo(f"Already published {c.metadata.name} v{c.metadata.version} (identical content, nothing changed)")


@app.command()
def check(
    contract: Path = typer.Option(..., "--contract", "-c", help="Path to contract YAML"),
) -> None:
    """Validate contract YAML syntax without accessing data."""
    try:
        c = load_contract(contract)
        typer.echo(f"OK  {c.metadata.name} v{c.metadata.version} — contract is valid")
    except Exception as exc:
        typer.echo(f"FAIL  {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command(name="list")
def list_contracts(
    registry_url: str = typer.Option(..., "--registry-url", "-r", help="Registry URL"),
    token:        str | None = typer.Option(None, "--token", envvar=TOKEN_ENV, help=f"Registry API token (default: ${TOKEN_ENV})"),
) -> None:
    """List all contracts in the registry."""
    import httpx
    try:
        data = httpx.get(f"{registry_url.rstrip('/')}/contracts/", headers=auth_headers(token), timeout=10).json()
        for c in data:
            typer.echo(f"  {c['name']:40s}  v{c['version']}")
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def infer(
    name:              str = typer.Option(..., "--name", "-n", help="Contract name"),
    dataset_format:    str = typer.Option("parquet", "--format", "-f", help="Dataset format: parquet|sql"),
    location:          str | None = typer.Option(None, "--location", help="Parquet path (format=parquet)"),
    connection_string: str | None = typer.Option(None, "--connection-string", help="DB connection string (format=sql)"),
    table_name:        str | None = typer.Option(None, "--table-name", help="Table name (format=sql)"),
    owner_team:        str = typer.Option("TODO", "--owner-team", help="Owner team — fill in before publishing"),
    owner_email:       str = typer.Option("TODO@example.com", "--owner-email", help="Owner email — fill in before publishing"),
    output:            Path | None = typer.Option(None, "--output", "-o", help="Write YAML to this path instead of stdout"),
) -> None:
    """Profile a dataset and scaffold a starter contract YAML.

    This is a STARTING POINT, not a finished contract — every inferred rule
    reflects only what today's data looks like. Review and tighten it
    (especially allowed_values and volume bounds) before relying on it.
    """
    import yaml

    from akad.models.contract import DatasetSpec
    from akad.profiler import contract_to_yaml_dict, generate_contract
    from akad.readers.parquet_reader import ParquetReader
    from akad.readers.sql_reader import SQLReader

    if dataset_format == "parquet":
        if not location:
            typer.echo("Error: --location is required for --format parquet", err=True)
            raise typer.Exit(code=2)
        df = ParquetReader().read(DatasetSpec(format="parquet", location=location))
    elif dataset_format == "sql":
        if not connection_string or not table_name:
            typer.echo("Error: --connection-string and --table-name are required for --format sql", err=True)
            raise typer.Exit(code=2)
        df = SQLReader().read(DatasetSpec(
            format="sql", connection_string=connection_string, table_name=table_name,
        ))
    else:
        typer.echo(f"Error: unsupported format '{dataset_format}' (use parquet or sql)", err=True)
        raise typer.Exit(code=2)

    contract = generate_contract(
        df,
        name=name,
        dataset_format=dataset_format,
        owner_team=owner_team,
        owner_email=owner_email,
        location=location,
        table_name=table_name,
        connection_string=connection_string,
    )

    header = (
        f"# Auto-generated by `akad infer` from {len(df)} row(s) — REVIEW BEFORE USE.\n"
        f"# Every rule below reflects only what this sample looked like; tighten\n"
        f"# allowed_values, volume bounds, and on_breach before relying on this.\n"
    )
    body = yaml.dump(contract_to_yaml_dict(contract), sort_keys=False, default_flow_style=False)
    text = header + body

    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"Wrote starter contract to {output}")
    else:
        typer.echo(text)


def _load_diff_contracts(
    old_contract: Path | None,
    new_contract: Path | None,
    name: str | None,
    old_version: str | None,
    new_version: str | None,
    registry_url: str | None,
    token: str | None = None,
) -> tuple[DataContract, DataContract]:
    """Resolve `akad diff`'s two loading modes into a pair of contracts."""
    if name:
        if not (old_version and new_version and registry_url):
            typer.echo("Error: --name requires --old-version, --new-version, and --registry-url", err=True)
            raise typer.Exit(code=2)
    elif not (old_contract and new_contract):
        typer.echo(
            "Error: provide --old/--new file paths, or --name with "
            "--old-version/--new-version/--registry-url",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        if name:
            if not (old_version and new_version and registry_url):
                raise AssertionError("unreachable — checked above")
            client = RegistryClient(registry_url, api_token=token)
            return client.get_contract_version(name, old_version), client.get_contract_version(name, new_version)
        if not (old_contract and new_contract):
            raise AssertionError("unreachable — checked above")
        return load_contract(old_contract), load_contract(new_contract)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def diff(
    old_contract: Path | None = typer.Option(None, "--old", help="Old contract YAML path"),
    new_contract: Path | None = typer.Option(None, "--new", help="New contract YAML path"),
    name:         str | None = typer.Option(None, "--name", "-n", help="Contract name (compare two registry versions instead of files)"),
    old_version:  str | None = typer.Option(None, "--old-version", help="Old version (with --name)"),
    new_version:  str | None = typer.Option(None, "--new-version", help="New version (with --name)"),
    registry_url: str | None = typer.Option(None, "--registry-url", "-r", help="Registry URL (with --name)"),
    output:       str = typer.Option("text", "--output", "-o", help="Output format: text|json"),
    token:        str | None = typer.Option(None, "--token", envvar=TOKEN_ENV, help=f"Registry API token (default: ${TOKEN_ENV})"),
) -> None:
    """Compare two contract versions and flag breaking vs non-breaking changes.

    Either pass two local files (--old/--new), or compare two versions
    already published to the registry (--name with --old-version,
    --new-version, and --registry-url).
    """
    from akad.differ import DiffSeverity, diff_contracts

    old, new = _load_diff_contracts(old_contract, new_contract, name, old_version, new_version, registry_url, token)
    entries = diff_contracts(old, new)
    breaking = [e for e in entries if e.severity == DiffSeverity.BREAKING]

    if output == "json":
        typer.echo(json.dumps([
            {"severity": e.severity.value, "path": e.path, "message": e.message}
            for e in entries
        ], indent=2))
    else:
        if not entries:
            typer.echo("No differences detected.")
        for e in entries:
            icon = "✗" if e.severity == DiffSeverity.BREAKING else "✓"
            typer.echo(f"  {icon} {e.severity.value:12s} {e.path}: {e.message}")
        if entries:
            typer.echo(f"\n{len(breaking)} breaking, {len(entries) - len(breaking)} non-breaking change(s).")

    if breaking:
        raise typer.Exit(code=1)


@app.command()
def history(
    name:         str = typer.Option(..., "--name", "-n", help="Contract name"),
    registry_url: str = typer.Option(..., "--registry-url", "-r", help="Registry URL"),
    limit:        int = typer.Option(20, "--limit", "-l", help="Number of results"),
    token:        str | None = typer.Option(None, "--token", envvar=TOKEN_ENV, help=f"Registry API token (default: ${TOKEN_ENV})"),
) -> None:
    """Show breach history for a contract."""
    import httpx
    try:
        url  = f"{registry_url.rstrip('/')}/validation-results/?contract_name={name}&limit={limit}"
        data = httpx.get(url, headers=auth_headers(token), timeout=10).json()
        for r in data:
            icon = "✓" if r["overall_status"] == "COMPLIANT" else "✗"
            typer.echo(f"  {icon} {r['validated_at']}  {r['overall_status']}")
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _print_result(result: ValidationResult, output: str) -> None:
    if output == "json":
        typer.echo(json.dumps({
            "status":          result.overall_status.value,
            "row_count":       result.row_count,
            "error_message":   result.error_message,
            "failed_clauses":  [c.to_dict() for c in result.failed_clauses],
            "errored_clauses": [c.to_dict() for c in result.errored_clauses],
        }, indent=2))
    else:
        icon = "✓" if result.overall_status.value == "COMPLIANT" else "✗"
        typer.echo(f"{icon} {result.contract_name} v{result.contract_version}: {result.overall_status.value}")
        if result.error_message:
            typer.echo(f"Error: {result.error_message}")
        for heading, clauses in (("Failed clauses:", result.failed_clauses),
                                 ("Errored clauses:", result.errored_clauses)):
            if clauses:
                typer.echo(heading)
                for c in clauses:
                    target = f" [{c.clause_target}]" if c.clause_target else ""
                    typer.echo(f"  - [{c.clause_type}]{target} {c.message}")


if __name__ == "__main__":
    app()
